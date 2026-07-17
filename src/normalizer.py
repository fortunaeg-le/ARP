"""Этап 2 — НОРМАЛИЗАЦИЯ ТЕКСТА ПЕРЕД ДЕТЕКЦИЕЙ.

Крупнейший бакет утечки ПДн — ИСКАЖЁННЫЙ текст: кириллические омоглифы вместо
цифр (телефон «12З-45-67»), латиница вместо кириллицы («ООО» латиницей),
невидимые символы внутри значения (ZWSP/NBSP), реквизиты, сгруппированные
дефисами («770-123-45-67»). Regex по \\d и Natasha такой текст не ловят: цифра
оказывается буквой, а между цифрами — невидимый символ.

Правило корпуса PT-1 запрещает нормализацию НА ИЗВЛЕЧЕНИИ (искажения обязаны
дойти до детектора как есть, а вывод/сессия — в ИСХОДНЫХ координатах). Поэтому
нормализация «символ-на-символ равной длины» — тупик: невидимые так не удалить, а
разделители внутри числа надо СХЛОПНУТЬ (длина меняется). Решение — строить
НОРМАЛИЗОВАННУЮ КОПИЮ текста ПЛЮС КАРТУ ИНДЕКСОВ norm_idx → src_idx:

  * детекторы ищут в normalize_for_detection(base)[0];
  * каждый найденный спан [n_start, n_end) отображается обратно в исходные
    координаты через norm_to_src(); токенизация/сессии/вывод — в исходных
    координатах, как и раньше;
  * нормализованный текст наружу НЕ выходит НИКОГДА.

ИНВАРИАНТ КАРТЫ. Каждый символ нормализованной копии происходит РОВНО ИЗ ОДНОГО
символа исходного текста (все замены строго 1:1; удаления/схлопывания только
ВЫБРАСЫВАЮТ исходные символы, никогда не порождают новый символ из нескольких).
Поэтому карта — это простой список offset_map длины len(norm), где offset_map[k]
— индекс исходного символа, из которого получен norm[k]; список строго
возрастает. Отсюда точное отображение спана:
    src_start = offset_map[n_start]
    src_end   = offset_map[n_end - 1] + 1
Внутренние (между n_start и n_end) выброшенные разделители попадают в исходный
спан естественно, а ведущие/хвостовые выброшенные разделители — нет, что и
требуется (не захватываем лишний дефис за границей матча).

Осознанно НЕ входит в этот этап (см. HANDOFF_STAGE_2): нормализация РЕГИСТРА
(её частично делает detection_text из extractor.py; полный второй проход NER по
регистро-нормализованной копии — под-задача 2b) и разрывы сущности через границу
сегмента (отдельный контракт SplitEntities). База для нормализации —
detection_text (если есть, он равной длины с segment.text) либо сам segment.text,
поэтому индексы карты всегда валидны как смещения в segment.text.
"""

import bisect
import unicodedata


# --- Свод буквы-на-месте-цифры В ЧИСЛОВОМ КОНТЕКСТЕ (О→0 З→3 Ч→4 І→1 б→6 и т.п.) ---
# Кириллические/латинские буквы, визуально неотличимые от цифр. Свод происходит
# ТОЛЬКО внутри цифрового токена (см. normalize_for_detection) — «О» в «Общество»
# нулём не станет. Латинские O/o добавлены: копипаст из PDF часто латинизирует ноль.
_DIGIT_LOOKALIKE = {
    "О": "0", "о": "0", "O": "0", "o": "0",
    "З": "3", "з": "3",
    "Ч": "4", "ч": "4",
    "І": "1", "і": "1",
    "б": "6",
}

# 'б' («кв. 2б», «д. 12б») — самый опасный из омоглифов: это частотная буква и
# суффикс номера квартиры/дома. Своим→6 её разрешено сводить ТОЛЬКО когда она
# ВНУТРИ цифрового токена (цифры с обеих сторон), но не на краю — так «12б»
# (квартира) остаётся адресом, а «7б0» (искажённая середина реквизита) чинится.
_LOOKALIKE_EDGE_UNSAFE = frozenset("бБ")

# --- Свод АЛФАВИТНЫХ омоглифов латиница → кириллица (для NER/regex/синтаксиса) ---
# Только визуально однозначные пары. Направление латиница→кириллица: корпус —
# русские договоры, Natasha опирается на кириллицу. Свод применяется ТОЛЬКО внутри
# смешанного по алфавиту слова (есть и кириллица, и латиница) — так «Aндрей»
# (латинская A) чинится, а чисто латинское «Microsoft» остаётся нетронутым (иначе
# рост ложных срабатываний NER на настоящей латинице).
_ALPHA_FOLD = {
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "e": "е", "o": "о", "c": "с", "p": "р", "x": "х", "y": "у",
    "k": "к", "m": "м",
}

