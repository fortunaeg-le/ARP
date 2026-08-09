# R-RDR: словарь признаков остатка — trick, format, source, bnd.
import json, io, pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[3]
out = io.StringIO()
d = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))

trick = Counter(); trick_bad = Counter(); fmt = Counter(); src = Counter()
bnd_dir = Counter(); types = Counter()
for doc in d:
    fmt[doc["format"]] += 1
    src[doc["source"]] += 1
    for e in doc["entities"]:
        t = e.get("trick")
        trick[t] += 1
        types[e["type"]] += 1
        b = e.get("bnd") or {}
        bnd_dir[b.get("direction")] += 1
        bad = (not e["found"]) or (not e["exact"]) or e["leaked"] or (b.get("direction") not in (None, "exact"))
        if bad:
            trick_bad[t] += 1
print("format(док):", fmt, file=out)
print("source(док):", src, file=out)
print("тип сущности:", types.most_common(), file=out)
print("bnd.direction:", bnd_dir, file=out)
print(file=out)
print("trick — всего / из них с ошибкой:", file=out)
for k, v in trick.most_common():
    print(f"  {str(k):40s} {v:6d}  bad={trick_bad[k]:6d}", file=out)
(pathlib.Path(__file__).parent / "peek2.txt").write_text(out.getvalue(), encoding="utf-8")
print("ok")
