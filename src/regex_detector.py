import re
import sys
import uuid

from config_cache import load_yaml_cached
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

# ЭТАП A2 — анти-якорь (симметричный «anchor:»): вместо ТРЕБОВАНИЯ, что рядом
# есть сигнал, ТРЕБУЕТ ОТСУТСТВИЯ дисквалифицирующего слова рядом. Нужен для
# PASSPORT: «паспорт КАЧЕСТВА»/«паспорт ИЗДЕЛИЯ» (уточнение ПОСЛЕ якоря, уже
# внутри m.group(0) — гэп [^\n\d]{0,40} его туда затягивает) и «ТЕХНИЧЕСКИЙ
# паспорт» (уточнение ПЕРЕД якорем, вне матча — его не увидеть без окна слева).
# Окно короче _ANCHOR_WINDOW: дисквалифицирующее слово всегда СЛИТНО с якорем
# («технический паспорт», не «технический документ, а также паспорт»).
#
# ЭТАП FIX-2 — ОКНО СТАЛО СВОИМ У КАЖДОГО ТИПА (`anti_anchor_window:` в
# entity_types.yaml), а это значение — УМОЛЧАНИЕМ. Повод: этап NEG не закрыл
# семь видов ложных срабатываний ровно потому, что уточнитель у них стоит не
# вплотную («массовая доля ПРИМЕСИ в товаре — не более 0,4 %», «Общество
# осуществляет ДЕЯТЕЛЬНОСТЬ в течение 11 лет»), а расширить общую константу
# нельзя: у PASSPORT она подобрана под свой класс дисквалификаторов
# («ТЕХНИЧЕСКИЙ паспорт»), и её движение меняло бы поведение типа, к находке
# отношения не имеющего. Умолчание оставлено прежним, поэтому все типы, у
# которых ключа нет, работают побайтно как до этапа.
_ANTI_ANCHOR_WINDOW = 20


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


#: ЭТАП A7 — разделители, через которые ЦИФРА СПРАВА/СЛЕВА считается соседом
#: значения. Ровно тот же класс, что разрешён ВНУТРИ реквизита паттернами
#: (B2-fix): обычный и неразрывный пробел. Не `\s`: перевод строки — граница
#: сегмента/ячейки, соседство через него не соседство (A2-NEWLINE-CROSS).
_RUN_SEPARATORS = "  "


def _digit_run_neighbour(search_text: str, start: int, end: int) -> bool:
    """Стоит ли справа или слева от найденного значения ЕЩЁ ОДНА ЦИФРА —
    вплотную или через один разделитель того же класса, что допущен внутри
    самого значения.

    ЭТАП A7 — ограничитель для контрольной суммы (долги `Stage4-C`,
    `A3-NEG-OGRN`, `A5-INN-KBK-FP`). КС на произвольном числе сходится с
    вероятностью 1/10 (ИНН-10), 1/11 (ОГРН-13), 1/13 (ОГРНИП-15) — как
    САМОСТОЯТЕЛЬНЫЙ фильтр она поэтому ненадёжна. Но её ложные срабатывания
    в замерах — не случайные числа вообще, а ОКНО, ВЫРЕЗАННОЕ ИЗ БОЛЕЕ
    ДЛИННОГО ПРОГОНА цифр: 10-значное окно внутри 20-значного КБК
    (`A5-INN-KBK-FP`), 15-значное окно внутри КБК, разбитого пробелами
    (`Stage4-C`). Настоящий реквизит так не пишут: у ИНН/ОГРН своя длина, и
    приписанная сбоку лишняя цифра означает, что мы взяли кусок чужого числа.
    Поэтому: КС БЕЗ ЯКОРЯ перестаёт быть основанием, если значение — часть
    более длинного числа.

    Проверка НЕ применяется под якорем: «ИНН 7707083893 770701001» (ИНН и КПП
    подряд через пробел) — законная запись, и там подтверждение даёт якорь, а
    не КС (инвариант этапа 4: под якорем КС — сигнал, а не шлагбаум).
    """
    left = start - 1
    if left >= 0 and search_text[left] in _RUN_SEPARATORS:
        left -= 1
    if left >= 0 and search_text[left].isdigit():
        return True
    right = end
    if right < len(search_text) and search_text[right] in _RUN_SEPARATORS:
        right += 1
    return right < len(search_text) and search_text[right].isdigit()


