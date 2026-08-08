"""Этап 1a — Обнаружение непрочитанных зон .docx.

extractor.py читает ТОЛЬКО тело word/document.xml, и только его верхний уровень:
`document.element.body.iterchildren()` -> w:p и w:tbl. Всё остальное текстовое
содержимое пакета молча не доходит до детекторов и не попадает в анонимный текст:
пользователь получает документ без куска договора и НЕ УЗНАЁТ об этом. Замер
корпуса это подтвердил — recall по in_header/in_footer/in_footnote/in_textbox/
nested_table ровно 0%.

Этот модуль зоны НЕ читает (это этап 6) — он их ЧЕСТНО ОБНАРУЖИВАЕТ, чтобы
вызывающий код (блок 7, cmd_encrypt) мог громко отказаться работать вместо тихой
потери текста.

Почему zip+lxml, а НЕ python-docx: python-docx и есть источник слепоты —
paragraph.text не спускается в w:txbxContent, а Document(path) вообще не открывает
header*/footer*/footnotes. Проверять полноту чтения инструментом, чья неполнота
проверяется, — заведомо ложноотрицательный результат.

ПРИНЦИП ПОЛНОТЫ: ложноотрицательный ответ («зон нет», когда они есть) недопустим —
это молча утёкший кусок договора. Ложноположительный (лишняя зона) терпим — он
приводит к отказу или к предупреждению, но не к тихой потере. Все спорные развилки
здесь решены в сторону «лучше сообщить лишнее».

Зависимости: ooxml_core (блок 8) для чтения ZIP, lxml.
"""

from dataclasses import dataclass, field

from ooxml_core import parse_xml, read_zip_parts

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_T = _W + "t"
_W_P = _W + "p"
_W_R = _W + "r"
_W_TR = _W + "tr"
_W_TC = _W + "tc"
_W_TBL = _W + "tbl"
_W_BODY = _W + "body"
_W_TXBX_CONTENT = _W + "txbxContent"
_W_FOOTNOTE = _W + "footnote"
_W_ENDNOTE = _W + "endnote"
_W_COMMENT = _W + "comment"
_W_ID = _W + "id"

# Части, которые extractor читает (тело и колонтитулы — этап NODES). Внутри них
# зоной объявляются только непрочитанные подузлы; все ОСТАЛЬНЫЕ части не читаются
# целиком и объявляются зоной целиком.
_DOCUMENT_PART = "word/document.xml"


# --- ПЕРЕПИСЬ УЗЛОВ (этап NODES) --------------------------------------------
#
# ЗАКРЫТЫЙ СПИСОК. Наборы ниже — единственное место, где записано, какие узлы
# OOXML система умеет обходить. Их читает И сканер зон (этот модуль), И
# извлечение текста (`extractor.py` импортирует их отсюда) — двух копий нет по
# построению, поэтому читатель и сторож не могут разойтись.
#
# Узел, которого нет ни в одном наборе своего уровня, становится зоной
# `unknown_node` — то есть приводит к ОТКАЗУ в strict-режиме, а не к тихому
# пропуску. Это и есть замок на будущее: новый вид узла в чужом документе даёт
# отказ. Полная таблица переписи с вердиктами — docs/ARCHITECTURE.md §10.
#
# Полнота обхода при этом НЕ доказывается самим списком: после обхода сканер
# сверяет, что каждый w:t части был достигнут читателем (см. _scan_readable_part).
# Список говорит «куда идти», сверка — «ничего ли не осталось».

def _w(*names: str) -> frozenset:
    return frozenset(_W + n for n in names)


# Маркеры диапазонов (закладки, примечания, защита, рецензирование, customXml).
# Собственного видимого текста не несут, встречаются на любом уровне.
_MARKERS = _w(
    "bookmarkStart", "bookmarkEnd", "proofErr",
    "commentRangeStart", "commentRangeEnd",
    "permStart", "permEnd",
    "moveFromRangeStart", "moveFromRangeEnd",
    "moveToRangeStart", "moveToRangeEnd",
    "customXmlInsRangeStart", "customXmlInsRangeEnd",
    "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
)

# Верхний уровень тела / колонтитула: во что спускаемся ради блоков.
BLOCK_DESCEND = _w("sdt", "sdtContent", "customXml")
# Верхний уровень тела / колонтитула: что пропускаем осознанно.
BLOCK_IGNORE = _MARKERS | _w("sectPr", "sdtPr", "sdtEndPr", "customXmlPr")

