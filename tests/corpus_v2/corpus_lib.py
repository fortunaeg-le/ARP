# -*- coding: utf-8 -*-
"""
corpus_lib (КОРПУС V2) — ядро второго корпуса.

ЭТО ОТДЕЛЬНАЯ КОПИЯ. Оригинал — tests/corpus/corpus_lib.py — заморожен вместе
со старым корпусом и входит в его MANIFEST.sha256; править его на месте нельзя,
поэтому копия живёт здесь и расходится с оригиналом сознательно.

Отличия от оригинала:
  * чанк может быть обёрнут в конструкцию Word (`wrap=`): отслеживаемая правка
    (`ins`/`del`), поле (`fld`/`fldinstr`), умный тег (`smarttag`), элемент
    формы (`sdt`). Обёртка меняет XML, но правило PT-1 остаётся прежним:
    канонический текст — это то, что видно в документе. Поэтому `del` (текст,
    удалённый правкой) в канонический текст НЕ входит и разметки не получает,
    а `fldinstr` (код поля) — тем более;
  * модель несёт `structure_group` ("simple"/"complex") — задача 4 сессии
    CORPUS-V2: две группы не смешиваются, пометка обязана быть в разметке.

Ключевая идея (без изменений): документ существует как МОДЕЛЬ (список блоков
из чанков).
Разметка ставится на чанк В МОМЕНТ ГЕНЕРАЦИИ (тип/категория/приём),
а координаты start/end ВЫЧИСЛЯЮТСЯ механически при сериализации модели
в канонический plain-текст. Поэтому:
  * разметка никогда не «ищется» в готовом тексте (никакого post-hoc поиска);
  * мутатор меняет модель — и границы пересчитываются автоматически, по построению.

Канонический plain-текст (правило PT-1, см. README.md) реализован в serialize()
и продублирован независимым экстрактором extract_docx(), который читает уже
записанный .docx. Генератор падает, если эти два результата разошлись хоть на символ.
"""
import hashlib
import io
import json
import os
import zipfile
from xml.sax.saxutils import escape

# ---------------------------------------------------------------- невидимые
NBSP = "\u00A0"        # неразрывный пробел
NNBSP = "\u202F"       # narrow no-break space
ZWSP = "\u200B"        # zero-width space
ZWJ = "\u200D"         # zero-width joiner
ZWNJ = "\u200C"        # zero-width non-joiner
SHY = "\u00AD"         # мягкий перенос
LRM = "\u200E"         # left-to-right mark
WJ = "\u2060"          # word joiner
INVISIBLES = [NBSP, NNBSP, ZWSP, ZWJ, ZWNJ, SHY, LRM, WJ]

# ---------------------------------------------------------------- омоглифы
# кириллица -> латиница (визуально идентичны)
CYR2LAT = {"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
           "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "а": "a", "е": "e",
           "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "м": "m",
           "З": "3", "Ѕ": "S"}
LAT2CYR = {v: k for k, v in CYR2LAT.items() if v.isalpha()}
# цифра -> визуально похожая буква (кириллица)
DIGIT2CYR = {"0": "О", "3": "З", "4": "Ч", "6": "б", "1": "І"}
# и обратно — буква на месте цифры
CYR2DIGIT = {v: k for k, v in DIGIT2CYR.items()}


# ---------------------------------------------------------------- модель
# Обёртки Word вокруг рана. Значение "невидимый" означает: текст чанка НЕ входит
# в канонический текст документа (правило PT-1), поэтому сериализатор его
# пропускает, а писатель .docx — пишет.
WRAPS_INVISIBLE = ("del",)
# ins        — <w:ins>       принятая/непринятая вставка (отслеживаемая правка)
# del        — <w:del>       удалённый правкой текст (в документе НЕ виден)
# fld        — <w:fldSimple> простое поле Word, виден кэшированный результат
# fldcomplex — begin/instrText/separate/результат/end: КОД поля не виден,
#              результат виден и получает координаты
# smarttag   — <w:smartTag>  умный тег
# sdt        — <w:sdt>       элемент формы (текстовый элемент управления)
WRAPS = ("ins", "del", "fld", "fldcomplex", "smarttag", "sdt")


