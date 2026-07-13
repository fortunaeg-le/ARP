"""Группа A — сохранность оформления (ядро задачи Компонента 2).

Проверяем не «значение появилось в тексте», а что XML-узлы оформления (rPr,
атрибут s у <c>, ...) идентичны до и после, и что части архива, которые
адаптер не был обязан трогать, переносятся побайтово.
"""
import zipfile

from lxml import etree

from docx_rewriter import rewrite as docx_rewrite
from pptx_rewriter import rewrite as pptx_rewrite
from xlsx_rewriter import rewrite as xlsx_rewrite

from conftest import W, A, S, P_NS, read_part, resolver, assert_untouched_parts_identical, make_zip, CONTENT_TYPES


# ---------------------------------------------------------------------------
# A1 — docx: rPr стилизованного run идентичен до/после
# ---------------------------------------------------------------------------

def test_docx_run_rpr_identical_after_resolve(docx_builder):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:rPr><w:color w:val="00FF00"/><w:sz w:val="36"/><w:b/><w:i/></w:rPr>
<w:t>[ORG_1]</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    dst = src.replace("src.docx", "out.docx")

    src_root = etree.fromstring(xml)
    src_rpr_xml = etree.tostring(src_root.find(f".//{{{W}}}rPr"))

    _, unresolved = docx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    dst_root = etree.fromstring(read_part(dst, "word/document.xml"))
    dst_rpr = dst_root.find(f".//{{{W}}}rPr")
    dst_rpr_xml = etree.tostring(dst_rpr)

    assert dst_rpr_xml == src_rpr_xml
    assert dst_root.find(f".//{{{W}}}t").text == "ООО «Ромашка»"


# ---------------------------------------------------------------------------
# A2 — xlsx: атрибут s (стиль ячейки) не меняется
# ---------------------------------------------------------------------------

def test_xlsx_cell_style_attribute_unchanged(xlsx_builder):
    shared = f'<sst xmlns="{S}" count="1" uniqueCount="1"><si><t>[ORG_1]</t></si></sst>'.encode("utf-8")
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s" s="7"><v>0</v></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", shared, sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    c = sh.find(f".//{{{S}}}c[@r='A1']")
    assert c.get("s") == "7"
    assert c.get("t") == "s"


# ---------------------------------------------------------------------------
# A3 — pptx: a:rPr заголовка слайда не меняется
# ---------------------------------------------------------------------------

def test_pptx_title_rpr_identical_after_resolve(pptx_builder):
    xml = f"""<p:sld xmlns:a="{A}" xmlns:p="{"http://schemas.openxmlformats.org/presentationml/2006/main"}"><p:cSld><p:spTree>
<p:sp><p:txBody><a:p><a:r>
<a:rPr sz="3200" b="1" i="1"><a:solidFill><a:srgbClr val="0000FF"/></a:solidFill></a:rPr>
<a:t>[ORG_1]</a:t></a:r></a:p></p:txBody></p:sp>
</p:spTree></p:cSld></p:sld>""".encode("utf-8")
    src = pptx_builder("src.pptx", xml)
    dst = src.replace("src.pptx", "out.pptx")

    src_root = etree.fromstring(xml)
    src_rpr_xml = etree.tostring(src_root.find(f".//{{{A}}}rPr"))

    _, unresolved = pptx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    dst_root = etree.fromstring(read_part(dst, "ppt/slides/slide1.xml"))
    dst_rpr_xml = etree.tostring(dst_root.find(f".//{{{A}}}rPr"))
    assert dst_rpr_xml == src_rpr_xml
    assert dst_root.find(f".//{{{A}}}t").text == "ООО «Ромашка»"


# ---------------------------------------------------------------------------
# A4 — весь архив: части, не входящие в маску адаптера, побайтово идентичны.
# Инвентарь "нетривиального оформления": картинка, диаграмма, сводная таблица,
# условное форматирование, нумерованный список.
# ---------------------------------------------------------------------------

