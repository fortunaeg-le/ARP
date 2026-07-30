# -*- coding: utf-8 -*-
"""
measure_lib — вспомогательный модуль замера покрытия (этап 2).

Не трогает код системы. Отвечает за три вещи, которые нужны харнессу
run_measurement.py и которые сознательно вынесены из него ради проверяемости:

  1. Отображение сущностей, найденных системой (координаты ЛОКАЛЬНЫЕ внутри
     segment.text), в глобальные координаты канонического plain-текста PT-1
     (в которых размечен gold.json).  См. map_entities_to_pt1().
  2. Нормализация текста для метрики RESIDUAL LEAK (§4.4): снятие невидимых
     символов, схлопывание пробелов, приведение регистра и омоглифов к
     каноническому виду; извлечение цифрового «ядра».
  3. Сопоставление найденного с эталоном: recall / span coverage / precision.

ВАЖНО про PT-1: система читает ТОЛЬКО тело документа (абзацы + ячейки таблиц
верхнего уровня).  В PT-1 тело — это непрерывный участок между колонтитулом
(если есть) и сносками/нижним колонтитулом.  Поэтому отображение делается
курсором-вперёд ТОЛЬКО по участку тела; сущности из колонтитулов/сносок/надписей/
вложенных таблиц система не находит вовсе — они остаются пропусками (это и меряем).
"""
import zipfile

from lxml import etree

# PT-1 функции берём из эталонной реализации корпуса — тем же кодом, что породил
# gold-координаты, чтобы гарантированно совпасть с ними символ в символ.
import corpus_lib
from corpus_lib import WNS, _p_text, _tbl_text, _blocks_text


# --------------------------------------------------------------------------- #
#                     PT-1: тело документа и его границы                       #
# --------------------------------------------------------------------------- #
def pt1_text(path: str) -> str:
    """Канонический plain-текст PT-1 всего документа (в координатах gold)."""
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    return corpus_lib.extract_docx(path)