def chunk(s, ent=None, neg=None, bold_split=False, ignore=None, wrap=None,
          instr=None):
    """`bold_split` — «слово разорвано форматированием».

    True   — разрыв посередине чанка (как в старом корпусе);
    целое N — разрыв РОВНО ПОСЛЕ N-го символа. Второй вид добавлен этапом
    CORPUS-V2-B: приём требуется применить «посреди ЧИСЛА», а середина чанка у
    формулировки вроде «10 000 (Десять тысяч) рублей» приходится на слова, а
    не на цифры, и приём проверял бы не то, что заявлено.
    """
    c = {"s": s}
    if ent:
        c["e"] = ent
    if neg:
        c["n"] = neg
    if ignore:
        c["i"] = ignore
    if bold_split:
        c["bs"] = True if bold_split is True else int(bold_split)
    if wrap:
        if wrap not in WRAPS:
            raise ValueError("unknown wrap %r" % wrap)
        if wrap in WRAPS_INVISIBLE and (ent or neg or ignore):
            raise ValueError(
                "чанк в обёртке %r не входит в канонический текст и не может "
                "нести разметку: %r" % (wrap, s))
        c["w"] = wrap
    if instr:
        c["fi"] = instr          # код поля Word (в документе не виден)
    return c


def ent(type_, cat, eid, trick=None, note=None, checksum=None, form=None,
        axes=None):
    e = {"type": type_, "cat": cat, "id": eid}
    if trick:
        e["trick"] = trick
    if note:
        e["note"] = note
    if checksum:
        e["checksum"] = checksum
    if form:
        # Идентификатор ФОРМЫ ЗАПИСИ. С этапа CORPUS-V2-B это не номер функции
        # в списке, а КОМБИНАЦИЯ ЗНАЧЕНИЙ ОСЕЙ (values.form_id).
        e["form"] = form
    if axes:
        # Набор признаков формулировки: {ось -> значение}. По нему тест
        # разнообразия считает покрытие осей и умеет назвать КОНКРЕТНУЮ ось и
        # КОНКРЕТНОЕ значение, которого не хватает.
        e["axes"] = axes
    return e


def neg(why, type_=None, form=None, axes=None):
    """Негатив. С этапа CORPUS-V2-B он может быть ТИПИЗИРОВАННЫМ.

    Обычный негатив («ГОСТ 7.32-2017 — не ПДн») несёт только причину.
    Типизированный несёт вид данных, форму и оси: так размечаются вхождения,
    у которых вид данных есть, а МАСКИРОВАТЬ НЕЧЕГО, — пустое место под сумму
    («______ рублей ______ копеек»), «Без НДС», номер договора, дата
    документа. Срабатывание детектора на них — ложное, а не находка, поэтому
    сущностями они быть не имеют права; но в покрытие осей они входят: их
    разнообразие проверяет ТОЧНОСТЬ ровно так же, как разнообразие величин
    проверяет полноту.
    """
    n = {"why": why}
    if type_:
        n["type"] = type_
    if form:
        n["form"] = form
    if axes:
        n["axes"] = axes
    return n


def para(chunks, style="normal", footnote=None):
    p = {"t": "p", "style": style, "chunks": chunks}
    if footnote:
        p["fn"] = footnote          # список чанков — текст сноски
    return p


def textbox(chunks):
    return {"t": "tb", "chunks": chunks}


def cell(blocks):
    return {"blocks": blocks}


def table(rows):
    return {"t": "tbl", "rows": rows}


