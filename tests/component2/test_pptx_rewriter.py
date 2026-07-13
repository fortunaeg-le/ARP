"""Быстрые тесты приёмки блока 10 (pptx_rewriter.py).

Не подключены к общему тестовому набору проекта (см. правило блока: "тесты не
пишешь" — это разовая проверка приёмки, а не часть сдаваемого блока).
Проверяет пункты приёмки из SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 10:
  - токен в стилизованном run (заголовок) раскрывается, оформление не меняется;
  - токен, разбитый на два <a:r> внутри ячейки таблицы, собирается и раскрывается;
  - неизвестный токен остаётся как есть и попадает в unresolved;
  - part'ы вне маски (тема, мастера, макеты) переносятся байт-в-байт;
  - токен в заметках докладчика (notesSlide) раскрывается;
  - <a:br> как псевдо-разделитель не ломает соседние run'ы.
"""

import zipfile

import pytest
from lxml import etree

from pptx_rewriter import rewrite

from conftest import CONTENT_TYPES

A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def make_pptx(path, parts: dict) -> str:
    # Настоящий .pptx всегда содержит [Content_Types].xml; без него read_zip_parts
    # (блок 8) считает файл не-OOXML. Добавляем, если тест не задал его сам.
    parts = {"[Content_Types].xml": CONTENT_TYPES, **parts}
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return str(path)


def read_part(path, name) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name)


def styled_title_slide() -> bytes:
    return f"""<p:sld xmlns:a="{A}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>
<p:sp><p:txBody>
<a:p><a:r><a:rPr sz="4400" b="1"><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></a:rPr><a:t>[ORG_1]</a:t></a:r></a:p>
</p:txBody></p:sp>
</p:spTree></p:cSld></p:sld>""".encode("utf-8")


def table_cell_split_slide() -> bytes:
    return f"""<p:sld xmlns:a="{A}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>
<a:tbl><a:tr><a:tc><a:txBody>
<a:p><a:r><a:t>[PHONE</a:t></a:r><a:r><a:t>_1] cell</a:t></a:r></a:p>
</a:txBody></a:tc></a:tr></a:tbl>
</p:spTree></p:cSld></p:sld>""".encode("utf-8")


def unknown_token_slide() -> bytes:
    return f"""<p:sld xmlns:a="{A}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>
<p:sp><p:txBody><a:p><a:r><a:t>[ORG_99]</a:t></a:r></a:p></p:txBody></p:sp>
</p:spTree></p:cSld></p:sld>""".encode("utf-8")


def notes_slide() -> bytes:
    return f"""<p:notes xmlns:a="{A}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>
<p:sp><p:txBody><a:p><a:r><a:t>Заметка: [ORG_1]</a:t></a:r></a:p></p:txBody></p:sp>
</p:spTree></p:cSld></p:notes>""".encode("utf-8")


def linebreak_slide() -> bytes:
    return f"""<p:sld xmlns:a="{A}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>
<p:sp><p:txBody><a:p><a:r><a:t>Line1</a:t></a:r><a:br/><a:r><a:t>Line2</a:t></a:r></a:p></p:txBody></p:sp>
</p:spTree></p:cSld></p:sld>""".encode("utf-8")


def resolver(mapping):
    return lambda token: mapping.get(token)


def test_styled_title_replaced_and_formatting_untouched(tmp_path):
    src = make_pptx(tmp_path / "src.pptx", {"ppt/slides/slide1.xml": styled_title_slide()})
    dst = str(tmp_path / "out.pptx")

    _, unresolved = rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "ppt/slides/slide1.xml"))
    t = root.find(f".//{{{A}}}t")
    assert t.text == "ООО «Ромашка»"
    rpr = root.find(f".//{{{A}}}rPr")
    assert rpr.get("sz") == "4400"
    assert rpr.get("b") == "1"
    assert rpr.find(f"{{{A}}}solidFill/{{{A}}}srgbClr").get("val") == "FF0000"


def test_token_split_across_two_runs_in_table_cell(tmp_path):
    src = make_pptx(tmp_path / "src.pptx", {"ppt/slides/slide1.xml": table_cell_split_slide()})
    dst = str(tmp_path / "out.pptx")

    _, unresolved = rewrite(src, dst, resolver({"[PHONE_1]": "+7 495 123-45-67"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "ppt/slides/slide1.xml"))
    ts = [t.text or "" for t in root.findall(f".//{{{A}}}t")]
    assert ts == ["+7 495 123-45-67", " cell"]


def test_unknown_token_left_as_is_and_reported(tmp_path):
    src = make_pptx(tmp_path / "src.pptx", {"ppt/slides/slide1.xml": unknown_token_slide()})
    dst = str(tmp_path / "out.pptx")

    _, unresolved = rewrite(src, dst, resolver({}))

    assert unresolved == ["[ORG_99]"]
    root = etree.fromstring(read_part(dst, "ppt/slides/slide1.xml"))
    assert root.find(f".//{{{A}}}t").text == "[ORG_99]"


def test_untouched_parts_copied_byte_for_byte(tmp_path):
    theme_bytes = f'<a:theme xmlns:a="{A}" name="Test"/>'.encode("utf-8")
    src = make_pptx(tmp_path / "src.pptx", {
        "ppt/slides/slide1.xml": unknown_token_slide(),
        "ppt/theme/theme1.xml": theme_bytes,
    })
    dst = str(tmp_path / "out.pptx")

    rewrite(src, dst, resolver({}))

    assert read_part(dst, "ppt/theme/theme1.xml") == theme_bytes


def test_token_in_notes_slide_resolved(tmp_path):
    src = make_pptx(tmp_path / "src.pptx", {
        "ppt/slides/slide1.xml": unknown_token_slide(),
        "ppt/notesSlides/notesSlide1.xml": notes_slide(),
    })
    dst = str(tmp_path / "out.pptx")

    _, unresolved = rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))

    assert unresolved == ["[ORG_99]"]
    notes_root = etree.fromstring(read_part(dst, "ppt/notesSlides/notesSlide1.xml"))
    assert notes_root.find(f".//{{{A}}}t").text == "Заметка: ООО «Ромашка»"


def test_slide_master_not_touched(tmp_path):
    master_bytes = f'<p:sldMaster xmlns:a="{A}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sldMaster>'.encode("utf-8")
    src = make_pptx(tmp_path / "src.pptx", {
        "ppt/slides/slide1.xml": unknown_token_slide(),
        "ppt/slideMasters/slideMaster1.xml": master_bytes,
    })
    dst = str(tmp_path / "out.pptx")

    rewrite(src, dst, resolver({}))

    assert read_part(dst, "ppt/slideMasters/slideMaster1.xml") == master_bytes


def test_linebreak_pseudo_separator_does_not_break_runs(tmp_path):
    src = make_pptx(tmp_path / "src.pptx", {"ppt/slides/slide1.xml": linebreak_slide()})
    dst = str(tmp_path / "out.pptx")

    _, unresolved = rewrite(src, dst, resolver({}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "ppt/slides/slide1.xml"))
    ts = [t.text for t in root.findall(f".//{{{A}}}t")]
    assert ts == ["Line1", "Line2"]