# --- Пробельные символы: приводятся к обычному пробелу U+0020 (замена 1:1) ---
# NBSP/narrow-NBSP/thin space и пр. визуально — пробелы. Приводим к обычному, чтобы
# (1) паттерны реквизитов, разрешающие лишь [' ', NBSP], ловили и narrow-NBSP;
# (2) граница слова для NER сохранялась (пробел не удаляем, иначе склеим ФИО).
# \t и \n НЕ трогаем — это структура сегмента (перенос строки в ячейке).
_SPACE_LIKE = frozenset(
    chr(c) for c in (
        0x00A0,  # NO-BREAK SPACE
        0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,  # en/em/… quads
        0x2007,  # FIGURE SPACE
        0x2008,  # PUNCTUATION SPACE
        0x2009,  # THIN SPACE
        0x200A,  # HAIR SPACE
        0x202F,  # NARROW NO-BREAK SPACE
        0x205F,  # MEDIUM MATHEMATICAL SPACE
        0x3000,  # IDEOGRAPHIC SPACE
    )
)

# --- Дефис-подобные: кандидаты на схлопывание ВНУТРИ числа ---
# Обычный дефис, неразрывный дефис, figure dash, en dash, минус. Em dash (U+2014)
# НАМЕРЕННО не входит — это тире прозы между словами, схлопывать его нельзя.
_HYPHEN_LIKE = frozenset(
    chr(c) for c in (
        0x002D,  # HYPHEN-MINUS
        0x2010,  # HYPHEN
        0x2011,  # NON-BREAKING HYPHEN
        0x2012,  # FIGURE DASH
        0x2013,  # EN DASH
        0x2212,  # MINUS SIGN
    )
)


def _is_ascii_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _is_cyrillic(ch: str) -> bool:
    # Кириллический блок U+0400..U+04FF (в т.ч. Ё/ё) — достаточно для признака
    # «слово смешанного алфавита» в своде латиница→кириллица.
    return "Ѐ" <= ch <= "ӿ"


