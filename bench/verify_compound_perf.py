# -*- coding: utf-8 -*-
import os, sys, io, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from models import SourceDocument, TextSegment
from regex_detector import detect_regex
from ner_detector import detect_ner
from syntax_compound import merge_compound_entities
from tokenizer import tokenize, _detect_boundary_entities
CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")
OUT = io.StringIO()
def w(*a): OUT.write(" ".join(str(x) for x in a)+"\n")

def compound(text):
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<c>")
    ents = detect_regex(doc, CFG) + detect_ner(doc, CFG)
    pre = [(e.entity_type, e.original_text) for e in ents]
    m = merge_compound_entities(doc, ents)
    post = [(e.entity_type, e.original_text) for e in m]
    return pre, post

w("========== C-доп. Граница ложного склеивания PERSON+ORG ==========")
PROBE = [
    "Генеральный директор Иванов И.И. ООО «Ромашка»",
    "ООО «Ромашка» директор Иванов И.И.",
    "Директор Иванов И.И. ООО «Ромашка»",
    "От Поставщика ООО «Ромашка» Иванов И.И.",
    "Свидетель Иванов И.И. ООО «Ромашка» подтвердил",
    "ООО «Ромашка» Иванов Иван Иванович",
]
for text in PROBE:
    pre, post = compound(text)
    pre_orgs = [t for k,t in pre if k=="ORG"]; pre_per=[t for k,t in pre if k=="PERSON"]
    post_orgs = [t for k,t in post if k=="ORG"]; post_per=[t for k,t in post if k=="PERSON"]
    merged = any(("Иванов" in o) for o in post_orgs)
    w(f"  {'СЛИЯНИЕ!' if merged else 'ok':9s} {text!r}")
    w(f"     до:  ORG={pre_orgs} PER={pre_per}")
    w(f"     после:ORG={post_orgs} PER={post_per}")

# ================= E. Performance =================
w("\n========== E. Производительность (мой замер) ==========")
import random
random.seed(1)  # note: Math.random unavailable in workflow, but plain python fine here
CITIES=["Москва","Казань","Уфа","Пермь","Сочи","Тверь","Омск","Тюмень"]
STREETS=["Ленина","Мира","Советская","Баумана","Победы","Кирова"]
def mkseg(i):
    r = i % 10
    if r==0: return f"г. {random.choice(CITIES)}, ул. {random.choice(STREETS)}, д. {i%200+1}"
    if r==1: return f"ИНН {7707083893}"
    if r==2: return f"Иванов Иван Иванович №{i}"
    if r==3: return f"ООО «Ромашка-{i%50}»"
    if r==4: return f"Телефон 8-900-123-45-{i%100:02d}"
    if r==5: return f"Пункт {i%20}.{i%9} настоящего Договора определяет порядок"
    if r==6: return f"Сумма {i*13%99999} рублей 00 копеек по счёту"
    if r==7: return f"Приложение № {i%30} к Договору поставки от 12.03.2024"
    if r==8: return f"Товар в количестве {i%500} штук по номенклатуре"
    return f"Строка договора номер {i} без чувствительных данных вовсе"
N=6000
segs=[TextSegment(id=f"s{i}", text=mkseg(i), source_type="txt_line", metadata={}) for i in range(N)]
doc=SourceDocument(segments=segs, source_format="txt", source_path="<perf>")

t0=time.perf_counter(); reg=detect_regex(doc, CFG); t1=time.perf_counter()
ner=detect_ner(doc, CFG); t2=time.perf_counter()
merged=merge_compound_entities(doc, reg+ner); t3=time.perf_counter()
bnd=_detect_boundary_entities(doc, CFG); t4=time.perf_counter()
w(f"  сегментов: {N}")
w(f"  detect_regex:            {t1-t0:6.2f} c")
w(f"  detect_ner (основной):   {t2-t1:6.2f} c  ({(t2-t1)/N*1000:.2f} мс/сег)")
w(f"  merge_compound (этап B): {t3-t2:6.3f} c")
w(f"  B3 граничный (батч, C):  {t4-t3:6.2f} c  ({(t4-t3)/N*1000:.2f} мс/сег)")
w(f"  ---- детекция всего:     {(t2-t0)+(t4-t3)+(t3-t2):6.2f} c")
w(f"  сущностей: regex={len(reg)} ner={len(ner)} boundary={len(bnd)}")

with open(os.path.join(os.path.dirname(__file__), "cperf_out.txt"), "w", encoding="utf-8") as f:
    f.write(OUT.getvalue())
print("done")