# Внутри ячейки таблицы читатель (python-docx `_Cell.paragraphs`) видит ТОЛЬКО
# прямых детей w:p. Поэтому набор спуска здесь пуст — сканер обязан повторять
# ограничения читателя, а не свои представления о разметке.
CELL_IGNORE = BLOCK_IGNORE | _w("tcPr")

# Уровень таблицы и строки. Читатель идёт python-docx'ом: w:tbl -> прямые w:tr
# -> прямые w:tc. Обёртки строк/ячеек (w:sdt, w:customXml, w:ins) он не видит,
# поэтому и здесь их нет — они станут unknown_node и вызовут отказ.
TABLE_IGNORE = _MARKERS | _w("tblPr", "tblGrid", "tblPrEx")
ROW_IGNORE = _MARKERS | _w("trPr", "tblPrEx")

# Внутри абзаца: обёртки, сквозь которые читатель добирается до ранов.
# w:ins/w:smartTag/w:sdt/w:fldSimple — четыре вида узлов этапа NODES.
INLINE_DESCEND = _w(
    "hyperlink", "ins", "smartTag", "sdt", "sdtContent", "fldSimple",
    "customXml", "moveTo", "dir", "bdo",
)
# Внутри абзаца: что пропускаем осознанно. w:del/w:moveFrom — удалённый текст
# рецензирования: он лежит в w:delText и видимым содержимым НЕ является.
INLINE_IGNORE = _MARKERS | _w(
    "pPr", "del", "moveFrom", "smartTagPr", "sdtPr", "sdtEndPr", "customXmlPr",
)

# Служебные сноски-разделители Word: w:id = -1 (separator) и 0 (continuationSeparator).
# Это не пользовательский текст, а линейка-разделитель — не зона. Оба обычно и так
# пусты (фильтр значимости их снял бы), но не полагаемся на это: id — явный контракт.
_SERVICE_NOTE_IDS = {"-1", "0"}

_PREVIEW_LEN = 120


class UnreadZoneError(Exception):
    """Документ содержит зоны, которые система не умеет читать (этап 1b).

    Несёт список Zone, чтобы вызывающий код мог напечатать таблицу, а не только
    сообщение. Поднимается ТОЛЬКО в strict-режиме — см. блок 7, cmd_encrypt.
    """

    def __init__(self, zones: "list[Zone]", path: str) -> None:
        self.zones = zones
        self.path = path
        super().__init__(
            f"Документ содержит {len(zones)} непрочитанных зон(ы): {path}"
        )


@dataclass
class Zone:
    """Одна непрочитанная зона.

    kind — из _KINDS; part — имя части ZIP-архива, в которой зона найдена
    (для зон внутри document.xml это 'word/document.xml', а не имя подузла);
    char_count — длина сырого текста зоны; text — СЫРОЙ текст целиком (нужен
    sidecar'у этапа 1b: пользователь должен видеть, что именно выброшено);
    text_preview — усечённая до _PREVIEW_LEN копия для печати в таблице.
    """

    kind: str
    part: str
    char_count: int
    text_preview: str
    text: str = field(default="", repr=False)
    # detail — уточнение для отчёта: для unknown_node это имя тега ('w:oMath').
    # Пустая строка для прочих видов; в sidecar не пишется (схема {sid}.unread.json
    # не меняется — её читают уже существующие сессии).
    detail: str = ""


_KINDS = ("header", "footer", "footnote", "endnote", "textbox", "nested_table",
          "comment", "unknown_node")


def _zone_text(element) -> str:
    """Весь текст поддерева: конкатенация w:t в порядке документа.

    Берём именно w:t (а не itertext()) по той же причине, что и docx_rewriter:
    w:delText (удалённый текст рецензирования) и w:instrText (коды полей) не
    являются видимым содержимым. Разделители между абзацами не вставляем —
    метрика здесь одна: есть ли непробельный символ и сколько их всего.
    """
    return "".join(t.text or "" for t in element.iter(_W_T))