def _docx_header_and_body_text(path: str):
    """(header_text|None, body_text) по правилу PT-1.  Нужны, чтобы вычислить, где
    в общем PT-1-тексте начинается тело: G = "\\n".join([header?, body, footnotes?,
    footer?]); тело система читает целиком и только его."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    header = None
    if "word/header1.xml" in names:
        header = _blocks_text(etree.fromstring(z.read("word/header1.xml")))
    doc = etree.fromstring(z.read("word/document.xml"))
    body = doc.find(WNS + "body")
    body_blocks = [c for c in body if c.tag in (WNS + "p", WNS + "tbl")]
    body_text = "\n".join(
        _p_text(c) if c.tag == WNS + "p" else _tbl_text(c) for c in body_blocks
    )
    return header, body_text


def body_offset(path: str, full_text: str) -> int:
    """Смещение начала тела документа внутри полного PT-1-текста.
    Для .txt — 0.  Для .docx — len(header)+1, если есть колонтитул, иначе 0."""
    if path.lower().endswith(".txt"):
        return 0
    header, _body = _docx_header_and_body_text(path)
    return (len(header) + 1) if header is not None else 0


# --------------------------------------------------------------------------- #
#      Отображение (segment_id, local offset) -> глобальное PT-1-смещение      #
# --------------------------------------------------------------------------- #
def build_segment_offsets(doc, full_text: str, body_start: int):
    """Курсором-вперёд находит абсолютное смещение каждого сегмента системы в
    участке тела PT-1-текста.  Порядок сегментов системы совпадает с порядком тела
    PT-1 (оба идут по дочерним элементам body в порядке документа), поэтому
    однопроходный курсор устойчив к повторам (пустые ячейки, одинаковые значения).

    Возвращает (offsets: dict segment_id->abs_start|None, misses: list segment_id
    с текстом, который не удалось локализовать)."""
    offsets = {}
    misses = []
    cursor = body_start
    for seg in doc.segments:
        if not seg.text:
            offsets[seg.id] = None
            continue
        idx = full_text.find(seg.text, cursor)
        if idx == -1:
            # запасной путь: ищем с начала тела (на случай редкого рассогласования
            # порядка); если и так нет — фиксируем промах локализации честно.
            idx = full_text.find(seg.text, body_start)
        if idx == -1:
            offsets[seg.id] = None
            misses.append(seg.id)
            continue
        offsets[seg.id] = idx
        cursor = idx + len(seg.text)
    return offsets, misses


# соответствие типов: ключ entity_types.yaml -> тип gold
TYPE_MAP = {
    "PERSON": "PER",
    "ORG": "ORG",
    "ADDRESS": "ADDRESS",
    "INN": "INN",
    # ЭТАП T2-INN: тип конфига INN_PERSON (12 цифр, физлицо) -> тип gold INN_PER.
    # Короткий код в замере — та же традиция, что PERSON->PER и BANK_ACCOUNT->ACCOUNT.
    "INN_PERSON": "INN_PER",
    "OGRN": "OGRN",
    "KPP": "KPP",
    "BANK_ACCOUNT": "ACCOUNT",
    "BIK": "BIK",
    "PASSPORT": "PASSPORT",
    "PHONE": "PHONE",
    "EMAIL": "EMAIL",
    "SUM": "SUM",          # в gold типа SUM нет — суммы это негативы
    # ЭТАП T2: имена типа в конфиге и в разметке СОВПАДАЮТ (в отличие от
    # SUM/MONEY, где они разошлись исторически). Записи тождественные — они
    # здесь ради полноты карты: тип, которого в TYPE_MAP нет вовсе, проходит
    # через .get(...) фолбэком, и отличить «имя совпадает» от «про тип забыли»
    # стало бы невозможно.
    "PERCENT": "PERCENT",
    "TERM": "TERM",
    "DATE": "DATE",
}


def map_entities_to_pt1(entities, offsets, full_text):
    """Список найденных Entity -> список dict со СГЛОБАЛИЗОВАННЫМИ координатами.
    Каждый элемент: {gtype, start, end, text, detector, seg_ok(bool),
    text_ok(bool — совпал ли срез PT-1 с original_text)}."""
    out = []
    for e in entities:
        base = offsets.get(e.segment_id)
        gtype = TYPE_MAP.get(e.entity_type, e.entity_type)
        if base is None:
            out.append({
                "gtype": gtype, "raw_type": e.entity_type,
                "start": None, "end": None, "text": e.original_text,
                "detector": e.detector, "seg_ok": False, "text_ok": False,
            })
            continue
        gs, ge = base + e.start, base + e.end
        sliced = full_text[gs:ge]
        out.append({
            "gtype": gtype, "raw_type": e.entity_type,
            "start": gs, "end": ge, "text": e.original_text,
            "detector": e.detector, "seg_ok": True,
            "text_ok": (sliced == e.original_text),
        })
    return out


# --------------------------------------------------------------------------- #
#           ЭТАП T4 — зеркало подавления: гейт «эталон под барьером»           #
# --------------------------------------------------------------------------- #
def globalize_suppressions(suppression_log, offsets):
    """`suppression_log` (см. `tokenizer._resolve_overlaps`, ЭТАП T4) -> тот же
    список записей с ГЛОБАЛЬНЫМИ координатами (`gstart`/`gend` в PT-1), плюс
    `seg_ok`. Сегмент без локализации (`offsets[...] is None`) -> `seg_ok=False`
    (та же семантика, что `map_entities_to_pt1`)."""
    out = []
    for s in suppression_log:
        base = offsets.get(s["segment_id"])
        if base is None:
            out.append({**s, "gstart": None, "gend": None, "seg_ok": False})
            continue
        out.append({**s, "gstart": base + s["start"], "gend": base + s["end"], "seg_ok": True})
    return out


def suppressed_gold_entities(suppressions_global, gold_entities):
    """Пересечение подавлений (глобальные координаты, `globalize_suppressions`) с
    ЭТАЛОННЫМИ сущностями документа. Возвращает список записей — по одной на
    каждую пару (подавление, эталон), чьи интервалы пересекаются: `gold_type`,
    `gold_text`, `gold_start/end`, `suppressed_type`, `suppressor_type`,
    `sup_start/end`. НЕПУСТОЙ список означает: эталонная сущность потеряла часть
    маски из-за отрицательного класса — линия гейта T4 обязана быть 0
    (ГЛАВНЫЙ РИСК этапа T4, см. docs/T4_REPORT.md)."""
    out = []
    for s in suppressions_global:
        if not s["seg_ok"]:
            continue
        for g in gold_entities:
            if max(s["gstart"], g["start"]) < min(s["gend"], g["end"]):
                out.append({
                    "gold_type": g["type"], "gold_text": g["text"],
                    "gold_start": g["start"], "gold_end": g["end"],
                    "suppressed_type": s["suppressed_type"],
                    "suppressor_type": s["suppressor_type"],
                    "sup_start": s["gstart"], "sup_end": s["gend"],
                })
    return out


# --------------------------------------------------------------------------- #
#                          Нормализация (RESIDUAL LEAK)                        #
# --------------------------------------------------------------------------- #
INVISIBLES = "".join(corpus_lib.INVISIBLES)
_INVIS_TABLE = {ord(ch): None for ch in INVISIBLES}

# буква-на-месте-цифры -> цифра (для «ядра» реквизитов, испорченных омоглифами)
_CYR2DIGIT = dict(corpus_lib.CYR2DIGIT)
# кириллица <-> латиница: сведём взаимные омоглифы к латинице
_CYR2LAT = dict(corpus_lib.CYR2LAT)


def _strip_invisibles(s: str) -> str:
    return s.translate(_INVIS_TABLE)


def norm_text(s: str) -> str:
    """Каноничная форма для сравнения текста: без невидимых, регистр вниз,
    омоглифы-буквы к латинице, буквы-на-месте-цифр к цифрам, пробелы схлопнуты."""
    s = _strip_invisibles(s)
    out = []
    for ch in s:
        if ch in _CYR2DIGIT:
            out.append(_CYR2DIGIT[ch])
        elif ch in _CYR2LAT:
            out.append(_CYR2LAT[ch])
        else:
            out.append(ch)
    s = "".join(out).lower()
    return " ".join(s.split())


def norm_nospace(s: str) -> str:
    """norm_text без единого пробела — для номеров, разбитых пробелами/дефисами."""
    return norm_text(s).replace(" ", "").replace("-", "")


def digit_cores(s: str, min_len: int = 6):
    """Максимальные цифровые последовательности длиной >= min_len из
    нормализованного (омоглифо-свёрнутого, беспробельного) текста.

    ВНИМАНИЕ (диагноз бага, зафиксирован на этапе 0b, НЕ править — метрику v1
    оставляем как есть для тренда).  Эта функция возвращает МАКСИМАЛЬНЫЕ цифровые
    run-ы эталонного значения ЦЕЛИКОМ.  leak_pieces() затем ищет каждый такой run
    как ПОДСТРОКУ анонимного текста.  Следствие: если 20-значный счёт разорван
    границей ячейки и до анонимного текста дожила лишь половина (11 цифр открытым
    текстом), полный 20-значный run как подстрока не находится и харнесс рапортует
    «не утекло».  Это прямо противоречит докстрингу leak_pieces («какие ЧАСТИ
    эталонного значения дожили»): v1 меряет не части, а выживание ЦЕЛОГО ядра.
    Систематическое ЗАНИЖЕНИЕ утечки.  Честную частичную метрику см. в leak_v2()."""
    s = norm_text(s)
    # приведём омоглифы-цифры уже сделаны; уберём разделители внутри числа
    compact = []
    for ch in s:
        if ch.isdigit():
            compact.append(ch)
        elif ch in " -":
            continue
        else:
            compact.append(" ")
    runs = "".join(compact).split()
    return [r for r in runs if len(r) >= min_len]


def alpha_tokens(s: str, min_len: int = 4):
    """Буквенные токены (>= min_len) из нормализованного текста — для ФИО/ORG."""
    s = norm_text(s)
    toks = []
    cur = []
    for ch in s:
        if ch.isalpha():
            cur.append(ch)
        else:
            if cur:
                toks.append("".join(cur))
                cur = []
    if cur:
        toks.append("".join(cur))
    return [t for t in toks if len(t) >= min_len]


def address_components(s: str):
    """Компоненты адреса для покомпонентной проверки утечки: цифровые группы
    (дом/корпус/квартира/индекс) и значимые буквенные токены (город/улица)."""
    comps = []
    comps += digit_cores(s, min_len=2)              # дом=«5», индекс=«170100»
    comps += alpha_tokens(s, min_len=3)             # Тверь, Советская, Ленина
    # выкинем служебные маркеры, чтобы не считать «утечкой» слово «ул»/«дом»
    stop = {"обл", "область", "город", "улица", "дом", "корпус", "квартира",
            "кв", "стр", "литера", "пом", "тер", "снт", "проспект", "переулок",
            "шоссе", "набережная", "бульвар", "проезд", "район", "рп", "пгт",
            "деревня", "село", "мкр", "микрорайон", "владение", "здание", "офис",
            "россия", "российская", "федерация"}
    return [c for c in comps if c not in stop]


# =========================================================================== #
#    leak_v2 — метрика ЧАСТИЧНОЙ утечки (этап 0b).  СОБСТВЕННЫЙ нормализатор.   #
# =========================================================================== #
# АРХИТЕКТУРНОЕ ТРЕБОВАНИЕ — НЕ РЕФАКТОРИТЬ, НЕ «ДЕДУПЛИЦИРОВАТЬ»:
#   Всё, что ниже (таблицы невидимых/омоглифов, _v2_* нормализаторы), — намеренная
#   НЕЗАВИСИМАЯ копия.  Она НЕ импортирует нормализацию из src/ и не переиспользует
#   норму v1 выше.  На следующем этапе слой нормализации появится в САМОЙ системе
#   (src/).  Если измерительный прибор и измеряемая система разделят код
#   нормализации, общий баг спрячет утечку ОДНОВРЕМЕННО в обоих — и увидеть это
#   будет нечем.  Дублирование здесь — требование корректности измерения, а не
#   небрежность.  Списки символов заданы явно (а не взяты из corpus_lib), чтобы
#   метрика оставалась независимой даже от эталонного инструментария корпуса.

import re as _re  # noqa: E402
import unicodedata as _ud  # noqa: E402

# Невидимые и форматирующие символы (снимаются перед сравнением).
_V2_INVIS = {
    0x00A0,  # NBSP
    0x202F,  # narrow NBSP
    0x200B,  # ZWSP
    0x200D,  # ZWJ
    0x200C,  # ZWNJ
    0x00AD,  # SHY (мягкий перенос)
    0x2060,  # WORD JOINER
}

# Омоглифы кириллица <-> латиница: сводим взаимные пары к латинице.
_V2_HOMOGLYPH = {
    "А": "A", "О": "O", "С": "C", "Е": "E", "Р": "P", "Х": "X", "К": "K",
    "М": "M", "Т": "T", "В": "B", "Н": "H", "У": "Y",
    "а": "a", "о": "o", "с": "c", "е": "e", "р": "p", "х": "x", "к": "k",
    "м": "m", "т": "t", "в": "b", "н": "h", "у": "y",
}

# Буква на месте цифры -> цифра (для «ядра» реквизитов).
_V2_LETTER2DIGIT = {
    "О": "0", "о": "0", "З": "3", "з": "3", "Ч": "4", "ч": "4",
    "І": "1", "і": "1", "б": "6", "Б": "6",
}


def _v2_strip_invisible(s: str) -> str:
    """Снятие невидимых из явного списка И всех символов категории Cf."""
    return "".join(
        ch for ch in s
        if ord(ch) not in _V2_INVIS and _ud.category(ch) != "Cf"
    )


def v2_norm_text(s: str) -> str:
    """Каноничная форма для БУКВЕННОГО сравнения (PER/ADDRESS/EMAIL):
    снятие невидимых/Cf, свод омоглифов к латинице, нижний регистр, схлопывание
    пробелов.  Цифры не трогаем (буквы-на-месте-цифр здесь остаются буквами)."""
    s = _v2_strip_invisible(s)
    s = "".join(_V2_HOMOGLYPH.get(ch, ch) for ch in s)
    return " ".join(s.lower().split())


def v2_digit_runs(s: str) -> str:
    """Цифровое поле текста для ЧИСЛОВОГО сравнения: снятие невидимых/Cf, свод
    буквы-на-месте-цифр к цифрам, затем оставляем только цифры; run-ы цифр,
    разделённые ЛЮБЫМ нецифровым символом кроме пробела/дефиса, разделяются
    пробелом-разделителем.  Пробел/дефис МЕЖДУ цифрами склеиваются (номер,
    разбитый пробелами, — это один номер).  Возвращает строку вида
    "runA runB ..." — пробел гарантирует, что окно не пересечёт границу двух
    независимых чисел."""
    s = _v2_strip_invisible(s)
    s = "".join(_V2_LETTER2DIGIT.get(ch, ch) for ch in s)
    out = []
    prev_digit = False
    for ch in s:
        if ch.isdigit():
            out.append(ch)
            prev_digit = True
        elif ch in (" ", "-") and prev_digit:
            # разделитель ВНУТРИ числа — склеиваем, но помечаем, что если дальше
            # снова цифра, run продолжается; если буква — run оборвётся ниже.
            continue
        else:
            if prev_digit:
                out.append(" ")
            prev_digit = False
    return " ".join("".join(out).split())


def v2_core_digits(s: str) -> str:
    """«Ядро» эталонного числового значения — все его цифры подряд (после свода
    буквы-на-месте-цифр).  Для счёта/ИНН/телефона это одна непрерывная строка."""
    s = _v2_strip_invisible(s)
    s = "".join(_V2_LETTER2DIGIT.get(ch, ch) for ch in s)
    return "".join(ch for ch in s if ch.isdigit())


# Доля run-а, которую обязано покрывать окно, чтобы run считался ОСТАТКОМ
# эталонного значения, а не чужим числом со случайным совпадением (Stage4-A).
# Обоснование — численное, замер по корпусу (см. HANDOFF_METRIC_NUMERIC.md §2):
#   принято (реальные утечки):   100% (разрыв: остаток стоит отдельным run-ом),
#                                95% / 94% / 90% (склейка: к остатку прилипла
#                                одна цифра соседнего числа через пробел);
#   отклонено (совпадения):      73% («40802810205» — чужое число с тем же
#                                стандартным префиксом счёта), 67% (БИК внутри
#                                корсчёта), 40% (ДРУГОЙ счёт того же банка),
#                                30% x6 (КБК — собственно Stage4-A).
# Между 73% и 90% — пустой зазор; порог 0.8 стоит в его середине.
RUN_COVERAGE_MIN = 0.8


def _lcs_with_run(core: str, run: str) -> str:
    """Самое длинное непрерывное окно `core`, встречающееся внутри ОДНОГО run-а."""
    best = ""
    L = len(core)
    for i in range(L):
        for j in range(L, i + len(best), -1):
            w = core[i:j]
            if w in run:
                if len(w) > len(best):
                    best = w
                break
    return best


def longest_surviving_window(core: str, field: str) -> str:
    """Самое длинное окно `core`, ДОЖИВШЕЕ в цифровом поле анонимного текста.

    Окно засчитывается, только если run (непрерывная цифровая группа анонимного
    текста), в котором оно найдено, сам является ОСТАТКОМ эталонного значения:
      * либо дожило ядро ЦЕЛИКОМ (полное выживание — утечка по определению,
        сколько бы цифр соседнего числа к нему ни прилипло),
      * либо окно покрывает >= RUN_COVERAGE_MIN длины run-а (остаток значения,
        к которому через пробел приклеилась цифра-другая соседнего числа).

    Зачем (дефект Stage4-A, исправлен здесь).  Раньше функция искала окно по
    ВСЕМУ полю разом, и короткая подстрока постороннего числа засчитывалась
    утечкой НИКАК не связанного реквизита: КБК «18210101011011000110» содержит
    «182101» из КПП «341821013», корсчёт содержит БИК, все расчётные счета
    банка делят префикс «40802810».  Сущность при этом замаскирована ЦЕЛИКОМ —
    метрика рапортовала утечку там, где её нет.

    Порог применяется к RUN-у, а не к ядру, намеренно: вопрос «остаток ли это
    моего значения» — про то, из чего состоит выживший в тексте кусок, а не про
    то, какую долю эталона он составляет.  Иначе честная частичная утечка (11
    цифр из 20 при разрыве по границе ячейки) не отличалась бы от совпадения.

    `field` разделён пробелами между независимыми числами, поэтому окно
    физически не может пересечь границу двух чисел.  core короткий (<=25),
    перебор дёшев."""
    best = ""
    for run in field.split():
        w = _lcs_with_run(core, run)
        if not w or len(w) <= len(best):
            continue
        if w == core or len(w) >= RUN_COVERAGE_MIN * len(run):
            best = w
    return best


def leak_v2_numeric(gtext: str, anon_digit_field: str,
                    strict: int = 8, soft: int = 6):
    """Частичная утечка числового реквизита.

    core = все цифры эталона подряд.  Ищем самое длинное окно core, дожившее в
    цифровом поле анонимного текста.  Порог адаптируется под короткие ядра
    (thr = min(thr, len(core))).  Возвращает dict:
      status: none|partial|full
      fragments: [самое длинное выжившее окно] (пусто при none)
      window_len: длина этого окна
      threshold: строгий порог, если окно прошло его; иначе мягкий, если прошло
                 мягкий; иначе None
      core_len: длина ядра (чтобы отличать «короткое ядро» при аудите)."""
    core = v2_core_digits(gtext)
    res = {"status": "none", "fragments": [], "window_len": 0,
           "threshold": None, "core_len": len(core)}
    if not core:
        return res
    best = longest_surviving_window(core, anon_digit_field)
    if not best:
        return res
    thr_strict = min(strict, len(core))
    thr_soft = min(soft, len(core))
    if len(best) < thr_soft:
        return res  # окно есть, но короче мягкого порога — не считаем утечкой
    res["fragments"] = [best]
    res["window_len"] = len(best)
    res["threshold"] = thr_strict if len(best) >= thr_strict else thr_soft
    res["status"] = "full" if best == core else "partial"
    return res


def per_tokens(gtext: str, min_len: int = 3):
    """Токены ФИО эталона (фамилия/имя/отчество/инициалы) в v2-норме.  Порог 3
    символа — чтобы ловить короткие корейские/тюркские имена («Ким Ен Су») и
    двухбуквенные части имени, которых v1 (порог 4) не видит вовсе."""
    s = v2_norm_text(gtext)
    toks = []
    cur = []
    for ch in s:
        if ch.isalpha():
            cur.append(ch)
        else:
            if cur:
                toks.append("".join(cur))
                cur = []
    if cur:
        toks.append("".join(cur))
    return [t for t in toks if len(t) >= min_len]


def leak_v2_per(gtext: str, anon_norm_v2: str):
    """Частичная утечка PER: выжил любой токен >=3 символов ИЛИ значение целиком."""
    whole = v2_norm_text(gtext)
    res = {"status": "none", "fragments": []}
    if whole and whole in anon_norm_v2:
        res["status"] = "full"
        res["fragments"] = [whole]
        return res
    survived = [t for t in per_tokens(gtext, 3) if t in anon_norm_v2]
    if survived:
        res["status"] = "partial"
        res["fragments"] = sorted(set(survived))
    return res


_V2_ADDR_STOP = {
    "обл", "область", "город", "улица", "дом", "корпус", "квартира", "кв",
    "стр", "литера", "пом", "тер", "снт", "проспект", "переулок", "шоссе",
    "набережная", "бульвар", "проезд", "район", "рп", "пгт", "деревня", "село",
    "мкр", "микрорайон", "владение", "здание", "офис", "россия", "российская",
    "федерация", "город", "гор",
    # сокращённые маркеры типа объекта, встречающиеся в корпусе как отдельные
    # токены; они НЕ идентифицируют адрес («пер», «наб», «край», «корп»).
    "ул", "пер", "наб", "бул", "пос", "просп", "пр", "ш", "корп", "край",
    "респ", "республика", "поселок", "посёлок",
}

# --------------------------------------------------------------------------- #
#   Пороги метрики ADDRESS (этап «починка прибора»).  Обоснование — численное,  #
#   замер на подвыборке корпуса (101 док.), см. HANDOFF_METRIC_ADDRESS.md §2.   #
# --------------------------------------------------------------------------- #
# Значимый буквенный токен: >= 4 букв.  На длине 3 словарь адресов корпуса —
# ЦЕЛИКОМ маркеры типа объекта (обл/стр/пом/наб/бул/пер/тер/пос/пгт) плюс шум
# омоглифных мутаций; ни одного идентифицирующего топонима длины 3 в корпусе нет
# (единственное географическое слово — «дон» из «Ростов-на-Дону», где голову
# несёт «ростов», 6 букв).
ADDR_ALPHA_MIN = 4
# Усечённый топоним: префикс >= 6 букв И >= 75% длины токена.  Замер пар
# (токен адреса, слово анонимного текста), где слово — префикс токена, по
# перекрёстному словарю подвыборки (86 токенов x 1030 слов):
#   порог          пар   из них ложных
#   >=3 букв        18   17 («нов»⊂«новосибирск», «род»⊂«родник», «про»⊂«проф»)
#   >=4 букв         3    2 («иван»/«иванов» ⊂ «ивановское» — это ФАМИЛИЯ, не адрес)
#   >=6 букв + 75%   1    0 (только истинное «новосибирск» ⊂ «новосибирская»)
#   >=6 букв + 90%   0    — теряется и истинный случай (11/13 = 0.85)
# 6 букв — это ещё и длина самого короткого идентифицирующего топонима корпуса
# (москва/самара/казань): фрагмент короче 6 букв сам по себе никого не называет.
# 75% — допуск на срез до четверти слова; реальное усечение спана детектором
# составляет 1-2 символа, так что запас взят с большим полем.
ADDR_PREFIX_MIN = 6
ADDR_PREFIX_RATIO = 0.75


# Слово = максимальная последовательность букв (цифры и знаки — границы).
_ADDR_WORD_RE = _re.compile(r"[^\W\d_]+", _re.UNICODE)


def _addr_word_set(anon_norm_v2: str):
    """Множество ЦЕЛЫХ слов анонимного текста (v2-норма).  Ключ лечения
    ЗАВЫШЕНИЯ: сравнение по границам слов вместо подстроки — «пер» больше не
    находится внутри «передать», «мира» внутри «командирами»."""
    return set(_ADDR_WORD_RE.findall(anon_norm_v2))


def _addr_run_set(anon_digit_field: str):
    """Множество ЦЕЛЫХ цифровых run-ов анонимного текста.  То же лечение для
    чисел: дом «12» больше не находится внутри года «2024»."""
    return set(anon_digit_field.split())


# Кэш производных множеств: leak_v2_address зовётся по разу на КАЖДУЮ эталонную
# сущность документа, а поля anon_* на документ одни и те же.  Без кэша разбор
# текста повторялся бы десятки раз на документ.
_ADDR_CACHE = {"words_key": None, "words": None, "runs_key": None, "runs": None}


def _addr_sets(anon_norm_v2: str, anon_digit_field: str):
    c = _ADDR_CACHE
    if c["words_key"] is not anon_norm_v2:
        c["words_key"], c["words"] = anon_norm_v2, _addr_word_set(anon_norm_v2)
    if c["runs_key"] is not anon_digit_field:
        c["runs_key"], c["runs"] = anon_digit_field, _addr_run_set(anon_digit_field)
    return c["words"], c["runs"]


def addr_alpha_survivor(tok: str, words):
    """Что от значимого токена `tok` дожило в множестве целых слов `words`.

    Возвращает выжившую форму (сам токен или его достаточно длинный префикс)
    либо None.  Префиксная ветка лечит ЗАНИЖЕНИЕ: спан, начатый/оборванный на
    символ раньше, оставляет «екатеринбур» открытым текстом — город утёк, и
    метрика обязана это увидеть, хотя целого токена в тексте нет."""
    if tok in words:
        return tok
    lim = max(ADDR_PREFIX_MIN, int(-(-len(tok) * 3 // 4)))  # ceil(0.75*len)
    for w in words:
        if len(w) >= lim and len(w) < len(tok) and tok.startswith(w):
            return w
    return None


def leak_v2_address(gtext: str, anon_norm_v2: str, anon_digit_field: str):
    """Частичная утечка ADDRESS покомпонентно, ПО ГРАНИЦАМ СЛОВ.

    Утечка адреса, если дожил хоть один ЗНАЧИМЫЙ компонент:
      - почтовый индекс (ровно 6 цифр эталона) как ЦЕЛЫЙ цифровой run, ИЛИ
      - значимый топоним/улица (>= ADDR_ALPHA_MIN букв, не маркер) целым словом
        ИЛИ достаточно длинным префиксом (усечение спана), ИЛИ
      - связка «значимый топоним + номер дома» (частный случай предыдущего,
        сохранён явно ради читаемости фрагментов).
    НЕ утечка: голый номер дома/квартиры без топонима рядом — 438 из 447
    адресов-одиночек этапа 0b-fix это именно голые номера, они никого не
    идентифицируют (вывод подтверждён на данных, не пересматривается).

    Два лечения относительно версии этапа 0b (обе стороны вранья прибора):
      ЗАВЫШЕНИЕ — сравнение было ПОДСТРОЧНЫМ: «пер» находился внутри «передать»,
        «12» внутри «2024», и полностью замаскированный адрес рапортовался
        утёкшим ВЕЧНО (0c-H/Stage2-N).  Теперь сравнение по границам слов.
      ЗАНИЖЕНИЕ — токен сверялся ЦЕЛИКОМ: усечённый выживший «екатеринбур»
        метрика не видела и говорила «не утекло».  Теперь есть префиксная ветка.

    Возвращает status/fragments; числовой порог не применяется (адрес — не
    число в окне)."""
    res = {"status": "none", "fragments": []}
    fragments = []
    words, runs = _addr_sets(anon_norm_v2, anon_digit_field)

    # Блоки цифр эталона по отдельности (индекс/дом/корпус/квартира).
    # Свод «буква-на-месте-цифры» применяется, но блок засчитывается ТОЛЬКО если
    # в нём есть хоть одна НАСТОЯЩАЯ цифра: иначе заглавный буквенный текст
    # («ОБЛ.», «ПРОСП.») порождает блоки-призраки '0'/'3'/'6' из О/З/Б и
    # даёт фантомный «номер дома», которого в адресе нет.
    digit_blocks = []
    cur = []
    cur_real = False
    raw = _v2_strip_invisible(gtext)
    for ch in raw:
        folded = _V2_LETTER2DIGIT.get(ch, ch)
        if folded.isdigit():
            cur.append(folded)
            cur_real = cur_real or ch.isdigit()
        else:
            if cur and cur_real:
                digit_blocks.append("".join(cur))
            cur, cur_real = [], False
    if cur and cur_real:
        digit_blocks.append("".join(cur))

    index_survived = False
    house_survived = False
    house_fragments = []
    for b in digit_blocks:
        if b not in runs:          # ЦЕЛЫЙ run, а не подстрока (лечение завышения)
            continue
        if len(b) == 6:
            index_survived = True
            fragments.append(b)
        elif 1 <= len(b) <= 4:
            house_survived = True
            house_fragments.append(b)

    alpha_survived = []
    for t in alpha_tokens_v2(gtext, ADDR_ALPHA_MIN):
        if t in _V2_ADDR_STOP:
            continue
        s = addr_alpha_survivor(t, words)
        if s is not None:
            alpha_survived.append(s)

    leaked = index_survived or bool(alpha_survived)
    if alpha_survived:
        fragments += alpha_survived
        if house_survived:
            # связка «улица/нас.пункт + дом» — номер дома значим только рядом
            # с топонимом, поэтому в фрагменты он попадает только здесь.
            fragments += house_fragments
    if leaked:
        res["status"] = "partial"
        # «full», если выжило всё: и индекс, и улица, и дом — но для адреса
        # различие partial/full малосодержательно, оставляем partial.
        res["fragments"] = sorted(set(fragments))
    return res


def alpha_tokens_v2(s: str, min_len: int = 3):
    """Буквенные токены (>= min_len) из v2-нормы — для адреса/ORG на пороге 3."""
    s = v2_norm_text(s)
    toks = []
    cur = []
    for ch in s:
        if ch.isalpha():
            cur.append(ch)
        else:
            if cur:
                toks.append("".join(cur))
                cur = []
    if cur:
        toks.append("".join(cur))
    return [t for t in toks if len(t) >= min_len]


def v2_date_field(s: str) -> str:
    """Цифровое поле текста для сравнения ДАТ.  Отличие от v2_digit_runs: между
    цифрами склеиваются НЕ только пробел/дефис, но и точка/слэш/перенос строки —
    «10.07.1996» это ОДНО значение (дата), а не три независимых числа.

    Зачем отдельное поле (диагноз этапа 0b-fix, подтверждён на данных):
    v2_digit_runs («0 10 07 1996») рвёт дату по точкам, и самое длинное окно ядра
    «10071996» в таком поле — «1996» (4 цифры), ниже мягкого порога 6.  Поэтому
    дату НЕЛЬЗЯ мерить общей числовой веткой: она дала бы ~0% утечки на типе,
    который утекает всегда.  Отсюда — своё поле и своя ветка leak_v2_birthdate.

    Склейка через перенос строки обязательна: корпус намеренно мутирует даты
    переносом внутри значения («15\\n.11.1996»), и метрика должна видеть утечку."""
    s = _v2_strip_invisible(s)
    s = "".join(_V2_LETTER2DIGIT.get(ch, ch) for ch in s)
    out = []
    prev_digit = False
    for ch in s:
        if ch.isdigit():
            out.append(ch)
            prev_digit = True
        elif prev_digit and (ch.isspace() or ch in "-./"):
            # разделитель ВНУТРИ даты/числа — склеиваем
            continue
        else:
            if prev_digit:
                out.append(" ")
            prev_digit = False
    return " ".join("".join(out).split())


def leak_v2_birthdate(gtext: str, anon_date_field: str):
    """Утечка ДАТЫ РОЖДЕНИЯ (этап 0b-fix).

    В корпусе BIRTHDATE встречается в единственной форме — DD.MM.YYYY (с
    мутациями: невидимые, омоглифы-буквы на месте цифр, переносы, пробелы внутри
    значения).  Правило по ЗАДАНИЮ: дата утекла, если выжила связка
    день+месяц+год, т.е. ПОЛНАЯ числовая дата.  Ядро = все цифры эталона подряд
    (для DD.MM.YYYY это ровно day+month+year), поле — v2_date_field анонимного
    текста.  Правил сверх этого не изобретаем: частичного статуса у даты нет —
    выживший «1996» никого не идентифицирует, а полная дата идентифицирует.

    Возвращает full (ядро дожило целиком) либо none."""
    core = v2_core_digits(gtext)
    res = {"status": "none", "fragments": [], "core_len": len(core)}
    if not core:
        return res
    if core in anon_date_field:
        res["status"] = "full"
        res["fragments"] = [core]
    return res


def leak_v2_email(gtext: str, anon_norm_v2: str):
    """Частичная утечка EMAIL: выжила локальная часть ИЛИ домен."""
    res = {"status": "none", "fragments": []}
    n = v2_norm_text(gtext).replace(" ", "")
    hay = anon_norm_v2.replace(" ", "")
    if not n:
        return res
    if n in hay:
        res["status"] = "full"
        res["fragments"] = [n]
        return res
    if "@" in n:
        local, _, domain = n.partition("@")
        survived = [p for p in (local, domain) if len(p) >= 2 and p in hay]
        if survived:
            res["status"] = "partial"
            res["fragments"] = survived
    return res


# =========================================================================== #
#   masking_correctness — КОРРЕКТНОСТЬ ТОГО, ЧТО СИСТЕМА УЖЕ ЗАМАСКИРОВАЛА     #
# =========================================================================== #
# ТРЕБОВАНИЕ ПРОДУКТА (асимметрия ошибок).  После автоматической шифрации документ
# проверяет ЧЕЛОВЕК.  Он ищет, не осталось ли ЛИШНЕГО (утечка) — и НЕ проверяет,
# правильно ли спрятано уже спрятанное.  Поэтому:
#   пропуск  (сущность не замаскирована) — терпимо, человек поймает;
#   кривая маскировка                    — недопустима, проходит проверку насквозь.
# Отсюда метрика с ДРУГИМ ЗНАМЕНАТЕЛЕМ, чем recall/leak: там знаменатель — gold
# («сколько поймали»), здесь — МАСКИ, поставленные системой («из пойманного,
# сколько корректно»).  Путать нельзя: рост recall не улучшает эти проценты
# автоматически, а этап, который начнёт маскировать криво ради recall, обязан
# уронить именно их.
#
# Три уровня:
#   A. ROUND-TRIP (жёстко) — замаскированное значение восстанавливается decrypt'ом
#      БАЙТ-В-БАЙТ.  Знаменатель: все маски.
#   B. ГРАНИЦЫ (жёстко) — эталонный спан закрыт масками ЦЕЛИКОМ, не наполовину.
#      Знаменатель: маски, легшие хоть на одну эталонную сущность (маске, не
#      попавшей ни в одну gold, покрывать нечего — это ложное срабатывание, его
#      меряет FP-метрика, и в знаменатель B/C она не идёт).
#   C. ТИП (мягко, информационно) — тип маски совпал с эталонным типом.
#      Знаменатель тот же, что у B.
#
# ПОЧЕМУ A СЧИТАЕТСЯ ДВУМЯ ЧИСЛАМИ (a_ok и a_channel_ok) — не дублирование:
#   a_ok         — вернулось ли РОВНО исходное значение сущности (требование продукта);
#   a_channel_ok — совпал ли восстановленный участок с тем, что стояло на этом месте
#                  в plain-сборке документа.
# Они расходятся ровно там, где сама plain-сборка лоссова независимо от маскировки:
# tokenizer._render_table заменяет '\n' внутри ячейки таблицы на пробел ПОСЛЕ
# подстановки, поэтому значение, содержащее перенос строки внутри ячейки, в plain
# стоит с пробелом, а detokenize возвращает его с исходным '\n'.  Значение при этом
# восстановлено ВЕРНЕЕ оригинала документа, а не испорчено.  Считать это нарушением
# A было бы подгонкой метрики под артефакт текстового канала; считать это «всё
# хорошо» молча — потерей факта.  Поэтому два числа, и гейт жёсток по a_ok.
# Замены '\n'->' ' длину не меняют, поэтому координаты plain и restored совпадают
# символ в символ, пока len(restored)==len(plain) (это проверяется явно, см. aligned).


def plain_segment_offsets(doc):
    """(plain_text, {segment_id: offset}) — сборка plain-текста ПОВТОРЯЕТ
    tokenizer._assemble/_render_table, но попутно запоминает смещение каждого
    сегмента.  Смещения нужны, чтобы перевести локальные координаты Entity в
    координаты plain/restored и проверить A поштучно.

    Вызывающий ОБЯЗАН сверить возвращённый текст с build_plain_text(doc): если
    реализации разойдутся (сборка в src/ изменится), смещения станут враньём, и
    метрика A обязана умереть громко, а не тихо мерить не то.  Замена '\\n'->' '
    в ячейках длину не меняет, поэтому локальные оффсеты сущности внутри ячейки
    остаются валидными."""
    segs = doc.segments
    n = len(segs)
    units = []
    offsets = {}
    pos = 0  # смещение начала текущей верхнеуровневой единицы

    def _emit(unit_text, seg_offsets):
        nonlocal pos
        for sid, local in seg_offsets:
            offsets[sid] = pos + local
        units.append(unit_text)
        pos += len(unit_text) + 1  # '\n' между единицами

    i = 0
    while i < n:
        seg = segs[i]
        if seg.source_type == "docx_table_cell":
            table_index = seg.metadata["table_index"]
            cells = []
            while (i < n
                   and segs[i].source_type == "docx_table_cell"
                   and segs[i].metadata["table_index"] == table_index):
                cells.append(segs[i])
                i += 1
            rows = {}
            for c in cells:
                rows.setdefault(c.metadata["row_index"], []).append(c)
            row_strs = []
            seg_offsets = []
            tpos = 0
            for r in sorted(rows):
                row_cells = sorted(rows[r], key=lambda c: c.metadata["col_index"])
                cpos = tpos
                cell_strs = []
                for k, c in enumerate(row_cells):
                    if k:
                        cpos += 3  # ' | '
                    seg_offsets.append((c.id, cpos))
                    ct = c.text.replace("\n", " ")
                    cell_strs.append(ct)
                    cpos += len(ct)
                row_strs.append(" | ".join(cell_strs))
                tpos = cpos + 1  # '\n' между строками таблицы
            _emit("\n".join(row_strs), seg_offsets)
        else:
            _emit(seg.text, [(seg.id, 0)])
            i += 1

    return "\n".join(units), offsets


def mc_check_a(pstart, original_text, restored, plain, aligned=True):
    """A для ОДНОЙ маски.  pstart — смещение сущности в plain-координатах.

    Возвращает {a_ok, a_channel_ok, reason}:
      ok               — значение вернулось байт-в-байт и совпало с plain;
      channel_ws_only  — значение вернулось байт-в-байт, но в plain на этом месте
                         стоял схлопнутый пробел (артефакт сборки, не порча);
      value_corrupted  — значение вернулось НЕ таким, каким было (нарушение A);
      unaligned        — длины restored и plain разошлись, координаты недействительны;
                         считаем нарушением КОНСЕРВАТИВНО (защиту доказать не смогли).
    """
    if not aligned:
        return {"a_ok": False, "a_channel_ok": False, "reason": "unaligned"}
    pend = pstart + len(original_text)
    r = restored[pstart:pend]
    p = plain[pstart:pend]
    a_ok = (r == original_text)
    ch_ok = (r == p)
    if a_ok and ch_ok:
        reason = "ok"
    elif a_ok:
        reason = "channel_ws_only"
    else:
        reason = "value_corrupted"
    return {"a_ok": a_ok, "a_channel_ok": ch_ok, "reason": reason}


def _iv_overlap(a0, a1, b0, b1):
    return max(a0, b0) < min(a1, b1)


def _covered_by_union(gs, ge, spans):
    """Покрыт ли [gs,ge) объединением интервалов spans (список (s,e))."""
    cur = gs
    for s, e in sorted(spans):
        if s > cur:
            return False
        cur = max(cur, e)
        if cur >= ge:
            return True
    return cur >= ge


def mc_check_bc(mask, golds, all_masks):
    """B и C для ОДНОЙ маски.  mask/golds/all_masks — dict'ы со start/end и
    типом (`gtype` у масок, `type` у gold), координаты общие (PT-1).

    Эталонная сущность маски — та, с которой у неё НАИБОЛЬШЕЕ пересечение.
    B считается по ОБЪЕДИНЕНИЮ всех масок, накрывающих эту эталонную сущность:
    значение, закрытое двумя соседними масками, спрятано полностью — это НЕ брак.
    Брак — когда объединение оставляет кусок эталона открытым (половина счёта под
    маской, половина открытым текстом: разрыв через перенос строки/границу ячейки).

    ВАЖНО: маска ШИРЕ эталона (over-capture, напр. PASSPORT) по B НЕ нарушение —
    B про «не половина», а не про «ровно». Точность спана меряет recall_exact,
    это другая метрика и другое требование.

    Возвращает {scored, b_ok, c_ok, gold_type, mask_type}. scored=False — маска не
    легла ни на одну эталонную сущность (ложное срабатывание): B/C неприменимы,
    в знаменатель не идёт."""
    ms, me = mask["start"], mask["end"]
    ov = [g for g in golds if _iv_overlap(ms, me, g["start"], g["end"])]
    if not ov:
        return {"scored": False, "b_ok": None, "c_ok": None,
                "gold_type": None, "mask_type": mask["gtype"]}
    g = max(ov, key=lambda g: min(me, g["end"]) - max(ms, g["start"]))
    gs, ge = g["start"], g["end"]
    covering = [(m["start"], m["end"]) for m in all_masks
                if _iv_overlap(m["start"], m["end"], gs, ge)]
    return {
        "scored": True,
        "b_ok": _covered_by_union(gs, ge, covering),
        "c_ok": (mask["gtype"] == g["type"]),
        "gold_type": g["type"], "mask_type": mask["gtype"],
    }


def _empty_mc():
    return {"n_masks": 0, "a_ok": 0, "a_channel_ok": 0,
            "n_scored": 0, "b_ok": 0, "c_ok": 0}


def mc_aggregate(mask_records):
    """Сводка masking_correctness из плоского списка записей маски (поле каждой:
    gtype, a_ok, a_channel_ok, scored, b_ok, c_ok).  Возвращает
    {total: {...}, per_type: {gtype: {...}}}."""
    total = _empty_mc()
    per_type = {}
    for r in mask_records:
        t = r["gtype"]
        s = per_type.setdefault(t, _empty_mc())
        for d in (total, s):
            d["n_masks"] += 1
            d["a_ok"] += int(r["a_ok"])
            d["a_channel_ok"] += int(r["a_channel_ok"])
            if r["scored"]:
                d["n_scored"] += 1
                d["b_ok"] += int(r["b_ok"])
                d["c_ok"] += int(r["c_ok"])
    return {"total": total, "per_type": per_type}


def mc_rates(stats):
    """(A%, B%, C%) из блока статистики mc_aggregate.  Пустой знаменатель -> 100.0
    (нечего маскировать — нечего испортить); гейт сравнивает АБСОЛЮТНЫЕ доли,
    поэтому важно, чтобы пустой случай не давал 0% и не выглядел регрессом."""
    a = (100.0 * stats["a_ok"] / stats["n_masks"]) if stats["n_masks"] else 100.0
    b = (100.0 * stats["b_ok"] / stats["n_scored"]) if stats["n_scored"] else 100.0
    c = (100.0 * stats["c_ok"] / stats["n_scored"]) if stats["n_scored"] else 100.0
    return a, b, c


# =========================================================================== #
#         Агрегация по типам для регресс-гейта (этап 0d, tests/corpus/gate.py) #
# =========================================================================== #
# 15 типов сущностей корпуса (README §«Правила разметки»).  Список ФИКСИРОВАН
# и полон, даже если в конкретном прогоне у типа 0 сущностей: гейт обязан
# видеть все 15 колонок, а не только те, что встретились (иначе тип, у
# которого детектор полностью сломался и recall упал до 0, может тихо
# выпасть из diff-таблицы вместо того, чтобы показать явный regress).
#
# SUM — 14-й, добавлен этапом T-GOLD-A: 648 денежных сумм переклассифицированы
# из негативов в эталонные сущности (суммы маскируются по решению владельца,
# детектор `SUM` в entity_types.yaml есть и включён).  Без записи в этом списке
# тип не получил бы собственных колонок recall/leak/masking, и гейт судил бы о
# нём только по агрегату — то есть был бы слеп к обвалу целого типа, ровно
# против чего список и заведён.
#
# INN_PER — 15-й, добавлен этапом T2-INN: ИНН физического лица (12 цифр)
# отделён от ИНН организации (10 цифр), потому что первый — персональные данные,
# а второй нет.  Записи эталона с 10 цифрами при этом не тронуты, так что колонка
# INN остаётся сравнимой с прошлыми замерами, а INN_PER — новая колонка, у
# которой прошлого нет.  Без записи здесь новый тип не получил бы ни собственных
# recall/leak/masking, ни строки в линиях гейта — то есть был бы НЕ ОХРАНЯЕМ.
#
# PERCENT и TERM — 16-й и 17-й, добавлены этапом T2 (детекторы процента и срока).
# В ЭТОМ (старом) корпусе их эталонных сущностей НЕТ ни одной: проценты и сроки
# лежат в нём неразмеченной прозой, а измеримый эталон по ним есть только в
# корпусе v2 (tests/corpus_v2). Записать их сюда всё равно ОБЯЗАТЕЛЬНО, и
# именно потому, что эталона нет: без записи маски новых типов не получили бы
# СВОЕЙ строки ни в линии «д» (over-mask прозы — а тут они и дают весь свой
# вклад), ни в линии «а» (precision), ни в «е» (границы) — то есть новый тип
# мог бы залить старый корпус масками, оставаясь невидимым по имени и
# растворяясь в агрегате. Тип без строки не охраняется.
ALL_ENTITY_TYPES = (
    "PER", "ORG", "ADDRESS", "INN", "INN_PER", "OGRN", "KPP", "ACCOUNT", "BIK",
    "PHONE", "EMAIL", "PASSPORT", "SNILS", "BIRTHDATE", "SUM", "PERCENT", "TERM",
)


def _leak_v2_hits(entity_type: str, v2: dict):
    """(прошла ли утечка мягкий порог >=6, прошла ли строгий порог >=8).

    Числовые типы (INN/OGRN/KPP/ACCOUNT/BIK/PHONE/PASSPORT/SNILS): запись v2
    уже посчитана run_measurement'ом через leak_v2_numeric(strict=8, soft=6),
    который АДАПТИРУЕТ строгий порог вниз для коротких ядер
    (thr_strict = min(8, core_len)) — иначе короткое ядро (например 6-значный
    код подразделения паспорта), выжившее ЦЕЛИКОМ, никогда не набрало бы 8 и
    несправедливо не засчиталось бы «строгой» утечкой.  Здесь воспроизводим
    ТУ ЖЕ адаптацию по сохранённым window_len/core_len, а не по фиксированному
    literal >=8 — иначе агрегат разойдётся с тем, что реально пишет
    leak_v2_numeric (проверено на baseline: literal >=8 даёт 39.8% BIK-excl
    вместо верных 43.0%, см. HANDOFF_STAGE_0C.md/HANDOFF_STAGE_0D.md).

    BIRTHDATE: частичной утечки у даты не существует (leak_v2_birthdate отдаёт
    только full/none) — оба порога совпадают со status=='full'.

    PER/ORG/ADDRESS/EMAIL: утечка — статус без числового окна (буквенный
    токен/компонент либо выжил, либо нет) — оба порога совпадают с
    status!='none'."""
    status = v2["status"]
    if entity_type == "BIRTHDATE":
        hit = status == "full"
        return hit, hit
    window_len = v2.get("window_len")
    core_len = v2.get("core_len")
    if window_len is not None and core_len is not None:
        hit_soft = status != "none"
        hit_strict = hit_soft and window_len >= min(8, core_len)
        return hit_soft, hit_strict
    hit = status != "none"
    return hit, hit


def _empty_type_stats():
    return {"n": 0, "found": 0, "leak_v1": 0, "leak_v2_6": 0, "leak_v2_8": 0}


def aggregate_results(results):
    """Сводка по типам сущностей для регресс-гейта из списка записей
    run_measurement.process_doc() (outcome processed|crashed).

    Возвращает dict:
      crashed          — [doc_id, ...] упавших документов
      n_docs           — всего документов в results
      per_type         — {type: {n, found, leak_v1, leak_v2_6, leak_v2_8}},
                          ключи — ВСЕ ALL_ENTITY_TYPES, даже с n=0
      total_all        — сумма per_type по всем типам, BIK включён
      total_bik_excl   — та же сумма, BIK ИСКЛЮЧЁН (см. BASELINE.md §1: общий
                          для банков префикс БИК выживает почти всегда и
                          никого не идентифицирует — включать его в общий
                          agregate — шум).  Per-type регресс по BIK гейт всё
                          равно ловит: BIK есть в per_type наравне с прочими.
      fp_on_neg         — {type: count} ложных срабатываний, легших на
                          размеченный негатив (gold['negatives'])
      fp_total          — {type: count} ВСЕХ ложных срабатываний (включая
                          неразмеченную прозу) — информационно, гейт по нему
                          не падает (только по fp_on_neg_total)
      fp_on_neg_total, fp_total_total — суммы по всем типам

    Документы outcome!='processed' (креш) в per_type/total/fp НЕ участвуют —
    их сущности не измерены (см. gate.py условие 1: сам факт креша роняет
    гейт отдельно, порог 0, независимо от leak-чисел)."""
    crashed = [r["doc_id"] for r in results if r.get("outcome") != "processed"]
    mask_records = []
    per_type = {t: _empty_type_stats() for t in ALL_ENTITY_TYPES}
    fp_on_neg = {t: 0 for t in ALL_ENTITY_TYPES}
    fp_total = {t: 0 for t in ALL_ENTITY_TYPES}

    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            t = e["type"]
            s = per_type.setdefault(t, _empty_type_stats())
            s["n"] += 1
            s["found"] += int(e["found"])
            s["leak_v1"] += int(e["leak_v1"])
            hit6, hit8 = _leak_v2_hits(t, e["leak_v2"])
            s["leak_v2_6"] += int(hit6)
            s["leak_v2_8"] += int(hit8)
        mask_records.extend(r.get("masks", []))
        for f in r.get("false_positives", []):
            gt = f["gtype"]
            fp_total[gt] = fp_total.get(gt, 0) + 1
            if f.get("on_negative") is not None:
                fp_on_neg[gt] = fp_on_neg.get(gt, 0) + 1

    def _sum(exclude=()):
        agg = _empty_type_stats()
        for t, s in per_type.items():
            if t in exclude:
                continue
            for k in agg:
                agg[k] += s[k]
        return agg

    return {
        "crashed": crashed,
        "n_docs": len(results),
        "n_docs_processed": len(results) - len(crashed),
        "per_type": per_type,
        "total_all": _sum(),
        "total_bik_excl": _sum(exclude={"BIK"}),
        "fp_on_neg": fp_on_neg,
        "fp_total": fp_total,
        "fp_on_neg_total": sum(fp_on_neg.values()),
        "fp_total_total": sum(fp_total.values()),
        # masking_correctness: знаменатель — МАСКИ системы, не gold (см. блок выше).
        # Старые results_*.json поля "masks" не содержат — тогда блок пуст, и гейт
        # честно печатает «нет данных» вместо выдуманных 100%.
        "masking_correctness": mc_aggregate(mask_records),
    }


# =========================================================================== #
#            ЭТАП D — метрика PRECISION NER (единая FP-дефиниция)              #
# =========================================================================== #
# Precision в ЕДИНОЙ дефиниции разбора C (HANDOFF_STAGE_C §9.1): FP = kept-МАСКА
# (после разрешения пересечений в tokenize, НЕ сырой спан), пересекающая
# ОБЪЯВЛЕННЫЙ негатив (gold['negatives']); TP = kept-маска на gold-сущности СВОЕГО
# типа.  precision_T = TP_T / (TP_T + FP_neg_T).  Уровень — kept-маска, набор — весь
# корпус.  Совпадает по FP с aggregate_results()['fp_on_neg'] (итого 650 на этапе C).
#
# ПОЧЕМУ это НЕ masking_correctness.  masking_correctness B/C считаются ТОЛЬКО по
# маскам, легшим на gold (mc_check_bc: scored=False → в знаменатель не идёт), то есть
# СЛЕПЫ к over-маскированию: маска на негативе/прозе в masking_correctness не
# существует.  Поэтому «masking C = 90.81%» — НЕ precision (см. FINDINGS «90%
# фикция»); честный precision считает ИМЕННО ложные размещения масок.
#
# ДИАГНОСТИКА (не в precision, не в гейт): cross — маска на gold ДРУГОГО типа (ошибка
# ТИПА, домен masking C); nothing — маска на НЕАННОТИРОВАННОЙ прозе (over-mask,
# on-nothing, неоднозначный набор).  Считаются отдельно, чтобы over-маскирование было
# ВИДНО в отчёте, но не роняло precision молча и не входило в гейт.

# Типы-хребет NER этапа D (по ним precision — гейтовый). Прочие (regex-реквизиты, SUM)
# считаются тоже — для полноты таблицы, — но гейт precision держит на ORG/PER/ADDRESS.
NER_SPINE_TYPES = ("ORG", "PER", "ADDRESS")


def _empty_prec():
    return {"tp": 0, "fp_neg": 0, "cross": 0, "nothing": 0}


def precision_by_type(results):
    """Precision + диагностика по типам из списка записей run_measurement.process_doc.

    Возвращает {"per_type": {T: {tp, fp_neg, cross, nothing, precision}},
                "fp_neg_total", "cross_total", "nothing_total"}.
    precision = None, если знаменатель (tp+fp_neg) пуст (нет ни одной маски-решения
    по типу — считать «нет данных», а не 100%)."""
    per = {t: _empty_prec() for t in ALL_ENTITY_TYPES}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for m in r.get("masks", []):
            d = per.setdefault(m["gtype"], _empty_prec())
            # TP: маска легла на gold СВОЕГО типа (scored=лёгла на gold, c_ok=тип совпал).
            if m.get("scored") and m.get("c_ok"):
                d["tp"] += 1
        for f in r.get("false_positives", []):
            d = per.setdefault(f["gtype"], _empty_prec())
            if f.get("on_negative") is not None:
                d["fp_neg"] += 1                 # FP гейтовый: маска на объявленном негативе
            elif f.get("cross_type") is not None:
                d["cross"] += 1                  # диагностика: маска на gold другого типа
            else:
                d["nothing"] += 1                # диагностика: over-mask неаннотированной прозы
    for t, d in per.items():
        denom = d["tp"] + d["fp_neg"]
        d["precision"] = (100.0 * d["tp"] / denom) if denom else None
    return {
        "per_type": per,
        "fp_neg_total": sum(d["fp_neg"] for d in per.values()),
        "cross_total": sum(d["cross"] for d in per.values()),
        "nothing_total": sum(d["nothing"] for d in per.values()),
    }


# =========================================================================== #
#     ЭТАП GATE-2 — ГРАНИЦЫ ПО НАПРАВЛЕНИЮ ОШИБКИ (линия «е» гейта)           #
# =========================================================================== #
# ЗАЧЕМ ОТДЕЛЬНАЯ МЕТРИКА, ЕСЛИ ЕСТЬ masking B И `exact`.  Ни одна штатная
# метрика не отвечает на вопрос «маска КОРОЧЕ эталона или ДЛИННЕЕ»:
#   * masking B («эталон закрыт масками целиком», mc_check_bc) видит ТОЛЬКО
#     недобор — маска ШИРЕ эталона по B нарушением не считается ВООБЩЕ,
#     поэтому перебор может расти неограниченно при зелёном гейте;
#   * `exact` — булево «ровно», направление промаха из него не извлекается;
#   * left_trim/right_trim считаются по ХАЛЛУ масок СВОЕГО типа и молчат о
#     случае «слева уехал внутрь, справа вылез наружу» (обе стороны сразу).
#
# Две цены названы раздельно и НАЗЫВАЮТСЯ ПО-РАЗНОМУ, потому что стоят разного:
#   НЕДОБОР (under) — символы эталона, не закрытые НИ ОДНОЙ маской (любого типа:
#                     соседний PER/ORG-токен тоже прячет значение). Это УТЕЧКА.
#   ПЕРЕБОР (over)  — символы масок СВОЕГО типа, лежащие ВНЕ своего эталона.
#                     Данные защищены, документ хуже читается. Это ПОРЧА.
# Правка границ легко улучшает одну сторону за счёт другой (этап ADDR-B), а
# гейт с одним числом такой размен принимает молча.
#
# ПРОВЕНАНС. Логика дословно перенесена из прибора этапа ADDR-B
# (experiments/stage_addr_b/addr_probe.py), который теперь зовёт ЭТИ функции —
# две реализации одной метрики разъехались бы, и «постоянная линия» перестала
# бы совпадать с прибором, которым этап её мерил.  Отличие ровно одно: прибор
# считал только ADDRESS, здесь метрика считается для ВСЕХ типов эталона.
#
# СОПОСТАВЛЕНИЕ МАСКА->ЭТАЛОН — то же правило, что у mc_check_bc: маска
# приписывается эталонной сущности с НАИБОЛЬШИМ пересечением. Маска, не
# пересёкшая ни одного эталона своего типа, в границы не идёт вообще — это
# ложное срабатывание, её меряют линии «а» (precision) и «д» (over-mask прозы).

def uncovered_pieces(gs, ge, spans):
    """Куски [gs,ge), НЕ покрытые ни одним интервалом spans -> [(s,e), ...]."""
    out = []
    cur = gs
    for s, e in sorted((s, e) for s, e in spans if _iv_overlap(s, e, gs, ge)):
        if s > cur:
            out.append((cur, min(s, ge)))
        cur = max(cur, e)
        if cur >= ge:
            break
    if cur < ge:
        out.append((cur, ge))
    return [(s, e) for s, e in out if e > s]


def outside_pieces(ms, me, gs, ge):
    """Куски маски [ms,me), лежащие ВНЕ эталона [gs,ge) -> [(s,e), ...]."""
    out = []
    if ms < gs:
        out.append((ms, min(me, gs)))
    if me > ge:
        out.append((max(ms, ge), me))
    return [(s, e) for s, e in out if e > s]


def boundary_by_gold(golds, masks_same_type, all_mask_spans):
    """Направление ошибки границ для КАЖДОЙ эталонной сущности одного типа.

    golds            — [{start, end, ...}] эталонные сущности ОДНОГО типа;
    masks_same_type  — [(s,e)] маски этого же типа (kept, координаты PT-1);
    all_mask_spans   — [(s,e)] ВСЕ маски документа (любого типа) — недобор
                       считается по ним: значение, закрытое чужой маской, не
                       утекло, и записывать это в недобор было бы неправдой.

    Возвращает список, ВЫРОВНЕННЫЙ с golds:
      {"under": int, "over": int, "direction": str}
      direction: not_found | exact | shorter | longer | both

    `direction` выводится ИЗ ДВУХ ЧИСЕЛ, а не из равенства спанов: `exact`
    означает «ничего не открыто и ничего лишнего не закрыто», в том числе
    когда своя маска короче эталона, но остаток закрыт СОСЕДНЕЙ маской —
    значение не утекло и документ не испорчен, платить не за что.

    `not_found` здесь — «ни одна маска СВОЕГО типа не приписана этой сущности»
    и может расходиться с полем `found` записи эталона: `found` смотрит на факт
    пересечения, а приписана маска может быть соседней сущности того же типа
    (пересечение с ней больше). Это не дефект: обе величины считают разное, и
    линия «е» судит только по своей.
    """
    assigned = {i: [] for i in range(len(golds))}
    for ms, me in masks_same_type:
        cand = [(i, g) for i, g in enumerate(golds)
                if _iv_overlap(ms, me, g["start"], g["end"])]
        if not cand:
            continue
        i, _g = max(cand, key=lambda p: min(me, p[1]["end"]) - max(ms, p[1]["start"]))
        assigned[i].append((ms, me))

    out = []
    for i, g in enumerate(golds):
        gs, ge = g["start"], g["end"]
        mine = assigned[i]
        under = sum(e - s for s, e in uncovered_pieces(gs, ge, all_mask_spans))
        over = 0
        for ms, me in mine:
            over += sum(e - s for s, e in outside_pieces(ms, me, gs, ge))
        if not mine:
            direction = "not_found"
        elif under == 0 and over == 0:
            direction = "exact"
        elif under == 0:
            direction = "longer"
        elif over == 0:
            direction = "shorter"
        else:
            direction = "both"
        out.append({"under": under, "over": over, "direction": direction})
    return out


def _empty_bnd():
    return {"n": 0, "assigned": 0, "exact": 0, "shorter": 0, "longer": 0,
            "under_chars": 0, "over_chars": 0,
            "not_found": 0, "not_found_chars": 0}


#: Ключ записи эталона, в котором run_measurement хранит результат boundary_by_gold.
BND_FIELD = "bnd"


def boundaries_by_type(results):
    """Сводка линии «е» из дампа прогона.

    Возвращает {"per_type": {T: {...}}, "total": {...}, "n_with_field": int,
                "n_entities": int}.  `shorter`/`longer` — ЧИСЛО СУЩНОСТЕЙ, у
    которых маска соответственно не дотянулась / вылезла (класс `both`
    считается в ОБА, потому что стоит и то, и другое); `under_chars`/
    `over_chars` — символы, и ТОЛЬКО по своему классу.

    ПОЧЕМУ СУЩНОСТИ БЕЗ МАСКИ СВОЕГО ТИПА (`not_found`) ВЫНЕСЕНЫ ОТДЕЛЬНО и НЕ
    входят в `under_chars`. У них открыт весь эталон, и если считать эти
    символы недобором, линия «е» начнёт двигаться от КАЖДОГО изменения recall —
    то есть будет мерить не то, что обещает («маска короче эталона»), а заодно
    и «маски вообще не было». Эти два дефекта стоят разного и чинятся разным;
    непойманное значение уже охраняют линия «б» (leak_v2) и известный долг.
    Счёт и открытые символы этого класса печатаются как контекст.

    n_with_field == 0 при дампе, снятом до появления линии: гейту важно
    отличить «нет промахов» от «нечем мерить» (иначе линия зелена молча)."""
    per_type = {t: _empty_bnd() for t in ALL_ENTITY_TYPES}
    n_with_field = n_entities = 0
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            n_entities += 1
            b = e.get(BND_FIELD)
            if not b:
                continue
            n_with_field += 1
            s = per_type.setdefault(e["type"], _empty_bnd())
            s["n"] += 1
            d = b["direction"]
            if d == "not_found":
                s["not_found"] += 1
                s["not_found_chars"] += b["under"]
                continue
            s["assigned"] += 1
            if d == "exact":
                s["exact"] += 1
            if d in ("shorter", "both"):
                s["shorter"] += 1
                s["under_chars"] += b["under"]
            if d in ("longer", "both"):
                s["longer"] += 1
                s["over_chars"] += b["over"]
    total = _empty_bnd()
    for s in per_type.values():
        for k in total:
            total[k] += s[k]
    return {"per_type": per_type, "total": total,
            "n_with_field": n_with_field, "n_entities": n_entities}
