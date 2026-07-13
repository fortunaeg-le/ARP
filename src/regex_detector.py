import re
import sys
import uuid

import yaml

from models import Entity, SourceDocument


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


def _load_regex_types(config_path: str) -> list[tuple[str, re.Pattern, object]]:
    """Читает entity_types.yaml и возвращает [(entity_type, compiled_pattern, validator|None)]
    для записей с method: regex и enabled != false. Типы с незнакомым validate
    или некомпилируемым/отсутствующим pattern пропускаются целиком с предупреждением в stderr."""
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    result = []
    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or spec.get("method") != "regex":
            continue
        if spec.get("enabled", True) is False:
            continue

        pattern_str = spec.get("pattern")
        if not pattern_str:
            print(f"ПРЕДУПРЕЖДЕНИЕ: у типа {entity_type} (method: regex) нет поля pattern — "
                  f"тип пропущен целиком", file=sys.stderr)
            continue
        try:
            pattern = re.compile(pattern_str)
        except re.error as exc:
            print(f"ПРЕДУПРЕЖДЕНИЕ: паттерн типа {entity_type} не компилируется ({exc}) — "
                  f"тип пропущен целиком", file=sys.stderr)
            continue

        validator = None
        validate_name = spec.get("validate")
        if validate_name is not None:
            validator = VALIDATORS.get(validate_name)
            if validator is None:
                print(f"ПРЕДУПРЕЖДЕНИЕ: неизвестный validate '{validate_name}' у типа "
                      f"{entity_type} — тип пропущен целиком", file=sys.stderr)
                continue

        result.append((entity_type, pattern, validator))
    return result


def detect_regex(doc: SourceDocument, config_path: str) -> list[Entity]:
    regex_types = _load_regex_types(config_path)

    entities: list[Entity] = []
    for segment in doc.segments:
        # Ищем в detection_text (копия той же длины с нормализованным регистром,
        # если сегмент был визуально заглавным/строчным), но original_text вырезаем
        # из НАСТОЯЩЕГО segment.text по тем же оффсетам — равная длина делает их
        # валидными в обоих. Для сегментов без detection_text это ровно segment.text.
        search_text = segment.metadata.get("detection_text", segment.text)
        for entity_type, pattern, validator in regex_types:
            for m in pattern.finditer(search_text):
                if validator is not None and not validator(m.group(0)):
                    continue
                entities.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=m.start(),
                    end=m.end(),
                    original_text=segment.text[m.start():m.end()],
                    entity_type=entity_type,
                    detector="regex",
                    confidence=1.0,
                ))
    return entities