def test_docx_full_archive_byte_identical_except_document_xml(tmp_path):
    image_bytes = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-DATA" * 10
    numbering_xml = f'<w:numbering xmlns:w="{W}"><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'.encode("utf-8")
    styles_xml = f'<w:styles xmlns:w="{W}"><w:style w:styleId="Heading1"/></w:styles>'.encode("utf-8")
    theme_xml = b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="T"/>'
    document_xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
<w:r><w:t>[ORG_1]</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")

    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "word/document.xml": document_xml,
        "word/media/image1.png": image_bytes,
        "word/numbering.xml": numbering_xml,
        "word/styles.xml": styles_xml,
        "word/theme/theme1.xml": theme_xml,
    }
    src = make_zip(tmp_path / "src.docx", parts)
    dst = str(tmp_path / "out.docx")

    _, unresolved = docx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    assert_untouched_parts_identical(src, dst, touched_names={"word/document.xml"})


def test_pptx_full_archive_byte_identical_except_slide_xml(tmp_path):
    chart_xml = b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>'
    master_xml = f'<p:sldMaster xmlns:p="{P_NS}" xmlns:a="{A}"><p:cSld><p:spTree/></p:cSld></p:sldMaster>'.encode("utf-8")
    theme_xml = f'<a:theme xmlns:a="{A}" name="T"/>'.encode("utf-8")
    slide_xml = f"""<p:sld xmlns:a="{A}" xmlns:p="{P_NS}"><p:cSld><p:spTree>
<p:sp><p:txBody><a:p><a:r><a:t>[ORG_1]</a:t></a:r></a:p></p:txBody></p:sp>
</p:spTree></p:cSld></p:sld>""".encode("utf-8")

    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "ppt/slides/slide1.xml": slide_xml,
        "ppt/charts/chart1.xml": chart_xml,
        "ppt/slideMasters/slideMaster1.xml": master_xml,
        "ppt/theme/theme1.xml": theme_xml,
    }
    src = make_zip(tmp_path / "src.pptx", parts)
    dst = str(tmp_path / "out.pptx")

    _, unresolved = pptx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    assert_untouched_parts_identical(src, dst, touched_names={"ppt/slides/slide1.xml"})


def test_xlsx_full_archive_byte_identical_except_shared_strings(tmp_path):
    # sheet1 не содержит inline-строк -> адаптер не переписывает эту часть вовсе
    # (см. xlsx_rewriter._build_groups / "не переписываем часть, чтобы сохранить
    # её байт-в-байт"); conditionalFormatting внутри неё тоже остаётся нетронутым.
    styles_xml = f'<styleSheet xmlns="{S}"><fonts count="1"><font><sz val="11"/></font></fonts></styleSheet>'.encode("utf-8")
    pivot_xml = f'<pivotTableDefinition xmlns="{S}" name="Pivot1"/>'.encode("utf-8")
    chart_xml = b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>'
    shared = f'<sst xmlns="{S}" count="1" uniqueCount="1"><si><t>[ORG_1]</t></si></sst>'.encode("utf-8")
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s" s="0"><v>0</v></c></row>'
        f'</sheetData>'
        f'<conditionalFormatting sqref="A1:A10">'
        f'<cfRule type="cellIs" dxfId="0" priority="1" operator="greaterThan"><formula>0</formula></cfRule>'
        f'</conditionalFormatting>'
        f'</worksheet>'
    ).encode("utf-8")

    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "xl/sharedStrings.xml": shared,
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": styles_xml,
        "xl/pivotTables/pivotTable1.xml": pivot_xml,
        "xl/charts/chart1.xml": chart_xml,
    }
    src = make_zip(tmp_path / "src.xlsx", parts)
    dst = str(tmp_path / "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    # sheet1.xml не входит в touched: адаптер не нашёл в нём inline-строк и
    # не переписал часть -> она тоже обязана остаться байт-в-байт, включая
    # <conditionalFormatting>.
    assert_untouched_parts_identical(src, dst, touched_names={"xl/sharedStrings.xml"})