def normalize_for_detection(base: str) -> tuple[str, list[int]]:
    """Строит (norm_text, offset_map) — нормализованную копию текста и карту
    norm_idx → src_idx (индекс в base). Каждый символ norm происходит ровно из
    одного символа base (см. инвариант карты в docstring модуля).

    На неискажённом тексте функция — тождество (norm == base, offset_map ==
    range): свод срабатывает лишь при наличии омоглифов/невидимых/дефисов между
    цифрами. Это гарантирует, что recall_exact на каноническом корпусе не падает.
    """
    if not base:
        return "", []

    # --- Проход 1: снять невидимые (Cf), пробельные привести к ' ', остальное
    # оставить как есть. Собираем параллельные списки chars/src (src[k] — индекс
    # символа chars[k] в base). ---
    chars: list[str] = []
    src: list[int] = []
    for i, ch in enumerate(base):
        if ch in _SPACE_LIKE:
            chars.append(" ")
            src.append(i)
        elif ch in _HYPHEN_LIKE:
            chars.append(ch)
            src.append(i)
        elif unicodedata.category(ch) == "Cf":
            # Zero-width/невидимые управляющие форматирования: ZWSP/ZWNJ/ZWJ/WJ/
            # SHY/BOM/LRM/RLM. Удаляем целиком — длина падает, карта это учитывает.
            continue
        else:
            chars.append(ch)
            src.append(i)

    m = len(chars)
    is_sep = [c == " " or c in _HYPHEN_LIKE for c in chars]
    is_dl = [_is_ascii_digit(c) or c in _DIGIT_LOOKALIKE for c in chars]

    # --- Проход 2: цифровые токены (максимальные прогоны из цифр/омоглифов-цифр
    # БЕЗ разделителей). Внутри токена с >=2 реальными цифрами сводим омоглифы в
    # цифры. Токен с >=1 реальной цифрой помечаем numeric — это разрешает
    # схлопывание разделителей вокруг него (см. проход 3). ---
    out_chars = list(chars)
    is_num = [False] * m           # символ принадлежит цифровому токену (>=1 цифра)
    k = 0
    while k < m:
        if not is_dl[k]:
            k += 1
            continue
        j = k
        while j < m and is_dl[j]:
            j += 1
        digit_count = sum(1 for t in range(k, j) if _is_ascii_digit(chars[t]))
        if digit_count >= 1:
            for t in range(k, j):
                is_num[t] = True
            # Порог >=2 реальных цифр: защищает «2б»/«5о» (номер квартиры,
            # одиночная цифра рядом с буквой) от порчи. Реквизиты/телефоны — это
            # длинные прогоны, у них цифр заведомо больше.
            if digit_count >= 2:
                for t in range(k, j):
                    c = chars[t]
                    repl = _DIGIT_LOOKALIKE.get(c)
                    if repl is None:
                        continue
                    if c in _LOOKALIKE_EDGE_UNSAFE and (t == k or t == j - 1):
                        continue   # 'б' на краю токена не трогаем (см. константу)
                    out_chars[t] = repl
        k = j

    # --- Проход 3: схлопывание разделителей МЕЖДУ цифровыми токенами. Убираем
    # прогон разделителей, если слева и справа — цифровой символ И прогон либо
    # содержит дефис, либо длиннее одного символа. Одиночный пробел/NBSP между
    # цифрами НЕ трогаем: его уже допускают паттерны реквизитов, а лишнее
    # схлопывание только повышает риск склейки соседних чисел. ---
    keep = [True] * m
    k = 0
    while k < m:
        if not is_sep[k]:
            k += 1
            continue
        j = k
        has_hyphen = False
        while j < m and is_sep[j]:
            if chars[j] in _HYPHEN_LIKE:
                has_hyphen = True
            j += 1
        left_num = k - 1 >= 0 and is_num[k - 1]
        right_num = j < m and is_num[j]
        if left_num and right_num and (has_hyphen or (j - k) >= 2):
            for t in range(k, j):
                keep[t] = False
        k = j

    out2_chars: list[str] = []
    offset_map: list[int] = []
    for k in range(m):
        if keep[k]:
            out2_chars.append(out_chars[k])
            offset_map.append(src[k])

    # --- Проход 4: свод алфавитных омоглифов латиница→кириллица ВНУТРИ смешанных
    # по алфавиту слов (есть и кириллица, и сводимая латиница). ---
    p = 0
    L = len(out2_chars)
    while p < L:
        if not out2_chars[p].isalpha():
            p += 1
            continue
        q = p
        while q < L and out2_chars[q].isalpha():
            q += 1
        token = out2_chars[p:q]
        has_cyr = any(_is_cyrillic(c) for c in token)
        has_lat_fold = any(c in _ALPHA_FOLD for c in token)
        if has_cyr and has_lat_fold:
            for t in range(p, q):
                repl = _ALPHA_FOLD.get(out2_chars[t])
                if repl is not None:
                    out2_chars[t] = repl
        p = q

    return "".join(out2_chars), offset_map


def norm_to_src(offset_map: list[int], n_start: int, n_end: int) -> tuple[int, int]:
    """Отображает спан [n_start, n_end) нормализованного текста в исходные
    координаты. Требует n_end > n_start (пустые спаны детекторы не создают)."""
    return offset_map[n_start], offset_map[n_end - 1] + 1


def src_to_norm(offset_map: list[int], s: int, e: int) -> tuple[int, int]:
    """Обратное отображение: исходный спан [s, e) → спан нормализованного текста.
    Нужно, чтобы координаты уже найденных (в исходных координатах) сущностей —
    напр. regex-реквизитов-барьеров для расширения адреса — привести к системе
    координат норм-текста, в которой работает NER. offset_map строго возрастает,
    поэтому bisect корректен. Выброшенные при нормализации исходные позиции
    отображаются на ближайшую сохранённую границу — для геометрии барьеров этого
    достаточно (барьер лишь останавливает расширение, точность до символа не
    критична)."""
    ns = bisect.bisect_left(offset_map, s)
    ne = bisect.bisect_left(offset_map, e)
    return ns, ne


def detection_view(segment) -> tuple[str, list[int]]:
    """Возвращает (norm_text, offset_map) для сегмента, кэшируя результат в
    segment.metadata. База нормализации — detection_text (регистро-нормализованная
    копия из extractor, равной длины с segment.text) при наличии, иначе сам
    segment.text. В обоих случаях offset_map индексирует segment.text.

    Детекторы (regex/ner) вызывают эту функцию вместо прямого чтения
    detection_text: normalize_for_detection на неискажённом тексте — тождество,
    поэтому для чистых сегментов поведение не меняется."""
    md = segment.metadata
    cached = md.get("_norm_cache")
    if cached is not None:
        return cached
    base = md.get("detection_text", segment.text)
    norm, offset_map = normalize_for_detection(base)
    md["_norm_cache"] = (norm, offset_map)
    return norm, offset_map
