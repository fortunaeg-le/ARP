# -*- coding: utf-8 -*-
"""Что за класс «маска точна, а значение всё равно числится утёкшим»."""
import json, io, pathlib
from collections import Counter
ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).parent
d = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))
out = io.StringIO(); W = lambda *a: print(*a, file=out)
sel = []
for doc in d:
    for e in doc["entities"]:
        b = e.get("bnd") or {}
        if e.get("found") and b.get("direction") == "exact" and e.get("leaked"):
            sel.append((doc["doc_id"], e))
W("всего: %d" % len(sel))
W("по типам: %s" % Counter(e["type"] for _, e in sel).most_common())
for t in ("ADDRESS", "PER", "PASSPORT", "INN"):
    W("\n=== %s ===" % t)
    n = 0
    for did, e in sel:
        if e["type"] != t:
            continue
        n += 1
        if n > 12:
            break
        W("  %-16s %-45r pieces=%r lv2=%s" % (did, e["text"][:45], e["leak_pieces"][:3],
                                              (e.get("leak_v2") or {}).get("status")))
# сколько из них честны по v2
W("\nleak_v2-статус этих же: %s" % Counter((e.get("leak_v2") or {}).get("status") for _, e in sel))
(HERE / "peek3.txt").write_text(out.getvalue(), encoding="utf-8")
print("ok")