def _significant(text: str) -> bool:
    """Зона значима, только если в w:t есть хоть один НЕпробельный символ.

    Пустой колонтитул (Word кладёт header1.xml почти в каждый документ, даже когда
    пользователь ничего в него не вписал) — не зона: отказ на нём был бы отказом
    на любом документе вообще, и fail-closed мгновенно перестали бы воспринимать
    всерьёз.
    """
    return bool(text.strip())


def _make_zone(kind: str, part: str, text: str, detail: str = "") -> Zone:
    preview = text.strip()
    if len(preview) > _PREVIEW_LEN:
        preview = preview[:_PREVIEW_LEN] + "…"
    if detail:
        preview = f"<{detail}> {preview}".rstrip()
    return Zone(kind=kind, part=part, char_count=len(text),
                text_preview=preview, text=text, detail=detail)


def _whole_part_kind(name: str) -> "str | None":
    """kind для части, которая не читается ЦЕЛИКОМ, либо None — если часть не наша.

    Сопоставление по префиксу/точному имени, без fnmatch: 'word/header1.xml',
    'word/header12.xml' — оба заголовки; 'word/headerReference.xml' в OOXML не
    бывает, но даже если бы был — лишняя зона терпима (принцип полноты).
    """
    if not name.startswith("word/") or not name.endswith(".xml"):
        return None
    stem = name[len("word/"):-len(".xml")]
    if stem.startswith("header"):
        return "header"
    if stem.startswith("footer"):
        return "footer"
    return None


def _scan_notes(root, part: str, note_tag: str, kind: str) -> "list[Zone]":
    """Сноски / концевые сноски / примечания — по одной зоне на запись.

    Служебные разделители (w:id -1 и 0) пропускаются: см. _SERVICE_NOTE_IDS.
    """
    zones = []
    for note in root.iter(note_tag):
        if note.get(_W_ID) in _SERVICE_NOTE_IDS:
            continue
        text = _zone_text(note)
        if _significant(text):
            zones.append(_make_zone(kind, part, text))
    return zones


# Префиксы для отчёта: имя незнакомого тега печатается пользователю, и
# '{http://…/2006/math}oMath' в таблице отказа нечитаемо. Список не влияет ни на
# одно решение — только на вид строки.
_NS_PREFIX = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "w",
    "http://schemas.openxmlformats.org/officeDocument/2006/math": "m",
    "http://schemas.openxmlformats.org/markup-compatibility/2006": "mc",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "a",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape": "wps",
    "urn:schemas-microsoft-com:vml": "v",
}


def _short(tag: str) -> str:
    """'{…wordprocessingml…}oMath' -> 'w:oMath'. Незнакомое пространство имён
    остаётся как есть — лучше длинно, чем неоднозначно."""
    tag = str(tag)
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        prefix = _NS_PREFIX.get(ns)
        return f"{prefix}:{local}" if prefix else tag
    return tag


def walk_readable(root) -> "tuple[set, list]":
    """Обход части ТОЧНО ТАК ЖЕ, как её читает extractor.

    Возвращает `(reached, offlist)`:
      * `reached` — множество элементов w:t, до которых читатель ДОБИРАЕТСЯ;
      * `offlist` — список (элемент, тег) узлов, которых нет в закрытом списке
        своего уровня.

    Функция — общая для сканера зон и для `extractor.py`: извлечение текста идёт
    по тем же наборам (BLOCK_/CELL_/TABLE_/ROW_/INLINE_), поэтому «читатель
    прочитал» и «сторож считает прочитанным» не могут разойтись.

    Границы обхода намеренно повторяют ОГРАНИЧЕНИЯ читателя, а не разметку
    OOXML: в ячейку таблицы читатель заглядывает только за прямыми w:p, поэтому
    и сканер не спускается там ни в w:sdt, ни во вложенную w:tbl. Всё, что при
    этом осталось за бортом, ловит сверка по w:t у вызывающего.
    """
    reached: set = set()
    offlist: list = []

    def run(r) -> None:
        for t in r.iterchildren(_W_T):
            reached.add(t)

    def para(p) -> None:
        for child in p.iterchildren():
            tag = child.tag
            if tag == _W_R:
                run(child)
            elif tag in INLINE_DESCEND:
                para(child)          # обёртка: раны лежат внутри неё
            elif tag in INLINE_IGNORE or not isinstance(tag, str):
                continue             # not str -> комментарий / PI lxml
            else:
                offlist.append((child, tag))

    def table(tbl) -> None:
        for child in tbl.iterchildren():
            tag = child.tag
            if tag == _W_TR:
                row(child)
            elif tag in TABLE_IGNORE or not isinstance(tag, str):
                continue
            else:
                offlist.append((child, tag))

    def row(tr) -> None:
        for child in tr.iterchildren():
            tag = child.tag
            if tag == _W_TC:
                cell(child)
            elif tag in ROW_IGNORE or not isinstance(tag, str):
                continue
            else:
                offlist.append((child, tag))

    def cell(tc) -> None:
        for child in tc.iterchildren():
            tag = child.tag
            if tag == _W_P:
                para(child)
            elif tag == _W_TBL:
                continue             # вложенная таблица — не читается, ловится сверкой
            elif tag in CELL_IGNORE or not isinstance(tag, str):
                continue
            else:
                offlist.append((child, tag))

    def blocks(el) -> None:
        for child in el.iterchildren():
            tag = child.tag
            if tag == _W_P:
                para(child)
            elif tag == _W_TBL:
                table(child)
            elif tag in BLOCK_DESCEND:
                blocks(child)
            elif tag in BLOCK_IGNORE or not isinstance(tag, str):
                continue
            else:
                offlist.append((child, tag))

    blocks(body_of(root))
    return reached, offlist


