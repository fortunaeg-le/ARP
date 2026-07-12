"""Разовый внешний тест блока 4 (tokenizer.py). Ничего в коде не меняет."""
import uuid
from models import SourceDocument, TextSegment, Entity
from tokenizer import tokenize, build_plain_text

def E(seg, start, end, text, etype, detector, conf=1.0):
    return Entity(id=str(uuid.uuid4()), segment_id=seg, start=start, end=end,
                  original_text=text, entity_type=etype, detector=detector, confidence=conf)

def show(title, anon, entities):
    print(f"\n=== {title} ===")
    print(repr(anon))
    for e in entities:
        print(f"  {e.entity_type:12} {e.original_text!r:30} -> {e.token}")

# --- Тест 1: повторное упоминание одинаковой организации + разное написание ---
text1 = 'ООО "Ромашка" заключает договор с ООО "Ромашка" и с "РОМАШКА".'
seg1 = TextSegment(id="p0", text=text1, source_type="docx_paragraph", metadata={})
first = text1.index('"Ромашка"')
second = text1.index('"Ромашка"', first + 1)
third = text1.index('"РОМАШКА"')
ents1 = [
    E("p0", first, first + 9, '"Ромашка"', "ORG", "ner"),
    E("p0", second, second + 9, '"Ромашка"', "ORG", "ner"),
    E("p0", third, third + 9, '"РОМАШКА"', "ORG", "ner"),
]
doc1 = SourceDocument(segments=[seg1], source_format="txt", source_path="t1")
anon1, kept1 = tokenize(doc1, ents1, "entity_types.yaml")
show("Тест 1: повтор одинакового написания -> один токен; другой регистр -> другой токен", anon1, kept1)
assert kept1[0].token == kept1[1].token, "Одинаковое написание должно давать один токен"
assert kept1[0].token != kept1[2].token, "Другой регистр должен давать другой токен"
print("OK: правило переиспользования токенов подтверждено")

# --- Тест 2: пересечение regex vs ner (ИНН внутри названия организации) ---
seg2 = TextSegment(id="p0", text='ООО "Ромашка ИНН 7707083893" зарегистрировано.',
                    source_type="docx_paragraph", metadata={})
# ORG (ner) охватывает весь кусок с ИНН внутри; INN (regex) - только цифры
org_span = (5, 28)   # '"Ромашка ИНН 7707083893"'
inn_span = (17, 27)  # '7707083893'
ents2 = [
    E("p0", org_span[0], org_span[1], seg2.text[org_span[0]:org_span[1]], "ORG", "ner"),
    E("p0", inn_span[0], inn_span[1], seg2.text[inn_span[0]:inn_span[1]], "INN", "regex"),
]
doc2 = SourceDocument(segments=[seg2], source_format="txt", source_path="t2")
anon2, kept2 = tokenize(doc2, ents2, "entity_types.yaml")
show("Тест 2: regex (INN) должен победить пересекающийся ner (ORG)", anon2, kept2)
assert len(kept2) == 1 and kept2[0].detector == "regex" and kept2[0].entity_type == "INN"
print("OK: regex побеждает ner при пересечении")

# --- Тест 3: BIK/KPP конфликт ("04"-число, 9 цифр начинающиеся на 04) ---
text3 = 'БИК 044525225, КПП 773601001, счет 40702810900000012345.'
seg3 = TextSegment(id="p0", text=text3, source_type="docx_paragraph", metadata={})
bik_str = "044525225"
bik_start = text3.index(bik_str)
acc_str = "40702810900000012345"
acc_start = text3.index(acc_str)
ents3 = [
    E("p0", bik_start, bik_start + len(bik_str), bik_str, "BIK", "regex"),
    E("p0", bik_start, bik_start + len(bik_str), bik_str, "KPP", "regex"),
    E("p0", acc_start, acc_start + len(acc_str), acc_str, "BANK_ACCOUNT", "regex"),
]
doc3 = SourceDocument(segments=[seg3], source_format="txt", source_path="t3")
anon3, kept3 = tokenize(doc3, ents3, "entity_types.yaml")
show("Тест 3: BIK/KPP конфликт (один токен на месте) + BANK_ACCOUNT -> [ACCOUNT_N]", anon3, kept3)
bik_kpp_kept = [e for e in kept3 if e.start == 4]
assert len(bik_kpp_kept) == 1, "На пересекающемся месте BIK/KPP должен остаться ровно один токен"
assert bik_kpp_kept[0].entity_type == "BIK", "По приоритету _REGEX_PRIORITY BIK сильнее KPP"
acc_kept = [e for e in kept3 if e.entity_type == "BANK_ACCOUNT"][0]
assert acc_kept.token.startswith("[ACCOUNT_"), f"token_prefix должен быть ACCOUNT, получено {acc_kept.token}"
assert "[BANK_ACCOUNT_" not in anon3
print("OK: BIK/KPP -> один токен; BANK_ACCOUNT -> [ACCOUNT_N], не [BANK_ACCOUNT_N]")

