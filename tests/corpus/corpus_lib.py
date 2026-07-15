# -*- coding: utf-8 -*-
"""
corpus_lib — ядро корпуса.

Ключевая идея: документ существует как МОДЕЛЬ (список блоков из чанков).
Разметка ставится на чанк В МОМЕНТ ГЕНЕРАЦИИ (тип/категория/приём),
а координаты start/end ВЫЧИСЛЯЮТСЯ механически при сериализации модели
в канонический plain-текст. Поэтому:
  * разметка никогда не «ищется» в готовом тексте (никакого post-hoc поиска);
  * мутатор меняет модель — и границы пересчитываются автоматически, по построению.

Канонический plain-текст (правило PT-1, см. README.md) реализован в serialize()
и продублирован независимым экстрактором extract_docx(), который читает уже
записанный .docx. Генератор падает, если эти два результата разошлись хоть на символ.
"""
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
def chunk(s, ent=None, neg=None, bold_split=False, ignore=None):
    c = {"s": s}
    if ent:
        c["e"] = ent
    if neg:
        c["n"] = neg
    if ignore:
        c["i"] = ignore
    if bold_split:
        c["bs"] = True
    return c


def ent(type_, cat, eid, trick=None, note=None, checksum=None):
    e = {"type": type_, "cat": cat, "id": eid}
    if trick:
        e["trick"] = trick
    if note:
        e["note"] = note
    if checksum:
        e["checksum"] = checksum
    return e


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
        self.igns = []

    def emit(self, ch):
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
            self.negs.append({"start": start, "end": end, "why": ch["n"]["why"]})
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
        ents.append(e)
    ents.sort(key=lambda x: (x["start"], x["end"]))
    negs = sorted(o.negs, key=lambda x: x["start"])
    for n in negs:
        n["text"] = text[n["start"]:n["end"]]
    negs = [{"start": n["start"], "end": n["end"], "text": n["text"], "why": n["why"]} for n in negs]
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


def _runs_xml(chunks, caps=False, bold=False):
    """Чанки -> <w:r>. Поддержка bold_split (слово разорвано форматированием)
    и переводов строки внутри чанка (<w:br/>)."""
    out = []

    def run(text, b):
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
                body.append('<w:t xml:space="preserve">%s</w:t>' % escape(part))
        out.append("<w:r>%s%s</w:r>" % (rpr_xml, "".join(body)))

    for ch in chunks:
        s = ch["s"]
        if ch.get("bs") and "\n" not in s and len(s) > 3:
            k = len(s) // 2
            run(s[:k], True)
            run(s[k:], False)
        else:
            run(s, bold)
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
    return (
        '<w:p><w:r><w:pict>'
        '<v:shape id="tb%d" type="#_x0000_t202" style="position:absolute;'
        'margin-left:0;margin-top:0;width:400pt;height:40pt">'
        '<v:textbox><w:txbxContent>%s</w:txbxContent></v:textbox>'
        '</v:shape></w:pict></w:r></w:p>' % (abs(hash(inner)) % 9000 + 1000, inner)
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


def update_gold(root, entries):
    p = os.path.join(root, "gold.json")
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