# ---------------------------------------------------------------- сериализация
class _Out:
    def __init__(self):
        self.buf = []
        self.pos = 0
        self.ents = {}      # eid -> {meta, start, end}
        self.negs = []
        self.neg_by_id = {}
        self.igns = []

    def emit(self, ch):
        if ch.get("w") in WRAPS_INVISIBLE:
            # Удалённый правкой текст и код поля в документе не видны —
            # координат не получают и в канонический текст не попадают.
            return
        s = ch["s"]
        start, end = self.pos, self.pos + len(s)
        self.buf.append(s)
        self.pos = end
        if "e" in ch:
            e = ch["e"]
            eid = e["id"]
            if eid in self.ents:
                rec = self.ents[eid]
                rec["start"] = min(rec["start"], start)
                rec["end"] = max(rec["end"], end)
            else:
                self.ents[eid] = {"meta": e, "start": start, "end": end}
        if "n" in ch:
            m = ch["n"]
            nid = m.get("id")
            if nid is not None and nid in self.neg_by_id:
                # Негатив, разорванный границей ячейки, — ОДНО вхождение из
                # двух чанков, ровно как сущность с общим id.
                rec = self.neg_by_id[nid]
                rec["start"] = min(rec["start"], start)
                rec["end"] = max(rec["end"], end)
            else:
                rec = {"start": start, "end": end, "meta": m}
                if nid is not None:
                    self.neg_by_id[nid] = rec
                self.negs.append(rec)
        if "i" in ch:
            self.igns.append({"start": start, "end": end, "why": ch["i"]["why"]})

    def sep(self, s):
        self.buf.append(s)
        self.pos += len(s)


def _emit_para(o, p):
    for ch in p["chunks"]:
        o.emit(ch)


def _emit_blocks(o, blocks, footnotes):
    for i, b in enumerate(blocks):
        if i:
            o.sep("\n")
        t = b["t"]
        if t == "p":
            _emit_para(o, b)
            if b.get("fn"):
                footnotes.append(b["fn"])
        elif t == "tb":
            for ch in b["chunks"]:
                o.emit(ch)
        elif t == "tbl":
            for ri, row in enumerate(b["rows"]):
                if ri:
                    o.sep("\n")
                for ci, c in enumerate(row):
                    if ci:
                        o.sep("\t")
                    _emit_blocks(o, c["blocks"], footnotes)
        else:
            raise ValueError("unknown block %r" % t)


def serialize(model):
    """Модель -> (plain_text, entities, negatives). Правило PT-1."""
    o = _Out()
    footnotes = []
    sections = []
    if model.get("header"):
        sections.append(("hdr", model["header"]))
    sections.append(("body", model["body"]))
    first = True
    for _, blocks in sections:
        if not first:
            o.sep("\n")
        first = False
        _emit_blocks(o, blocks, footnotes)
    for fn in footnotes:
        o.sep("\n")
        for ch in fn:
            o.emit(ch)
    if model.get("footer"):
        o.sep("\n")
        _emit_blocks(o, model["footer"], footnotes[:0] or [])
    text = "".join(o.buf)
    ents = []
    for eid, rec in o.ents.items():
        m = rec["meta"]
        e = {"type": m["type"], "start": rec["start"], "end": rec["end"],
             "text": text[rec["start"]:rec["end"]], "category": m["cat"]}
        if m.get("trick"):
            e["trick"] = m["trick"]
        if m.get("note"):
            e["note"] = m["note"]
        if m.get("checksum"):
            e["checksum"] = m["checksum"]
        if m.get("form"):
            e["form"] = m["form"]
        if m.get("axes"):
            # Без этой строки признаки формулировки оставались бы в модели и
            # не доходили до эталона: тест осей видел бы «ось применима к 0
            # вхождений» и краснел бы на пустом месте.
            e["axes"] = m["axes"]
        ents.append(e)
    ents.sort(key=lambda x: (x["start"], x["end"]))
    negs = []
    for rec in sorted(o.negs, key=lambda x: x["start"]):
        m = rec["meta"]
        n = {"start": rec["start"], "end": rec["end"],
             "text": text[rec["start"]:rec["end"]], "why": m["why"]}
        # Типизированный негатив несёт вид данных, форму и оси — он участвует
        # в покрытии осей наравне с величинами (см. corpus_lib.neg).
        for key in ("type", "form", "axes", "trick"):
            if m.get(key):
                n[key] = m[key]
        negs.append(n)
    igns = sorted(o.igns, key=lambda x: x["start"])
    igns = [{"start": g["start"], "end": g["end"], "text": text[g["start"]:g["end"]],
             "why": g["why"]} for g in igns]
    return text, ents, negs, igns