def _has_anti_anchor(search_text: str, match_start: int, match_end: int,
                      anti_anchor_re: re.Pattern,
                      window: int = _ANTI_ANCHOR_WINDOW) -> bool:
    """Есть ли дисквалифицирующее слово в окне [start - _ANTI_ANCHOR_WINDOW,
    end) — т.е. НЕПОСРЕДСТВЕННО перед матчем (для «ТЕХНИЧЕСКИЙ паспорт») ИЛИ
    внутри самого матча (для «паспорт КАЧЕСТВА» — уточнение уже в m.group(0),
    см. `_ANTI_ANCHOR_WINDOW`). Не пересекает перевод строки — тот же приём,
    что и `_has_anchor`, дисквалификатор из чужого абзаца не в счёт."""
    window_start = max(0, match_start - window)
    context = search_text[window_start:match_end]
    nl = context.rfind("\n", 0, match_start - window_start)
    if nl != -1:
        context = context[nl + 1:]
    return anti_anchor_re.search(_fold_anchor_context(context)) is not None


#: ЭТАП FIX-3 — умолчание окна ПРАВОГО анти-якоря (`anti_anchor_right_window:`).
#: Заведено ОТДЕЛЬНОЙ константой от левого (`_ANTI_ANCHOR_WINDOW`) намеренно:
#: левое окно уже разведено по типам этапом FIX-2, и связывать их одной
#: величиной значило бы двигать оба, правя одно.
_ANTI_ANCHOR_RIGHT_WINDOW = 40


def _has_anti_anchor_right(search_text: str, match_end: int,
                           anti_anchor_re: re.Pattern,
                           window: int = _ANTI_ANCHOR_RIGHT_WINDOW) -> bool:
    """ЭТАП FIX-3 — ДИСКВАЛИФИКАТОР СПРАВА (долг `FIX2-ANTIANCHOR-RIGHT`).

    Окно анти-якоря было левосторонним по построению, и один вид случая
    (`term/norm-ref`, 138 из 138) им не закрывался НИКАКИМ расширением:
    уточнитель стоит ПОСЛЕ значения — «в течение одного года, КАК УСТАНОВЛЕНО
    статьёй 725 ГК РФ». Расширить левое окно нельзя не потому, что мало, а
    потому, что смотрит в другую сторону.

    Правила те же, что у левого окна, и по той же причине:
      * окно НЕ пересекает `\\n` — уточнитель из следующего абзаца не в счёт
        (иначе дисквалификатор ловил бы чужое предложение);
      * контекст сводится `_fold_anchor_context` — омоглиф в слове-уточнителе
        не должен снимать дисквалификацию;
      * направление ошибки — «лучше не найти»: сработавший дисквалификатор
        ОТМЕНЯЕТ маску, поэтому ошибка здесь стоит пропуска, а не порчи, и
        список слов обязан состоять из оборотов, которые рядом с настоящим
        обязательством Стороны не встречаются.
    """
    context = search_text[match_end:match_end + window]
    nl = context.find("\n")
    if nl != -1:
        context = context[:nl]
    return anti_anchor_re.search(_fold_anchor_context(context)) is not None


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
    config = load_yaml_cached(config_path)

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
                            "anchor": spec.get("anchor"),
                            "anti_anchor": spec.get("anti_anchor"),
                            "anti_anchor_window": spec.get("anti_anchor_window"),
                            "anti_anchor_right": spec.get("anti_anchor_right"),
                            "anti_anchor_right_window":
                                spec.get("anti_anchor_right_window")}]

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

            anti_anchor = None
            anti_anchor_str = entry.get("anti_anchor")
            if anti_anchor_str is not None:
                try:
                    anti_anchor = re.compile(anti_anchor_str)
                except re.error as exc:
                    print(f"ПРЕДУПРЕЖДЕНИЕ: anti_anchor типа {entity_type} не компилируется "
                          f"({exc}) — анти-якорь проигнорирован", file=sys.stderr)

            anti_window = entry.get("anti_anchor_window")
            try:
                anti_window = int(anti_window) if anti_window is not None                     else _ANTI_ANCHOR_WINDOW
            except (TypeError, ValueError):
                print(f"ПРЕДУПРЕЖДЕНИЕ: anti_anchor_window типа {entity_type} не число "
                      f"({anti_window!r}) — взято умолчание {_ANTI_ANCHOR_WINDOW}",
                      file=sys.stderr)
                anti_window = _ANTI_ANCHOR_WINDOW

            # ЭТАП FIX-3 — правый дисквалификатор (свой ключ, своё окно).
            anti_right = None
            anti_right_str = entry.get("anti_anchor_right")
            if anti_right_str is not None:
                try:
                    anti_right = re.compile(anti_right_str)
                except re.error as exc:
                    print(f"ПРЕДУПРЕЖДЕНИЕ: anti_anchor_right типа {entity_type} не "
                          f"компилируется ({exc}) — правый анти-якорь проигнорирован",
                          file=sys.stderr)
            anti_right_window = entry.get("anti_anchor_right_window")
            try:
                anti_right_window = (int(anti_right_window)
                                     if anti_right_window is not None
                                     else _ANTI_ANCHOR_RIGHT_WINDOW)
            except (TypeError, ValueError):
                print(f"ПРЕДУПРЕЖДЕНИЕ: anti_anchor_right_window типа {entity_type} не "
                      f"число ({anti_right_window!r}) — взято умолчание "
                      f"{_ANTI_ANCHOR_RIGHT_WINDOW}", file=sys.stderr)
                anti_right_window = _ANTI_ANCHOR_RIGHT_WINDOW

            result.append((entity_type, pattern, validator, span_group, anchor,
                           anti_anchor, anti_window, anti_right, anti_right_window))
    return result


