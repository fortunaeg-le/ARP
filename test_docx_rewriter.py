"""Быстрые тесты приёмки блока 9 (docx_rewriter.py).

Не подключены к общему тестовому набору проекта (см. правило блока: "тесты не
пишешь" — это разовая проверка приёмки, а не часть сдаваемого блока).
Проверяет пункты приёмки из SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 9:
  - токен в стилизованном run раскрывается, оформление run'а и соседей не меняется;
  - токен, разбитый на два <w:r>, собирается и раскрывается одним значением;
  - токен в колонтитуле (header) раскрывается;
  - неизвестный токен остаётся как есть и попадает в unresolved;
  - part'ы вне маски (картинки и т.п.) переносятся байт-в-байт;
  - токен в ячейке таблицы раскрывается;
  - удалённый текст рецензирования (<w:delText>) не трогается.
"""

import zipfile

import pytest
from lxml import etree

from docx_rewriter import rewrite

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NSMAP = {"w": W}


def make_docx(path, parts: dict) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return str(path)


def read_part(path, name) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name)


def styled_run_document() -> bytes:
    return f"""<w:document xmlns:w="{W}"><w:body>
<w:p>
  <w:r><w:t xml:space="preserve">Before </w:t></w:r>
  <w:r>
    <w:rPr><w:color w:val="FF0000"/><w:sz w:val="52"/><w:b/></w:rPr>
    <w:t>[PERSON_1]</w:t>
  </w:r>
  <w:r><w:t xml:space="preserve"> after.</w:t></w:r>
</w:p>
</w:body></w:document>""".encode("utf-8")


def split_run_document() -> bytes:
    return f"""<w:document xmlns:w="{W}"><w:body>
<w:p>
  <w:r><w:t>[PERSON</w:t></w:r>
  <w:r><w:t>_1]</w:t></w:r>
</w:p>
</w:body></w:document>""".encode("utf-8")


def unknown_token_document() -> bytes:
    return f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>[ORG_99]</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")


def table_document() -> bytes:
    return f"""<w:document xmlns:w="{W}"><w:body>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>[INN_1]</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>""".encode("utf-8")


def deleted_text_document() -> bytes:
    return f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:del><w:r><w:delText>[PERSON_1]</w:delText></w:r></w:del></w:p>
</w:body></w:document>""".encode("utf-8")


def header_document() -> bytes:
    return f"""<w:hdr xmlns:w="{W}">
<w:p><w:r><w:t>[ORG_1]</w:t></w:r></w:p>
</w:hdr>""".encode("utf-8")


def resolver(mapping):
    return lambda token: mapping.get(token)


def test_styled_run_replaced_and_formatting_untouched(tmp_path):
    src = make_docx(tmp_path / "src.docx", {"word/document.xml": styled_run_document()})
    dst = str(tmp_path / "out.docx")

    unresolved = rewrite(src, dst, resolver({"[PERSON_1]": "Иванов Иван Иванович"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    runs = root.findall(f".//{{{W}}}r")
    texts = [r.find(f"{{{W}}}t").text for r in runs]
    assert texts == ["Before ", "Иванов Иван Иванович", " after."]

    styled_rpr = runs[1].find(f"{{{W}}}rPr")
    assert styled_rpr.find(f"{{{W}}}color").get(f"{{{W}}}val") == "FF0000"
    assert styled_rpr.find(f"{{{W}}}sz").get(f"{{{W}}}val") == "52"
    assert styled_rpr.find(f"{{{W}}}b") is not None
    assert runs[0].find(f"{{{W}}}rPr") is None
    assert runs[2].find(f"{{{W}}}rPr") is None


def test_token_split_across_two_runs_is_assembled(tmp_path):
    src = make_docx(tmp_path / "src.docx", {"word/document.xml": split_run_document()})
    dst = str(tmp_path / "out.docx")

    unresolved = rewrite(src, dst, resolver({"[PERSON_1]": "Иванов Иван Иванович"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    ts = [t.text or "" for t in root.findall(f".//{{{W}}}t")]
    assert ts == ["Иванов Иван Иванович", ""]


def test_unknown_token_left_as_is_and_reported(tmp_path):
    src = make_docx(tmp_path / "src.docx", {"word/document.xml": unknown_token_document()})
    dst = str(tmp_path / "out.docx")

    unresolved = rewrite(src, dst, resolver({}))

    assert unresolved == ["[ORG_99]"]
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    assert root.find(f".//{{{W}}}t").text == "[ORG_99]"


def test_untouched_parts_copied_byte_for_byte(tmp_path):
    image_bytes = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-DATA"
    src = make_docx(tmp_path / "src.docx", {
        "word/document.xml": unknown_token_document(),
        "word/media/image1.png": image_bytes,
    })
    dst = str(tmp_path / "out.docx")

    rewrite(src, dst, resolver({}))

    assert read_part(dst, "word/media/image1.png") == image_bytes


def test_token_in_table_cell_resolved(tmp_path):
    src = make_docx(tmp_path / "src.docx", {"word/document.xml": table_document()})
    dst = str(tmp_path / "out.docx")

    unresolved = rewrite(src, dst, resolver({"[INN_1]": "7701234567"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    assert root.find(f".//{{{W}}}t").text == "7701234567"


def test_deleted_review_text_not_touched(tmp_path):
    src = make_docx(tmp_path / "src.docx", {"word/document.xml": deleted_text_document()})
    dst = str(tmp_path / "out.docx")

    unresolved = rewrite(src, dst, resolver({"[PERSON_1]": "Иванов Иван Иванович"}))

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    assert root.find(f".//{{{W}}}delText").text == "[PERSON_1]"


def test_token_in_header_resolved(tmp_path):
    src = make_docx(tmp_path / "src.docx", {
        "word/document.xml": unknown_token_document(),
        "word/header1.xml": header_document(),
    })
    dst = str(tmp_path / "out.docx")

    unresolved = rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))

    assert unresolved == ["[ORG_99]"]
    hdr_root = etree.fromstring(read_part(dst, "word/header1.xml"))
    assert hdr_root.find(f".//{{{W}}}t").text == "ООО «Ромашка»"