def body_of(root):
    """Контейнер блоков части: w:body для word/document.xml, сам корень для
    w:hdr / w:ftr. Разделение по тегу, а не по имени части — так функция годится
    и для проверки на лету."""
    body = root.find(_W_BODY)
    return root if body is None else body


def _classify_unreached(t, offlist_set: set) -> "tuple[object, str] | None":
    """Кто отвечает за недостигнутый w:t: (элемент-владелец, kind).

    Идём от узла ВВЕРХ и берём ПЕРВОГО ответственного, чтобы одна надпись или
    одна вложенная таблица дала одну зону, а не по зоне на каждый w:t. None —
    узел лежит внутри уже зарегистрированного off-list элемента (он сам и есть
    зона, дубль не нужен).
    """
    owner_txbx = None
    owner_nested = None
    el = t
    while el is not None:
        if el in offlist_set:
            return None
        tag = el.tag
        if tag == _W_TXBX_CONTENT:
            owner_txbx = el
        elif tag == _W_TBL:
            # вложенность определяем по ПРЕДКУ — так таблица, вложенная на два
            # уровня, находится ровно один раз (её внешняя пара tc не считается)
            p = el.getparent()
            while p is not None:
                if p.tag == _W_TC:
                    owner_nested = el
                    break
                p = p.getparent()
        el = el.getparent()
    # самый ВНЕШНИЙ владелец выигрывает — он даёт одну зону на всё поддерево
    if owner_txbx is not None and owner_nested is None:
        return owner_txbx, "textbox"
    if owner_nested is not None:
        # надпись внутри вложенной таблицы: отчитываемся таблицей целиком
        return owner_nested, "nested_table"
    return t, "unknown_node"


def _scan_readable_part(root, part: str) -> "list[Zone]":
    """Непрочитанные подузлы ВНУТРИ читаемой части (тело или колонтитул).

    Часть читается, поэтому объявлять её зоной целиком нельзя — иначе отказ шёл
    бы на каждом .docx. Работа в два шага:

    1. **Закрытый список.** `walk_readable` обходит часть путём читателя; узел,
       которого нет в наборах своего уровня, попадает в зону `unknown_node`.
       Это замок на будущее: новый вид узла даёт отказ, а не тихую утечку.

    2. **Сверка по w:t.** Всё, до чего читатель не добрался, обязано быть
       объявлено — даже если сам маршрут туда «известен». Сравниваем множество
       ВСЕХ w:t части с достигнутыми: разница классифицируется по предку
       (`textbox` — содержимое надписи в любом из двух кодирований, VML и
       DrawingML, ведь оба кладут внутрь один и тот же w:txbxContent;
       `nested_table`; иначе `unknown_node`). Именно эта сверка, а не список,
       и есть гарантия полноты: список говорит, куда идти, сверка — не осталось
       ли текста в стороне.
    """
    reached, offlist = walk_readable(root)
    offlist_set = {el for el, _ in offlist}

    zones: "list[Zone]" = []
    for el, tag in offlist:
        text = _zone_text(el)
        zones.append(_make_zone("unknown_node", part, text, detail=_short(tag)))

    seen_owners: set = set()
    for t in root.iter(_W_T):
        if t in reached:
            continue
        owner = _classify_unreached(t, offlist_set)
        if owner is None:
            continue
        el, kind = owner
        if el in seen_owners:
            continue
        seen_owners.add(el)
        text = _zone_text(el)
        if _significant(text):
            zones.append(_make_zone(kind, part, text,
                                    detail="" if kind != "unknown_node" else _short(t.getparent().tag)))
    return zones


