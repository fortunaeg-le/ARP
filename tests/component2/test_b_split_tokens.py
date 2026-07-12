"""Группа B — токен, разорванный между узлами (главный алгоритмический риск ядра).

Позитивные случаи: значение попадает целиком в стартовый узел с оформлением
ЭТОГО (первого) узла. Негативные случаи: псевдо-разделители (<w:br/>) и границы
групп (соседние абзацы) не должны склеиваться в токен.
"""
from lxml import etree

from docx_rewriter import rewrite as docx_rewrite
from xlsx_rewriter import rewrite as xlsx_rewrite

from conftest import W, S, read_part, resolver, make_zip


# ---------------------------------------------------------------------------
# B1 — токен на 2 run'а, значение получает оформление ПЕРВОГО run'а
# ---------------------------------------------------------------------------

def test_docx_token_split_two_runs_gets_first_run_formatting(docx_builder):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p>
  <w:r><w:rPr><w:b/><w:color w:val="FF0000"/></w:rPr><w:t>[ORG</w:t></w:r>
  <w:r><w:rPr><w:i/></w:rPr><w:t>_1]</w:t></w:r>
</w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    dst = src.replace("src.docx", "out.docx")

    unresolved = docx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    root = etree.fromstring(read_part(dst, "word/document.xml"))
    runs = root.findall(f".//{{{W}}}r")
    assert runs[0].find(f"{{{W}}}t").text == "ООО «Ромашка»"
    assert (runs[1].find(f"{{{W}}}t").text or "") == ""
    # оформление первого run'а (тот, куда легло значение) не тронуто
    rpr0 = runs[0].find(f"{{{W}}}rPr")
    assert rpr0.find(f"{{{W}}}b") is not None
    assert rpr0.find(f"{{{W}}}color").get(f"{{{W}}}val") == "FF0000"
    # второй run по-прежнему существует как узел (не удалён), его rPr не тронут
    rpr1 = runs[1].find(f"{{{W}}}rPr")
    assert rpr1.find(f"{{{W}}}i") is not None


# ---------------------------------------------------------------------------
# B2 — токен на 3+ run'а
# ---------------------------------------------------------------------------

def test_docx_token_split_across_three_runs(docx_builder):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p>
  <w:r><w:t>[OR</w:t></w:r>
  <w:r><w:t>G_</w:t></w:r>
  <w:r><w:t>1]</w:t></w:r>
</w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    dst = src.replace("src.docx", "out.docx")

    unresolved = docx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    root = etree.fromstring(read_part(dst, "word/document.xml"))
    ts = [t.text or "" for t in root.findall(f".//{{{W}}}t")]
    assert ts == ["ООО «Ромашка»", "", ""]


# ---------------------------------------------------------------------------
# B3 — xlsx rich text: токен разорван между <r> внутри <si>
# ---------------------------------------------------------------------------

def test_xlsx_token_split_across_three_runs_in_si(xlsx_builder):
    shared = (
        f'<sst xmlns="{S}" count="1" uniqueCount="1"><si>'
        f'<r><t>[OR</t></r><r><t>G_</t></r><r><t>1]</t></r>'
        f'</si></sst>'
    ).encode("utf-8")
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", shared, sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    unresolved = xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    root = etree.fromstring(read_part(dst, "xl/sharedStrings.xml"))
    ts = [t.text or "" for t in root.iter(f"{{{S}}}t")]
    assert ts == ["ООО «Ромашка»", "", ""]


# ---------------------------------------------------------------------------
# B4 — негативный: перенос строки (<w:br/>) внутри токена НЕ склеивается
# ---------------------------------------------------------------------------

def test_docx_token_split_by_linebreak_not_resolved(docx_builder):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p>
  <w:r><w:t>[PHONE</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>_1]</w:t></w:r>
</w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    dst = src.replace("src.docx", "out.docx")

    unresolved = docx_rewrite(src, dst, resolver({"[PHONE_1]": "+7 495 123-45-67"}))

    assert unresolved == []  # не найдено совпадений TOKEN_RE вообще -> не unresolved
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    ts = [t.text or "" for t in root.findall(f".//{{{W}}}t")]
    assert ts == ["[PHONE", "_1]"]
    assert root.find(f".//{{{W}}}br") is not None


# ---------------------------------------------------------------------------
# B5 — негативный: токен, разорванный между двумя соседними абзацами,
# не склеивается (группы строятся по одному <w:p>).
# ---------------------------------------------------------------------------

def test_docx_token_split_across_paragraphs_not_resolved(docx_builder):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>Конец абзаца …[ORG</w:t></w:r></w:p>
<w:p><w:r><w:t>_1] начало следующего…</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    dst = src.replace("src.docx", "out.docx")

    unresolved = docx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    ts = [t.text or "" for t in root.findall(f".//{{{W}}}t")]
    assert ts == ["Конец абзаца …[ORG", "_1] начало следующего…"]