def detect_regex(doc: SourceDocument, config_path: str,
                 anchor_prefix: str = "") -> list[Entity]:
    """ЭТАП FIX-2 — `anchor_prefix`: ТЕКСТ СЛЕВА, ВИДИМЫЙ ТОЛЬКО ЯКОРЮ.

    Зачем. Якорь ищется в окне слева от значения и не пересекает ни '\\n', ни
    цифру (`_has_anchor`); детекция идёт ПОСЕГМЕНТНО. Поэтому «БИК» в одной
    ячейке таблицы и число в СОСЕДНЕЙ — это якорь, до которого нельзя дотянуться
    ниоткуда: в своём сегменте значение стоит без подтверждения, а граничное
    окно B3 отдаёт только спаны, ПЕРЕСЁКШИЕ стык (здесь не пересекает ничего —
    значение целиком лежит во второй ячейке). Ровно на этом этап NEG потерял
    единственный настоящий БИК реального договора (`DOG-ANCHOR-SPLIT`).

    Класс родственный A6: там разрыв рвал ЗНАЧЕНИЕ, здесь отрывает ЯКОРЬ от
    значения. Механизм тот же — граничное окно, — и новый здесь не заводится:
    вызывающий (`tokenizer._detect_boundary_entities`, шаг 3c) склеивает хвост
    соседней ячейки и передаёт его сюда. Спан, координаты, валидатор, анти-якорь
    и ограничитель соседних цифр остаются прежними; меняется ровно одно — в
    какой строке ищется ЯКОРЬ.

    При `anchor_prefix=""` (все прочие вызовы) поведение побайтно прежнее:
    сдвиг ноль, контекст — сам сегмент.
    """
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
        # Контекст ЯКОРЯ (и только его): сегмент, при нужде — с приклеенным
        # слева хвостом соседней ячейки. Спан по-прежнему ищется и вырезается
        # из search_text, поэтому координаты не сдвигаются никогда.
        anchor_ctx = anchor_prefix + search_text if anchor_prefix else search_text
        shift = len(anchor_prefix)
        for (entity_type, pattern, validator, span_group, anchor,
             anti_anchor, anti_window, anti_right, anti_right_window) in regex_types:
            for m in pattern.finditer(search_text):
                if m.start(span_group) < 0:
                    continue  # группа не участвовала в матче — спана нет

                anchored = anchor is not None and _has_anchor(
                    anchor_ctx, shift + m.start(span_group), anchor)

                # ЭТАП A2 — анти-якорь: дисквалифицирующее слово рядом с якорем
                # («паспорт КАЧЕСТВА», «ТЕХНИЧЕСКИЙ паспорт») отменяет матч
                # целиком, до проверки КС/anchor ниже — это не «слабое
                # подтверждение», это «точно не тот смысл».
                # ЭТАП A7 — анти-якорь НЕ перебивает подтверждённый якорь:
                # у типов с `anchor:` (INN/OGRN) он дисквалифицирует только
                # неподтверждённое значение, где основанием осталась одна КС.
                # У PASSPORT `anchor:` нет (якорь встроен в pattern), anchored
                # там всегда False — поведение A2 сохраняется байт-в-байт.
                if (anti_anchor is not None and not anchored
                        and _has_anti_anchor(
                            anchor_ctx, shift + m.start(0), shift + m.end(0),
                            anti_anchor, anti_window)):
                    continue

                # ЭТАП FIX-3 — тот же приём с ПРАВОЙ стороны значения
                # (FIX2-ANTIANCHOR-RIGHT). Смотрит в search_text, а НЕ в
                # anchor_ctx: приклеенный слева хвост соседней ячейки сдвигает
                # только левые координаты, справа его нет вовсе.
                if (anti_right is not None and not anchored
                        and _has_anti_anchor_right(
                            search_text, m.end(0), anti_right,
                            anti_right_window)):
                    continue

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
                    if not anchored:
                        if validator is None or not validator(m.group(span_group)):
                            continue
                        # ЭТАП A7 — ограничитель для КС: значение, вырезанное
                        # из более длинного прогона цифр, контрольной суммой
                        # НЕ подтверждается (см. _digit_run_neighbour).
                        # По anchor_ctx, а не по сегменту: цифра в конце
                        # соседней ячейки — такой же сосед, и направление
                        # ошибки здесь «лучше не найти, чем нахватать».
                        if _digit_run_neighbour(anchor_ctx,
                                                shift + m.start(span_group),
                                                shift + m.end(span_group)):
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
