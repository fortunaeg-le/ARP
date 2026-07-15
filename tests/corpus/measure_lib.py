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
    "OGRN": "OGRN",
    "KPP": "KPP",
    "BANK_ACCOUNT": "ACCOUNT",
    "BIK": "BIK",
    "PASSPORT": "PASSPORT",
    "PHONE": "PHONE",
    "EMAIL": "EMAIL",
    "SUM": "SUM",          # в gold типа SUM нет — суммы это негативы
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


def longest_surviving_window(core: str, field: str) -> str:
    """Самое длинное непрерывное окно `core`, встречающееся как подстрока `field`.
    `field` содержит пробелы-разделители между независимыми числами, поэтому окно
    физически не может пересечь границу двух чисел.  core короткий (<=25) —
    перебор O(L^2) дёшев."""
    best = ""
    L = len(core)
    for i in range(L):
        # длиннее текущего best уже нет смысла искать короче
        for j in range(L, i + len(best), -1):
            w = core[i:j]
            if w in field:
                if len(w) > len(best):
                    best = w
                break
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
}


def leak_v2_address(gtext: str, anon_norm_v2: str, anon_digit_field: str):
    """Частичная утечка ADDRESS покомпонентно.  Утечка, если:
      - выжил почтовый индекс (6 цифр эталона), ИЛИ
      - выжила связка «значимый буквенный токен + номер дома/строения».
    Значимый токен = буквенный токен >=3 символов не из служебного стоп-списка
    (город/улица/нас.пункт).  Номер дома = короткий цифровой run эталона (1..4).
    Возвращает status/fragments; порог не применяется (адрес — не число в окне)."""
    res = {"status": "none", "fragments": []}
    fragments = []
    # почтовый индекс: любой ровно-6-значный цифровой блок эталона, выживший как
    # непрерывное окно в цифровом поле анонимного текста.
    core = v2_core_digits(gtext)  # общий поток цифр адреса не годится — нужны блоки
    # блоки цифр эталона по отдельности (индекс/дом/корпус)
    digit_blocks = []
    cur = []
    src = "".join(_V2_LETTER2DIGIT.get(ch, ch) for ch in _v2_strip_invisible(gtext))
    for ch in src:
        if ch.isdigit():
            cur.append(ch)
        else:
            if cur:
                digit_blocks.append("".join(cur))
                cur = []
    if cur:
        digit_blocks.append("".join(cur))

    index_survived = False
    house_survived = False
    for b in digit_blocks:
        if b not in anon_digit_field:
            continue
        if len(b) == 6:
            index_survived = True
            fragments.append(b)
        elif 1 <= len(b) <= 4:
            house_survived = True
            fragments.append(b)

    alpha_survived = [
        t for t in alpha_tokens_v2(gtext, 3)
        if t not in _V2_ADDR_STOP and t in anon_norm_v2
    ]

    leaked = False
    if index_survived:
        leaked = True
    if house_survived and alpha_survived:
        leaked = True
        fragments += alpha_survived
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
