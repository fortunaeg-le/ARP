import re
import sys
import uuid

import yaml

from models import Entity, SourceDocument
from normalizer import detection_view, norm_to_src, _ALPHA_FOLD


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


def inn10_checksum(value: str) -> bool:
    """КС ИНН ЮРИДИЧЕСКОГО ЛИЦА — ровно 10 знаков (этап T2-INN).

    Длина проверяется ЗДЕСЬ, а не только паттерном: у 10- и 12-значного ИНН
    РАЗНЫЕ алгоритмы (одно контрольное число против двух), и после разделения
    типов каждый тип обязан звать СВОЙ. Прибитая к длине проверка не даёт
    будущему ослаблению паттерна тихо отдать 12-значное значение в 10-значный
    алгоритм — там оно вернуло бы False, то есть без якоря просто пропало бы.
    """
    return len(_strip_requisite_separators(value)) == 10 and inn_checksum(value)


def inn12_checksum(value: str) -> bool:
    """КС ИНН ФИЗИЧЕСКОГО ЛИЦА — ровно 12 знаков (этап T2-INN). Симметрично
    inn10_checksum; сам алгоритм (два контрольных числа) не менялся."""
    return len(_strip_requisite_separators(value)) == 12 and inn_checksum(value)


#: `inn_checksum` оставлен в реестре: он по-прежнему единственная реализация
#: обоих алгоритмов ФНС (10 и 12), а inn10/inn12 — лишь прибитые к длине входы в
#: него. Конфиг после этапа T2-INN зовёт inn10/inn12, но имя `inn_checksum`
#: остаётся рабочим (валидный `validate:` для тех, кому нужны обе длины сразу).
VALIDATORS = {
    "inn_checksum": inn_checksum,
    "inn10_checksum": inn10_checksum,
    "inn12_checksum": inn12_checksum,
    "ogrn_checksum": ogrn_checksum,
}

# Этап 4 — КС как сигнал, а не шлагбаум. Окно, в котором ищем якорь СЛЕВА от
# значения (в НОРМАЛИЗОВАННОМ тексте, см. detect_regex). Не пересекает перевод
# строки И НЕ ПЕРЕСЕКАЕТ ЦИФРУ (якорь из соседнего абзаца/ячейки — или из
# ПРЕДЫДУЩЕГО реквизита, если два числа стоят рядом через запятую, — не
# защитывается, см. _has_anchor). Ограничение «нет цифр в разрыве» — тот же
# приём, что и у span_group-якорей SNILS/BIRTHDATE/PASSPORT
# (`[^\n\d]{0,20}` в entity_types.yaml): без него якорь «р/с» первого счёта в
# «р/с 407…, КБК 182…» защитывал бы и вторую, никак не связанную КБК-цифру.
# Совпадает по порядку величины с окнами тех якорей (0..20), взят с запасом под
# более длинные метки счёта («Расчётный счёт (дублирование): »).
_ANCHOR_WINDOW = 40


def _fold_anchor_context(s: str) -> str:
    """Свод латиница->кириллица для ОКНА якоря (не для всего документа — это
    зона normalizer.py, её не трогаем). Переиспользует ТУ ЖЕ таблицу
    визуально неотличимых пар, что и normalize_for_detection (_ALPHA_FOLD), но
    БЕЗ условия «слово смешанного алфавита»: корпус (mutate.py, правило
    homoglyph/combo, NOISE_P=0.15) мутирует омоглифами и обычную прозу вокруг
    сущности, в т.ч. само слово-якорь целиком («р/с» -> «p/с», «к/с» -> «k/с»)
    — оба символа тогда одноалфавитные (только Cyrillic или только Latin) по
    отдельности, и normalizer их не тронет. Ложный fold внутри 40-символьного
    окна перед ЧИСЛОМ реквизита не опасен: единственное, на что он влияет, —
    находится ли якорное слово, само окно не участвует в маскируемом спане."""
    return "".join(_ALPHA_FOLD.get(ch, ch) for ch in s)


def _has_anchor(search_text: str, pos: int, anchor_re: re.Pattern) -> bool:
    """Есть ли якорь anchor_re в окне [pos - _ANCHOR_WINDOW, pos) search_text, не
    пересекая границу строки и не пересекая цифру (см. комментарий у
    _ANCHOR_WINDOW). Асимметрия этапа 4: якорь — это то, что переводит КС из
    шлагбаума в сигнал (см. docstring detect_regex)."""
    window_start = max(0, pos - _ANCHOR_WINDOW)
    context = search_text[window_start:pos]
    cut = -1
    for i, ch in enumerate(context):
        if ch == "\n" or ch.isdigit():
            cut = i
    if cut != -1:
        context = context[cut + 1:]
    return anchor_re.search(_fold_anchor_context(context)) is not None