def scan_unread_zones(docx_path: str) -> "list[Zone]":
    """Находит в .docx всё текстовое содержимое, которого extractor не увидит.

    Возвращает список Zone (пустой — документ читается целиком). Порядок:
    document.xml, затем прочие части по имени — детерминированно, чтобы отчёт и
    sidecar не «дрожали» между прогонами.

    Битый / не-ZIP файл -> ooxml_core.OoxmlError (пробрасывается наверх: решать,
    что это значит, — дело вызывающего; здесь мы не знаем, читают нас из encrypt
    или из харнесса замера).

    XML-часть, которую не удалось разобрать, НЕ проглатывается молча: считаем её
    зоной kind по имени части (или 'textbox' для document.xml — заведомо ложное,
    но громкое). Тихо вернуть «зон нет» на битой части — ровно тот молчаливый
    провал, против которого написан модуль.
    """
    parts = read_zip_parts(docx_path)

    zones: "list[Zone]" = []

    for name in sorted(parts):
        kind = _whole_part_kind(name)
        is_notes = name in ("word/footnotes.xml", "word/endnotes.xml", "word/comments.xml")
        is_doc = name == _DOCUMENT_PART
        if not (kind or is_notes or is_doc):
            continue

        try:
            root = parse_xml(parts[name]).getroot()
        except Exception:
            # Часть не разобралась — доложить, а не пропустить (см. docstring).
            zones.append(Zone(
                kind=kind or ("textbox" if is_doc else "footnote"),
                part=name, char_count=0,
                text_preview="<часть не разобралась как XML — содержимое неизвестно>",
                text="",
            ))
            continue

        if is_doc or kind:
            # ЭТАП NODES: колонтитулы теперь ЧИТАЮТСЯ наравне с телом, поэтому
            # частью целиком больше не объявляются — как и document.xml, они
            # проходят разбор подузлов.
            zones.extend(_scan_readable_part(root, name))
        elif name == "word/footnotes.xml":
            zones.extend(_scan_notes(root, name, _W_FOOTNOTE, "footnote"))
        elif name == "word/endnotes.xml":
            zones.extend(_scan_notes(root, name, _W_ENDNOTE, "endnote"))
        elif name == "word/comments.xml":
            zones.extend(_scan_notes(root, name, _W_COMMENT, "comment"))

    # document.xml первым — он «главный», остальные части следом по имени.
    zones.sort(key=lambda z: (z.part != _DOCUMENT_PART, z.part))
    return zones


def zones_table(zones: "list[Zone]") -> str:
    """Человекочитаемая таблица «тип зоны / часть / сколько символов» (блок 7)."""
    rows = [("ТИП ЗОНЫ", "ЧАСТЬ", "СИМВОЛОВ", "НАЧАЛО ТЕКСТА")]
    for z in zones:
        rows.append((z.kind, z.part, str(z.char_count), z.text_preview))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    out = []
    for i, r in enumerate(rows):
        out.append("  %-*s  %-*s  %*s  %s" % (
            widths[0], r[0], widths[1], r[1], widths[2], r[2], r[3]))
        if i == 0:
            out.append("  " + "-" * (sum(widths) + 6 + len(rows[0][3])))
    return "\n".join(out)


def zones_to_json(zones: "list[Zone]") -> "list[dict]":
    """Список зон для sidecar {sid}.unread.json — с СЫРЫМ текстом (этап 1b).

    Сырой текст здесь намеренно: смысл sidecar'а в том, чтобы пользователь увидел,
    что именно выброшено из анонимного результата. Файл содержит ПДн в открытом
    виде и наружу (в LLM) не отдаётся — он лежит рядом с сессией, как и .enc.
    """
    return [
        {"kind": z.kind, "part": z.part, "char_count": z.char_count, "text": z.text}
        for z in zones
    ]
