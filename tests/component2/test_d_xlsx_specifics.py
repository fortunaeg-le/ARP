"""Группа D — самая тонкая часть: Excel.

Главный риск: значение токена — это, как правило, "похожая на число" строка
(ИНН, счёт с ведущими нулями, телефон). Ячейка обязана остаться строкой:
тип `t`, стиль `s` и `<v>`-индекс не тронуты, ведущие нули не съедены,
значение не превращается в число/экспоненту.
"""
from lxml import etree

from xlsx_rewriter import rewrite as xlsx_rewrite

from conftest import S, read_part, resolver


# ---------------------------------------------------------------------------
# D1 — ИНН/счёт с ведущими нулями остаётся строкой посимвольно
# ---------------------------------------------------------------------------

def test_leading_zeros_and_type_preserved_for_account_number(xlsx_builder):
    shared = f'<sst xmlns="{S}" count="1" uniqueCount="1"><si><t>[ACC_1]</t></si></sst>'.encode("utf-8")
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s" s="2"><v>0</v></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", shared, sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[ACC_1]": "00123456789"}))
    assert unresolved == []

    ss_root = etree.fromstring(read_part(dst, "xl/sharedStrings.xml"))
    text = ss_root.find(f".//{{{S}}}t").text
    assert text == "00123456789"  # ведущие нули не съедены, не превратилось в число

    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    c = sh.find(f".//{{{S}}}c[@r='A1']")
    assert c.get("t") == "s"       # тип ячейки — строка (индекс в sharedStrings)
    assert c.get("s") == "2"       # стиль не тронут
    assert c.find(f"{{{S}}}v").text == "0"  # <v>-индекс не тронут


def test_inn_value_not_turned_into_number_or_exponent(xlsx_builder):
    shared = f'<sst xmlns="{S}" count="1" uniqueCount="1"><si><t>[INN_1]</t></si></sst>'.encode("utf-8")
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", shared, sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[INN_1]": "7701234567"}))
    assert unresolved == []

    ss_root = etree.fromstring(read_part(dst, "xl/sharedStrings.xml"))
    text = ss_root.find(f".//{{{S}}}t").text
    assert text == "7701234567"
    assert "E+" not in text and "e+" not in text


# ---------------------------------------------------------------------------
# D2 — один <si>, на который ссылаются 3 ячейки -> раскрыт во всех трёх
# ---------------------------------------------------------------------------

def test_shared_si_referenced_by_three_cells_all_resolved(xlsx_builder):
    shared = f'<sst xmlns="{S}" count="3" uniqueCount="1"><si><t>[ORG_1]</t></si></sst>'.encode("utf-8")
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="s"><v>0</v></c>'
        f'<c r="B1" t="s"><v>0</v></c>'
        f'<c r="C1" t="s"><v>0</v></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", shared, sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    # Общая строка одна: раскрытие произошло в единственном <si>, и все три
    # ячейки (v=0) продолжают на неё указывать -> "раскрыт во всех трёх".
    ss_root = etree.fromstring(read_part(dst, "xl/sharedStrings.xml"))
    assert [t.text for t in ss_root.iter(f"{{{S}}}t")] == ["ООО «Ромашка»"]
    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    for ref in ("A1", "B1", "C1"):
        c = sh.find(f".//{{{S}}}c[@r='{ref}']")
        assert c.get("t") == "s"
        assert c.find(f"{{{S}}}v").text == "0"


# ---------------------------------------------------------------------------
# D3 — count/uniqueCount у <sst> не пересчитаны
# ---------------------------------------------------------------------------

def test_sst_count_and_unique_count_not_recalculated(xlsx_builder):
    shared = (
        f'<sst xmlns="{S}" count="42" uniqueCount="17">'
        f'<si><t>[ORG_1]</t></si><si><t>[ORG_99]</t></si>'
        f'</sst>'
    ).encode("utf-8")
    sheet = f'<worksheet xmlns="{S}"><sheetData/></worksheet>'.encode("utf-8")
    src = xlsx_builder("src.xlsx", shared, sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))

    root = etree.fromstring(read_part(dst, "xl/sharedStrings.xml"))
    assert root.get("count") == "42"
    assert root.get("uniqueCount") == "17"


# ---------------------------------------------------------------------------
# D4 — inline-строки (<c t="inlineStr">) обрабатываются
# ---------------------------------------------------------------------------

def test_inline_string_cell_resolved(xlsx_builder):
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1" t="inlineStr" s="4"><is><t>Клиент: [ORG_1]</t></is></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", sheet_xml=sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))
    assert unresolved == []

    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    c = sh.find(f".//{{{S}}}c[@r='A1']")
    assert c.get("t") == "inlineStr"  # тип не изменён
    assert c.get("s") == "4"          # стиль не изменён
    assert c.find(f"{{{S}}}is/{{{S}}}t").text == "Клиент: ООО «Ромашка»"


# ---------------------------------------------------------------------------
# D5 — токен внутри формулы <f> НЕ заменяется и НЕ попадает в unresolved
# (задокументированное поведение — фиксируем как ожидаемое, не как дефект).
# ---------------------------------------------------------------------------

def test_token_inside_formula_left_untouched_and_not_reported(xlsx_builder):
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1"><c r="A1"><f>CONCATENATE("[ORG_1]", A2)</f><v>0</v></c></row>'
        f'</sheetData></worksheet>'
    ).encode("utf-8")
    src = xlsx_builder("src.xlsx", sheet_xml=sheet)
    dst = src.replace("src.xlsx", "out.xlsx")

    _, unresolved = xlsx_rewrite(src, dst, resolver({"[ORG_1]": "ООО «Ромашка»"}))

    sh = etree.fromstring(read_part(dst, "xl/worksheets/sheet1.xml"))
    assert sh.find(f".//{{{S}}}f").text == 'CONCATENATE("[ORG_1]", A2)'
    assert "[ORG_1]" not in unresolved