def _load_regex_types(config_path: str) -> list[tuple[str, re.Pattern, object, int, object]]:
    """Читает entity_types.yaml и возвращает
    [(entity_type, compiled_pattern, validator|None, span_group)] для записей с
    method: regex и enabled != false. Типы с незнакомым validate или
    некомпилируемым/отсутствующим pattern пропускаются целиком с предупреждением в stderr.

    Два способа задать паттерны (этап 3):
      * `pattern:` — один паттерн, как раньше;
      * `patterns:` — СПИСОК записей {pattern, span_group?, validate?, anchor?} для
        типов, у которых одна сущность корпуса пишется несколькими непохожими
        способами. Нужно для PASSPORT: «серия+номер» и «код подразделения» — один
        тип gold, но два разных якоря и две разных формы значения.

    `span_group: N` — номер группы, чей спан становится спаном сущности (по умолчанию
    0, т.е. весь матч). Ключевое для ЯКОРНЫХ детекторов (этап 3): якорь («СНИЛС»,
    «дата рождения», «код подразделения») обязан участвовать в ПОИСКЕ, но не должен
    попадать в МАСКИРУЕМЫЙ спан — иначе повторяется дефект PASSPORT 0c-B, где
    m.group(0) включает слово «паспорт» и спан шире эталонного (recall_exact 0%).
    Детектируем ПО якорю, маскируем БЕЗ якоря.

    `anchor:` — ЭТАП 4. Отдельный (необязательный) regex-якорь, ищется в окне слева
    от значения В ТОМ ЖЕ сегменте (см. `_has_anchor`), но НЕ встроен в `pattern:` —
    в отличие от span_group-якорей выше, значение матчится САМО ПО СЕБЕ (голым
    числом), а `anchor:` лишь меняет роль `validate:` для конкретного найденного
    значения: под якорем КС игнорируется (сигнал, не шлагбаум), без якоря КС
    по-прежнему решает — см. `detect_regex`.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    result = []
    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or spec.get("method") != "regex":
            continue
        if spec.get("enabled", True) is False:
            continue
        # ЭТАП T4: у отрицательных классов (CLAUSE_REF) `suppress_masking: false`
        # — тот же выключатель, что `enabled: false` (симметрично
        # anchor_registry._neg_class_on). У обычных regex-типов поля нет вовсе,
        # get(..., True) всегда True — поведение прежних 13 типов не меняется.
        if spec.get("suppress_masking", True) is False:
            continue

        if spec.get("patterns"):
            raw_entries = spec["patterns"]
        else:
            raw_entries = [{"pattern": spec.get("pattern"),
                            "validate": spec.get("validate"),
                            "span_group": spec.get("span_group", 0),
                            "anchor": spec.get("anchor")}]

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

            anchor = None
            anchor_str = entry.get("anchor")
            if anchor_str is not None:
                try:
                    anchor = re.compile(anchor_str)
                except re.error as exc:
                    print(f"ПРЕДУПРЕЖДЕНИЕ: anchor типа {entity_type} не компилируется "
                          f"({exc}) — якорь проигнорирован", file=sys.stderr)

            result.append((entity_type, pattern, validator, span_group, anchor))
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
        for entity_type, pattern, validator, span_group, anchor in regex_types:
            for m in pattern.finditer(search_text):
                if m.start(span_group) < 0:
                    continue  # группа не участвовала в матче — спана нет

                # ЭТАП 4 — КС как сигнал уверенности, а не шлагбаум (STATE §6).
                # Под якорем (anchor найден слева, см. _has_anchor) значение
                # маскируется НЕЗАВИСИМО от КС: якорь уже подтвердил, что это
                # реквизит, а невалидная КС — опечатка в ПДн, не признак «не ПДн».
                # Без якоря КС по-прежнему решает: validator есть и не сошёлся ->
                # отбраковка (фильтр от FP, законно — нет якорного подтверждения);
                # validator отсутствует (BANK_ACCOUNT) -> без якоря подтвердить
                # нечем, не берём (это и глушит КБК/20-значные коды — FP W2/этапа 3).
                # Типы БЕЗ validate И БЕЗ anchor (SNILS/BIRTHDATE/PASSPORT/KPP/BIK/
                # PHONE/EMAIL/SUM) в этой развилке не участвуют вовсе — для них,
                # как и раньше, подтверждать нечего, значение маскируется
                # безусловно по одному факту совпадения паттерна.
                if validator is not None or anchor is not None:
                    anchored = anchor is not None and _has_anchor(search_text, m.start(span_group), anchor)
                    if not anchored:
                        if validator is None or not validator(m.group(span_group)):
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
