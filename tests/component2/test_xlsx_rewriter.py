"""Быстрые тесты приёмки блока 11 (xlsx_rewriter.py).

Разовая проверка приёмки (не часть сдаваемого модуля). Проверяет пункты приёмки
из SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 11:
  - [ORG_1] в B2 (стиль s=1) раскрыт, стиль/тип ячейки не изменились;
  - [INN_1] в C2 остался строкой (t="s"), не превратился в число, <v>-индекс цел;
  - rich text, где токен разорван между двумя <r>, собран и раскрыт;
  - inline-строка (<c t="inlineStr">) раскрыта;
  - один <si> на две ячейки раскрыт для обеих (общие строки);
  - неизвестный токен не тронут и попал в unresolved;
  - формула <f> с токеном не тронута и в unresolved не попала;
  - count/uniqueCount у <sst> не пересчитаны;
  - styles.xml/workbook.xml байт-в-байт, namelist совпадает, zip валиден.

openpyxl запрещён — .xlsx собирается вручную через zipfile.
"""

import zipfile

import pytest
from lxml import etree

from xlsx_rewriter import rewrite

from conftest import CONTENT_TYPES

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

STYLES = (
    '<styleSheet xmlns="%s"><fonts count="1"><font><sz val="14"/><b/></font>'
    '</fonts></styleSheet>' % S
).encode("utf-8")

WORKBOOK = (
    '<workbook xmlns="%s"><sheets><sheet name="S1" sheetId="1" r:id="rId1" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    '/></sheets></workbook>' % S
).encode("utf-8")

# si0 [ORG_1]; si1 [INN_1]; si2 rich text с токеном, разорванным между <r>;
# si3 неизвестный [ORG_99]; si4 общий [ORG_1] (тот же, что si0).
SHARED = (
    '<sst xmlns="%s" count="6" uniqueCount="5">'
    '<si><t>[ORG_1]</t></si>'
    '<si><t>[INN_1]</t></si>'
    '<si><r><rPr><sz val="14"/><b/><color rgb="FFFF0000"/></rPr>'
    '<t xml:space="preserve">[PHONE</t></r><r><t>_1] доб. 5</t></r></si>'
    '<si><t>Нет в сессии: [ORG_99]</t></si>'
    '<si><t>[ORG_1]</t></si>'
    '</sst>' % S
).encode("utf-8")

# B2->si0, C2->si1 (оба стиль s=1), D2 inline-строка, E3 формула с токеном.
SHEET = (
    '<worksheet xmlns="%s"><sheetData>'
    '<row r="2"><c r="B2" t="s" s="1"><v>0</v></c>'
    '<c r="C2" t="s" s="1"><v>1</v></c>'
    '<c r="D2" t="inlineStr" s="1"><is><t>inline [INN_1] tail</t></is></c></row>'
    '<row r="3"><c r="E3"><f>SUM(A1:A2)+[ORG_1]</f><v>3</v></c></row>'
    '</sheetData></worksheet>' % S
).encode("utf-8")


def make_xlsx(path, shared=SHARED, sheet=SHEET) -> str:
    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "xl/workbook.xml": WORKBOOK,
        "xl/styles.xml": STYLES,
        "xl/sharedStrings.xml": shared,
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return str(path)


def read_part(path, name) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name)


def si_texts(path):
    root = etree.fromstring(read_part(path, "xl/sharedStrings.xml"))
    return root, ["".join(t.text or "" for t in si.iter("{%s}t" % S))
                  for si in root.iter("{%s}si" % S)]


def resolver(mapping):
    return lambda token: mapping.get(token)


MAP = {
    "[ORG_1]": "ООО «Ромашка»",
    "[INN_1]": "7701234567",
    "[PHONE_1]": "+7 495 123-45-67",
}


@pytest.fixture
def out(tmp_path):
    src = make_xlsx(tmp_path / "in.xlsx")
    dst = str(tmp_path / "out.xlsx")
    _, unresolved = rewrite(src, dst, resolver(MAP))
    return src, dst, unresolved


def test_org_and_inn_resolved_in_shared_strings(out):
    _, dst, _ = out
    _, texts = si_texts(dst)
    assert texts[0] == "ООО «Ромашка»"
    assert texts[1] == "7701234567"  # ИНН остался строкой


def test_inn_cell_keeps_type_style_and_v_index(out):
    _, dst, _ = out
    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    c2 = sh.find(".//{%s}c[@r='C2']" % S)
    assert c2.get("t") == "s"           # тип ячейки не изменён
    assert c2.get("s") == "1"           # индекс стиля не изменён
    assert c2.find("{%s}v" % S).text == "1"  # <v>-индекс не тронут


def test_rich_text_token_split_across_runs(out):
    _, dst, _ = out
    _, texts = si_texts(dst)
    assert texts[2] == "+7 495 123-45-67 доб. 5"


def test_inline_string_resolved(out):
    _, dst, _ = out
    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    t = sh.find(".//{%s}c[@r='D2']/{%s}is/{%s}t" % (S, S, S))
    assert t.text == "inline 7701234567 tail"


def test_shared_si_expands_for_all_cells(out):
    _, dst, _ = out
    _, texts = si_texts(dst)
    assert texts[0] == texts[4] == "ООО «Ромашка»"  # один токен на B2 и si4


def test_unknown_token_untouched_and_reported(out):
    _, dst, unresolved = out
    _, texts = si_texts(dst)
    assert texts[3] == "Нет в сессии: [ORG_99]"
    assert unresolved == ["[ORG_99]"]


def test_formula_with_token_not_touched(out):
    _, dst, unresolved = out
    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    assert sh.find(".//{%s}f" % S).text == "SUM(A1:A2)+[ORG_1]"
    assert "[ORG_1]" not in unresolved  # токен в формуле не идёт в unresolved


def test_sst_count_not_recalculated(out):
    _, dst, _ = out
    root, _ = si_texts(dst)
    assert root.get("count") == "6"
    assert root.get("uniqueCount") == "5"


def test_untouched_parts_byte_for_byte_and_zip_valid(out):
    src, dst, _ = out
    assert read_part(dst, "xl/styles.xml") == read_part(src, "xl/styles.xml")
    assert read_part(dst, "xl/workbook.xml") == read_part(src, "xl/workbook.xml")
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        assert sorted(b.namelist()) == sorted(a.namelist())
        assert b.testzip() is None