def gold_entry(model):
    text, ents, negs, igns = serialize(model)
    return {
        "doc_id": model["doc_id"],
        "format": model["format"],
        "source": model["source"],
        # ЯВНАЯ пометка группы структуры (задача 4). Группы не смешиваются:
        # метрики новых видов данных считаются только по "simple", "complex"
        # существует, чтобы потери чтения были видны числом.
        "structure_group": model["structure_group"],
        "structure_tricks": model.get("structure_tricks", []),
        "contract_type": model.get("contract_type", ""),
        "parties": model.get("parties", ""),
        "features": model.get("features", []),
        "entities": ents,
        "negatives": negs,
        "ignore": igns,
    }


# ---------------------------------------------------------------- .docx writer
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
NS_ALL = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" '
    'xmlns:o="urn:schemas-microsoft-com:office:office"'
)


_REV_DATE = '2024-01-01T00:00:00Z'
_REV_AUTHOR = 'Юрист'
# Счётчик w:id для отслеживаемых правок и элементов формы. Сбрасывается в начале
# write_docx: генерация однопоточная и детерминированная, поэтому счётчик даёт
# одни и те же id при каждом прогоне (побайтная воспроизводимость корпуса).
_REV = {"n": 1}


def _runs_xml(chunks, caps=False, bold=False, rev=None):
    """Чанки -> <w:r> (при необходимости в обёртке Word).

    Поддержка bold_split (слово разорвано форматированием), переводов строки
    внутри чанка (<w:br/>) и обёрток `wrap=` (см. WRAPS).

    `rev` — счётчик id для отслеживаемых правок; общий на документ, чтобы
    w:id не повторялись (Word на дубликатах ругается).
    """
    out = []
    if rev is None:
        rev = _REV

    def _rid():
        i = rev["n"]
        rev["n"] += 1
        return i

    def run(text, b, tag="w:t"):
        rpr = []
        if b:
            rpr.append("<w:b/>")
        if caps:
            rpr.append("<w:caps/>")
        rpr_xml = "<w:rPr>%s</w:rPr>" % "".join(rpr) if rpr else ""
        parts = text.split("\n")
        body = []
        for i, part in enumerate(parts):
            if i:
                body.append("<w:br/>")
            if part:
                body.append('<%s xml:space="preserve">%s</%s>'
                            % (tag, escape(part), tag))
        return "<w:r>%s%s</w:r>" % (rpr_xml, "".join(body))

    def wrapped(ch, xml):
        w = ch.get("w")
        if not w:
            return xml
        if w == "ins":
            return ('<w:ins w:id="%d" w:author="%s" w:date="%s">%s</w:ins>'
                    % (_rid(), _REV_AUTHOR, _REV_DATE, xml))
        if w == "del":
            return ('<w:del w:id="%d" w:author="%s" w:date="%s">%s</w:del>'
                    % (_rid(), _REV_AUTHOR, _REV_DATE, xml))
        if w == "fld":
            return ('<w:fldSimple w:instr="%s">%s</w:fldSimple>'
                    % (escape(ch.get("fi") or ' DOCPROPERTY "Value" \\* MERGEFORMAT ',
                              {'"': "&quot;"}), xml))
        if w == "fldcomplex":
            return (
                '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve">%s</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                '%s'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
                % (escape(ch.get("fi") or " REF value \\h "), xml))
        if w == "smarttag":
            return ('<w:smartTag w:uri="urn:schemas-microsoft-com:office:smarttags"'
                    ' w:element="PersonName">%s</w:smartTag>' % xml)
        if w == "sdt":
            return (
                '<w:sdt><w:sdtPr><w:alias w:val="Поле формы"/>'
                '<w:tag w:val="v2"/><w:id w:val="%d"/><w:text/></w:sdtPr>'
                '<w:sdtContent>%s</w:sdtContent></w:sdt>' % (_rid(), xml))
        raise ValueError("unknown wrap %r" % w)

    for ch in chunks:
        s = ch["s"]
        # Удалённый правкой текст пишется в <w:delText> — именно поэтому он не
        # виден ни Word'у, ни эталонному экстрактору, ни в канонтексте.
        tag = "w:delText" if ch.get("w") == "del" else "w:t"
        bs = ch.get("bs")
        if bs and "\n" not in s and len(s) > 3:
            # bs is True -> разрыв посередине; целое -> разрыв после N-го
            # символа (нужен, чтобы рвать ЧИСЛО, а не слово рядом с ним).
            k = len(s) // 2 if bs is True else max(1, min(int(bs), len(s) - 1))
            xml = run(s[:k], True, tag) + run(s[k:], False, tag)
        else:
            xml = run(s, bold, tag)
        out.append(wrapped(ch, xml))
    return "".join(out)


