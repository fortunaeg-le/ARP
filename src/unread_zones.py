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
_W_TC = _W + "tc"
_W_TBL = _W + "tbl"
_W_TXBX_CONTENT = _W + "txbxContent"
_W_FOOTNOTE = _W + "footnote"
_W_ENDNOTE = _W + "endnote"
_W_COMMENT = _W + "comment"
_W_ID = _W + "id"

# Часть, которую extractor читает (частично — только верхний уровень тела).
# Все ОСТАЛЬНЫЕ части ниже не читаются целиком, поэтому объявляются зоной целиком;
# внутри document.xml зоной объявляются только непрочитанные подузлы.
_DOCUMENT_PART = "word/document.xml"

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


_KINDS = ("header", "footer", "footnote", "endnote", "textbox", "nested_table", "comment")


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


def _make_zone(kind: str, part: str, text: str) -> Zone:
    preview = text.strip()
    if len(preview) > _PREVIEW_LEN:
        preview = preview[:_PREVIEW_LEN] + "…"
    return Zone(kind=kind, part=part, char_count=len(text),
                text_preview=preview, text=text)


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


def _scan_document(root) -> "list[Zone]":
    """Непрочитанные подузлы ВНУТРИ word/document.xml.

    Само тело extractor читает, поэтому частью целиком его объявлять нельзя —
    иначе отказывал бы каждый .docx. Не читаются два вида подузлов:

    1. w:txbxContent — содержимое надписи. Ищем по ТЕГУ, а не по родителю
       (w:pict / mc:AlternateContent), и это принципиально: у надписи два разных
       кодирования — старое VML (w:pict/v:shape/v:textbox) и DrawingML
       (mc:AlternateContent/mc:Choice/w:drawing/wps:txbx, с VML-дублем в
       mc:Fallback). Оба кладут внутрь один и тот же w:txbxContent, поэтому поиск
       по тегу ловит оба кодирования разом и не может пропустить третье, если
       Word его когда-нибудь заведёт. Плата — AlternateContent с обоими вариантами
       даёт две зоны на одну надпись; это ложноположительный результат, который мы
       осознанно предпочитаем риску пропуска.

    2. w:tbl с предком w:tc — вложенная таблица. Определяем по ПРЕДКУ, а не
       перебором `tc.iter(tbl)`: последний дал бы дубли на таблице, вложенной на
       два уровня (её нашли бы оба внешних tc). Признак «есть предок w:tc» ловит
       любую глубину вложенности ровно один раз.
    """
    zones = []
    for txbx in root.iter(_W_TXBX_CONTENT):
        text = _zone_text(txbx)
        if _significant(text):
            zones.append(_make_zone("textbox", _DOCUMENT_PART, text))

    for tbl in root.iter(_W_TBL):
        parent = tbl.getparent()
        nested = False
        while parent is not None:
            if parent.tag == _W_TC:
                nested = True
                break
            parent = parent.getparent()
        if not nested:
            continue
        text = _zone_text(tbl)
        if _significant(text):
            zones.append(_make_zone("nested_table", _DOCUMENT_PART, text))
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

        if is_doc:
            zones.extend(_scan_document(root))
        elif kind:
            text = _zone_text(root)
            if _significant(text):
                zones.append(_make_zone(kind, name, text))
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
