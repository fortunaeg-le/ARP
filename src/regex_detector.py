import re
import sys
import uuid

import yaml

from models import Entity, SourceDocument
from normalizer import detection_view, norm_to_src


def _strip_requisite_separators(value: str) -> str:
    """Убирает разделители-пробелы внутри реквизита перед проверкой чек-суммы.

    B2-fix: паттерны INN/OGRN теперь допускают внутри цифр одиночный обычный или
    неразрывный пробел (реквизиты в договорах группируют пробелами). m.group(0)
    приходит в валидатор «сырым», с этими пробелами, поэтому здесь их снимаем —
    формат Entity.original_text при этом НЕ меняется (пробелы там сохраняются как
    есть; нормализация — зона блока 4, а не детектора). Снимаем ровно те же два
    символа, что разрешены как разделители в паттерне: U+0020 и U+00A0."""
    return value.replace(" ", "").replace(" ", "")


def inn_checksum(value: str) -> bool:
    """Контрольное число ИНН по алгоритму ФНС (10 и 12 знаков)."""
    value = _strip_requisite_separators(value)
    if not value.isdigit():
        return False
    digits = [int(ch) for ch in value]
    if len(digits) == 10:
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        control = sum(w * d for w, d in zip(weights, digits)) % 11 % 10
        return control == digits[9]
    if len(digits) == 12:
        weights11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        weights12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        control11 = sum(w * d for w, d in zip(weights11, digits)) % 11 % 10
        control12 = sum(w * d for w, d in zip(weights12, digits)) % 11 % 10
        return control11 == digits[10] and control12 == digits[11]
    return False


def ogrn_checksum(value: str) -> bool:
    """Контрольное число ОГРН (13 знаков, mod 11) и ОГРНИП (15 знаков, mod 13)."""
    value = _strip_requisite_separators(value)
    if not value.isdigit():
        return False
    if len(value) == 13:
        return int(value[:12]) % 11 % 10 == int(value[12])
    if len(value) == 15:
        return int(value[:14]) % 13 % 10 == int(value[14])
    return False


VALIDATORS = {
    "inn_checksum": inn_checksum,
    "ogrn_checksum": ogrn_checksum,
}


def _load_regex_types(config_path: str) -> list[tuple[str, re.Pattern, object, int]]:
    """Читает entity_types.yaml и возвращает
    [(entity_type, compiled_pattern, validator|None, span_group)] для записей с
    method: regex и enabled != false. Типы с незнакомым validate или
    некомпилируемым/отсутствующим pattern пропускаются целиком с предупреждением в stderr.

    Два способа задать паттерны (этап 3):
      * `pattern:` — один паттерн, как раньше;
      * `patterns:` — СПИСОК записей {pattern, span_group?, validate?} для типов, у
        которых одна сущность корпуса пишется несколькими непохожими способами.
        Нужно для PASSPORT: «серия+номер» и «код подразделения» — один тип gold,
        но два разных якоря и две разных формы значения.

    `span_group: N` — номер группы, чей спан становится спаном сущности (по умолчанию
    0, т.е. весь матч). Ключевое для ЯКОРНЫХ детекторов (этап 3): якорь («СНИЛС»,
    «дата рождения», «код подразделения») обязан участвовать в ПОИСКЕ, но не должен
    попадать в МАСКИРУЕМЫЙ спан — иначе повторяется дефект PASSPORT 0c-B, где
    m.group(0) включает слово «паспорт» и спан шире эталонного (recall_exact 0%).
    Детектируем ПО якорю, маскируем БЕЗ якоря.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    result = []
    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or spec.get("method") != "regex":
            continue
        if spec.get("enabled", True) is False:
            continue

        if spec.get("patterns"):
            raw_entries = spec["patterns"]
        else:
            raw_entries = [{"pattern": spec.get("pattern"),
                            "validate": spec.get("validate"),
                            "span_group": spec.get("span_group", 0)}]

        for entry in raw_entries:
            pattern_str = entry.get("pattern")
            if not pattern_str:
                print(f"ПРЕДУПРЕЖДЕНИЕ: у типа {entity_type} (method: regex) нет поля "
                      f"pattern — паттерн пропущен", file=sys.stderr)
                continue
            try:
                pattern = re.compile(pattern_str)
            except re.error as exc:
                print(f"ПРЕДУПРЕЖДЕНИЕ: паттерн типа {entity_type} не компилируется "
                      f"({exc}) — паттерн пропущен", file=sys.stderr)
                continue

            span_group = entry.get("span_group", 0) or 0
            if span_group > pattern.groups:
                print(f"ПРЕДУПРЕЖДЕНИЕ: у типа {entity_type} span_group={span_group}, "
                      f"а групп в паттерне {pattern.groups} — паттерн пропущен",
                      file=sys.stderr)
                continue

            validator = None
            validate_name = entry.get("validate")
            if validate_name is not None:
                validator = VALIDATORS.get(validate_name)
                if validator is None:
                    print(f"ПРЕДУПРЕЖДЕНИЕ: неизвестный validate '{validate_name}' у типа "
                          f"{entity_type} — паттерн пропущен", file=sys.stderr)
                    continue

            result.append((entity_type, pattern, validator, span_group))
    return result


def detect_regex(doc: SourceDocument, config_path: str) -> list[Entity]:
    regex_types = _load_regex_types(config_path)

    entities: list[Entity] = []
    for segment in doc.segments:
        # Ищем в НОРМАЛИЗОВАННОЙ копии (омоглифы сведены, невидимые сняты,
        # разделители внутри числа схлопнуты — этап 2), а найденный спан
        # отображаем обратно в ИСХОДНЫЕ координаты через offset_map и вырезаем
        # original_text из НАСТОЯЩЕГО segment.text. m.group(0) — уже нормализованная
        # строка (чистые цифры), поэтому валидатор чек-суммы работает по ней прямо.
        # На неискажённом тексте нормализация — тождество, поведение прежнее.
        search_text, offset_map = detection_view(segment)
        for entity_type, pattern, validator, span_group in regex_types:
            for m in pattern.finditer(search_text):
                if m.start(span_group) < 0:
                    continue  # группа не участвовала в матче — спана нет
                if validator is not None and not validator(m.group(span_group)):
                    continue
                # span_group>0: якорь остаётся вне спана (см. _load_regex_types)
                start, end = norm_to_src(offset_map, m.start(span_group), m.end(span_group))
                entities.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=start,
                    end=end,
                    original_text=segment.text[start:end],
                    entity_type=entity_type,
                    detector="regex",
                    confidence=1.0,
                ))
    return entities