def _para_xml(p, fn_id=None):
    style = p.get("style", "normal")
    caps = style == "capsstyle"
    bold = style in ("title", "bold")
    ppr = ""
    if style == "title":
        ppr = '<w:pPr><w:jc w:val="center"/></w:pPr>'
    elif style == "right":
        ppr = '<w:pPr><w:jc w:val="right"/></w:pPr>'
    xml = _runs_xml(p["chunks"], caps=caps, bold=bold)
    if fn_id is not None:
        xml += ('<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                '<w:footnoteReference w:id="%d"/></w:r>' % fn_id)
    return "<w:p>%s%s</w:p>" % (ppr, xml)


def _textbox_xml(tb):
    inner = "<w:p>%s</w:p>" % _runs_xml(tb["chunks"])
    # Идентификатор фигуры считается УСТОЙЧИВЫМ хешем, а не встроенным hash().
    # Здесь был hash(inner): для строк он рандомизируется при каждом запуске
    # интерпретатора (PYTHONHASHSEED), и десять .docx с текстбоксом выходили
    # РАЗНЫМИ БАЙТАМИ при каждой пересборке. Внутри одного прогона это не
    # видно — гниль вылезала только сверкой MANIFEST.sha256 после повторной
    # сборки, то есть ровно там, где корпус обязан быть побайтно
    # воспроизводимым.
    shape_id = int(hashlib.sha256(inner.encode("utf-8")).hexdigest()[:8], 16)
    return (
        '<w:p><w:r><w:pict>'
        '<v:shape id="tb%d" type="#_x0000_t202" style="position:absolute;'
        'margin-left:0;margin-top:0;width:400pt;height:40pt">'
        '<v:textbox><w:txbxContent>%s</w:txbxContent></v:textbox>'
        '</v:shape></w:pict></w:r></w:p>' % (shape_id % 9000 + 1000, inner)
    )


def _tbl_xml(tbl, fn_counter):
    rows = []
    ncols = max(len(r) for r in tbl["rows"])
    grid = "<w:tblGrid>%s</w:tblGrid>" % ("<w:gridCol w:w=\"%d\"/>" % (9360 // ncols) * ncols)
    for row in tbl["rows"]:
        cells = []
        for c in row:
            body = _blocks_xml(c["blocks"], fn_counter)
            if not body.endswith("</w:p>"):
                body += "<w:p/>"
            cells.append(
                '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>%s</w:tc>'
                % (9360 // ncols, body))
        rows.append("<w:tr>%s</w:tr>" % "".join(cells))
    props = ('<w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
             '<w:tblBorders>'
             '<w:top w:val="single" w:sz="4" w:color="auto"/>'
             '<w:left w:val="single" w:sz="4" w:color="auto"/>'
             '<w:bottom w:val="single" w:sz="4" w:color="auto"/>'
             '<w:right w:val="single" w:sz="4" w:color="auto"/>'
             '<w:insideH w:val="single" w:sz="4" w:color="auto"/>'
             '<w:insideV w:val="single" w:sz="4" w:color="auto"/>'
             '</w:tblBorders></w:tblPr>')
    return "<w:tbl>%s%s%s</w:tbl>" % (props, grid, "".join(rows))


def _blocks_xml(blocks, fn_counter):
    out = []
    for b in blocks:
        t = b["t"]
        if t == "p":
            if b.get("fn"):
                fn_counter["items"].append(b["fn"])
                out.append(_para_xml(b, fn_id=fn_counter["next"]))
                fn_counter["next"] += 1
            else:
                out.append(_para_xml(b))
        elif t == "tb":
            out.append(_textbox_xml(b))
        elif t == "tbl":
            out.append(_tbl_xml(b, fn_counter))
    return "".join(out)


_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
{extra}
</Types>"""

_RELS_ROOT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles %s>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="character" w:styleId="FootnoteReference"><w:name w:val="footnote reference"/><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="FootnoteText"><w:name w:val="footnote text"/><w:rPr><w:sz w:val="20"/></w:rPr></w:style>
</w:styles>""" % W


def write_docx(model, path):
    _REV["n"] = 1
    fn = {"items": [], "next": 1}
    body_xml = _blocks_xml(model["body"], fn)

    sect = ['<w:sectPr>']
    rels = ['<Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    extra_ct = []
    files = {}

    if model.get("header"):
        hdr = _blocks_xml(model["header"], {"items": [], "next": 900})
        files["word/header1.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                     '<w:hdr %s>%s</w:hdr>' % (NS_ALL, hdr))
        rels.append('<Relationship Id="rIdH" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>')
        extra_ct.append('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>')
        sect.append('<w:headerReference w:type="default" r:id="rIdH"/>')

    if model.get("footer"):
        ftr = _blocks_xml(model["footer"], {"items": [], "next": 950})
        files["word/footer1.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                     '<w:ftr %s>%s</w:ftr>' % (NS_ALL, ftr))
        rels.append('<Relationship Id="rIdF" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
        extra_ct.append('<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>')
        sect.append('<w:footerReference w:type="default" r:id="rIdF"/>')

    if fn["items"]:
        fns = ['<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
               '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>']
        for i, item in enumerate(fn["items"], start=1):
            fns.append('<w:footnote w:id="%d"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>'
                       '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteRef/></w:r>%s</w:p></w:footnote>'
                       % (i, _runs_xml(item)))
        files["word/footnotes.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                       '<w:footnotes %s>%s</w:footnotes>' % (NS_ALL, "".join(fns)))
        rels.append('<Relationship Id="rIdN" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>')
        extra_ct.append('<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>')

    sect.append('<w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" w:header="708" w:footer="708"/>')
    sect.append('</w:sectPr>')

    files["word/document.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                  '<w:document %s><w:body>%s%s</w:body></w:document>'
                                  % (NS_ALL, body_xml, "".join(sect)))
    files["word/styles.xml"] = _STYLES
    files["[Content_Types].xml"] = _CT.format(extra="\n".join(extra_ct))
    files["_rels/.rels"] = _RELS_ROOT
    files["word/_rels/document.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
        % "".join(rels))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["[Content_Types].xml", "_rels/.rels", "word/document.xml",
                     "word/styles.xml", "word/header1.xml", "word/footer1.xml",
                     "word/footnotes.xml", "word/_rels/document.xml.rels"]:
            if name in files:
                zi = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, files[name].encode("utf-8"))


# ---------------------------------------------------------------- эталонный экстрактор
from lxml import etree  # noqa: E402

WNS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _p_text(p):
    """Текст абзаца: все w:t в порядке документа (включая внутри txbxContent),
    w:br -> \\n, w:tab -> \\t. Никакой нормализации Unicode."""
    parts = []
    for el in p.iter():
        tag = el.tag
        if tag == WNS + "t":
            parts.append(el.text or "")
        elif tag == WNS + "br":
            parts.append("\n")
        elif tag == WNS + "tab":
            parts.append("\t")
    return "".join(parts)


def _blocks_text(parent):
    out = []
    for child in parent:
        if child.tag == WNS + "p":
            out.append(_p_text(child))
        elif child.tag == WNS + "tbl":
            out.append(_tbl_text(child))
    return "\n".join(out)


def _tbl_text(tbl):
    rows = []
    for tr in tbl.findall(WNS + "tr"):
        cells = []
        for tc in tr.findall(WNS + "tc"):
            cells.append(_blocks_text(tc))
        rows.append("\t".join(cells))
    return "\n".join(rows)


def extract_docx(path):
    """Независимая реализация правила PT-1 поверх записанного .docx."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    chunks = []
    if "word/header1.xml" in names:
        root = etree.fromstring(z.read("word/header1.xml"))
        chunks.append(_blocks_text(root))
    doc = etree.fromstring(z.read("word/document.xml"))
    body = doc.find(WNS + "body")
    body_blocks = [c for c in body if c.tag in (WNS + "p", WNS + "tbl")]
    chunks.append("\n".join(_p_text(c) if c.tag == WNS + "p" else _tbl_text(c)
                           for c in body_blocks))
    if "word/footnotes.xml" in names:
        root = etree.fromstring(z.read("word/footnotes.xml"))
        for f in root.findall(WNS + "footnote"):
            if f.get(WNS + "id") in ("-1", "0"):
                continue
            chunks.append("\n".join(_p_text(p) for p in f.findall(WNS + "p")))
    if "word/footer1.xml" in names:
        root = etree.fromstring(z.read("word/footer1.xml"))
        chunks.append(_blocks_text(root))
    return "\n".join(chunks)


# ---------------------------------------------------------------- IO
def save_model(model, root):
    p = os.path.join(root, "_model", model["doc_id"] + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=1, sort_keys=True)


def load_model(root, doc_id):
    with open(os.path.join(root, "_model", doc_id + ".json"), encoding="utf-8") as f:
        return json.load(f)


def render(model, root, verify=True):
    """Модель -> файл документа + проверка совпадения plain-текста."""
    text, ents, negs, igns = serialize(model)
    docs = os.path.join(root, "docs")
    os.makedirs(docs, exist_ok=True)
    if model["format"] == "txt":
        path = os.path.join(docs, model["doc_id"] + ".txt")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if verify:
            with open(path, encoding="utf-8", newline="") as f:
                got = f.read()
            assert got == text, "txt mismatch in %s" % model["doc_id"]
    else:
        path = os.path.join(docs, model["doc_id"] + ".docx")
        write_docx(model, path)
        if verify:
            got = extract_docx(path)
            if got != text:
                for i, (a, b) in enumerate(zip(got, text)):
                    if a != b:
                        raise AssertionError(
                            "docx mismatch %s at %d: extracted %r vs model %r\n...%r\n...%r"
                            % (model["doc_id"], i, a, b, got[max(0, i-60):i+30], text[max(0, i-60):i+30]))
                raise AssertionError("docx length mismatch %s: %d vs %d"
                                     % (model["doc_id"], len(got), len(text)))
    save_model(model, root)
    return path, text, ents, negs, igns


GOLD_NAME = "gold_v2.json"      # своё имя: со старым gold.json не перепутать


def update_gold(root, entries):
    p = os.path.join(root, GOLD_NAME)
    cur = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cur = json.load(f)
    by_id = {e["doc_id"]: e for e in cur}
    for e in entries:
        by_id[e["doc_id"]] = e
    out = [by_id[k] for k in sorted(by_id)]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return len(out)