# --- Тест 4: цепочка из трёх пересекающихся сущностей A-B-C, A и C не пересекаются ---
# Индексы: A=[0,10), B=[5,15), C=[12,20) -- A и C не пересекаются между собой (10 <= 12)
seg4 = TextSegment(id="p0", text="X" * 25, source_type="docx_paragraph", metadata={})
A = E("p0", 0, 10, "X" * 10, "ORG", "ner", conf=0.5)
B = E("p0", 5, 15, "X" * 10, "ORG", "ner", conf=0.9)   # выше confidence, длина совпадает -> B сильнее A и C
C = E("p0", 12, 20, "X" * 8, "PERSON", "ner", conf=0.5)
ents4 = [A, B, C]
doc4 = SourceDocument(segments=[seg4], source_format="txt", source_path="t4")
anon4, kept4 = tokenize(doc4, ents4, "entity_types.yaml")
show("Тест 4: A-B-C цепочка, победитель B, A и C должны быть удалены (оба пересекаются с B)", anon4, kept4)
assert len(kept4) == 1 and kept4[0] is B
print("OK: удалены только пересекающиеся с победителем (тут оба A и C пересекались с B напрямую)")

# --- Тест 4b: настоящая цепочка, где C НЕ пересекается с итоговым победителем напрямую, но через B ---
# A=[0,10) слабый, B=[8,20) самый длинный и сильный (победитель), C=[18,25) не пересекается с A, пересекается с B
seg4b = TextSegment(id="p0", text="Y" * 30, source_type="docx_paragraph", metadata={})
A2 = E("p0", 0, 10, "Y" * 10, "PERSON", "ner", conf=0.9)
B2 = E("p0", 8, 20, "Y" * 12, "ORG", "ner", conf=0.9)     # длиннее всех -> побеждает
C2 = E("p0", 18, 25, "Y" * 7, "PERSON", "ner", conf=0.9)
ents4b = [A2, B2, C2]
doc4b = SourceDocument(segments=[seg4b], source_format="txt", source_path="t4b")
anon4b, kept4b = tokenize(doc4b, ents4b, "entity_types.yaml")
show("Тест 4b: победитель B2 (самый длинный), A2 и C2 оба пересекаются с B2 -> оба удалены", anon4b, kept4b)
assert len(kept4b) == 1 and kept4b[0] is B2
print("OK")

# --- Тест 5: соседний текст и знаки препинания не съедаются ---
seg5 = TextSegment(id="p0", text='Оплата: 5000 руб., получатель ООО "Вектор".',
                    source_type="docx_paragraph", metadata={})
sum_span = (8, 17)  # '5000 руб.'
org_span5 = (31, 43)  # '"Вектор"' approx -- пересчитаем ниже
text5 = seg5.text
sum_text = "5000 руб."
sum_start = text5.index(sum_text)
org_text = '"Вектор"'
org_start = text5.index(org_text)
ents5 = [
    E("p0", sum_start, sum_start+len(sum_text), sum_text, "SUM", "regex"),
    E("p0", org_start, org_start+len(org_text), org_text, "ORG", "ner"),
]
doc5 = SourceDocument(segments=[seg5], source_format="txt", source_path="t5")
anon5, kept5 = tokenize(doc5, ents5, "entity_types.yaml")
show("Тест 5: проверка сохранности окружающего текста/пунктуации", anon5, kept5)
assert anon5 == 'Оплата: [SUM_1], получатель ООО [ORG_1].', anon5
print("OK: текст вокруг токенов не повреждён")

# --- Тест 6: таблица из 3+ строк - порядок сборки ---
cells = []
for row in range(4):
    for col in range(2):
        cells.append(TextSegment(id=f"t0_r{row}_c{col}", text=f"r{row}c{col}",
                                  source_type="docx_table_cell",
                                  metadata={"table_index": 0, "row_index": row, "col_index": col}))
doc6 = SourceDocument(segments=cells, source_format="docx", source_path="t6")
plain6 = build_plain_text(doc6)
show("Тест 6: порядок строк таблицы (build_plain_text)", plain6, [])
expected6 = "r0c0 | r0c1\nr1c0 | r1c1\nr2c0 | r2c1\nr3c0 | r3c1"
assert plain6 == expected6, plain6
print("OK: строки таблицы идут по порядку сверху вниз")

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
