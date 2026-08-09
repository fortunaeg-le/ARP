# R-RDR разведка: осмотр структуры дампа гейта. Пишет в файл (stdout консоли — cp1251).
import json, io, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
out = io.StringIO()
d = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))
r = d[0]
e = r["entities"][0]
print("ENTITY KEYS:", list(e.keys()), file=out)
for ent in r["entities"][:6]:
    print(" ", json.dumps(ent, ensure_ascii=False)[:600], file=out)
print(file=out)
print("MASK KEYS:", list(r["masks"][0].keys()), file=out)
for m in r["masks"][:4]:
    print(" ", json.dumps(m, ensure_ascii=False)[:400], file=out)
print(file=out)
print("FP KEYS:", list(r["false_positives"][0].keys()), file=out)
# собрать словари значений признаков
from collections import Counter
c_miss = Counter(); c_cat = Counter(); c_leakv2 = Counter(); c_bnd = Counter()
for doc in d:
    for ent in doc["entities"]:
        c_cat[ent.get("category")] += 1
        c_miss[ent.get("miss_reason")] += 1
        lv = ent.get("leak_v2")
        if isinstance(lv, dict):
            c_leakv2[lv.get("status")] += 1
        for k in ent:
            if k not in ("type","category","trick","checksum","text","found","exact","full",
                         "left_trim","right_trim","miss_reason","in_body","leaked","leak_pieces",
                         "leak_v1","leak_v2"):
                c_bnd[k] += 1
print("category:", c_cat, file=out)
print("miss_reason:", c_miss, file=out)
print("leak_v2.status:", c_leakv2, file=out)
print("прочие ключи сущности:", c_bnd, file=out)
lv = [ent["leak_v2"] for doc in d for ent in doc["entities"] if isinstance(ent.get("leak_v2"), dict)]
print("leak_v2 sample:", json.dumps(lv[:3], ensure_ascii=False)[:800], file=out)
(pathlib.Path(__file__).parent / "peek.txt").write_text(out.getvalue(), encoding="utf-8")
print("ok")
