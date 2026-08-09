# -*- coding: utf-8 -*-
"""Разложить живые случаи среза и распечатать самый крупный класс."""
import json, io, pathlib, sys
from collections import Counter, defaultdict
HERE = pathlib.Path(__file__).parent
cases = json.load(open(HERE / "cases_subset.json", encoding="utf-8"))
out = io.StringIO(); W = lambda *a: print(*a, file=out)

def mut(c):
    return c["doc_id"].split("__")[1].split("_", 1)[1] if "__" in c["doc_id"] else "чистый"

bad = [c for c in cases if c["kind"] != "ok"]
W("срез: %d эталонных, верно %d, остаток %d" % (len(cases), len(cases) - len(bad), len(bad)))
W("классы: %s" % Counter(c["kind"] for c in bad).most_common())
W("класс × мутация:")
g = defaultdict(Counter)
for c in bad:
    g[c["kind"]][mut(c)] += 1
for k, v in sorted(g.items(), key=lambda x: -sum(x[1].values())):
    W("  %-12s %4d | %s" % (k, sum(v.values()), ", ".join("%s %d" % x for x in v.most_common())))
W("класс × тип:")
g2 = defaultdict(Counter)
for c in bad:
    g2[c["kind"]][c["type"]] += 1
for k, v in sorted(g2.items(), key=lambda x: -sum(x[1].values())):
    W("  %-12s %4d | %s" % (k, sum(v.values()), ", ".join("%s %d" % x for x in v.most_common())))

target = sys.argv[1] if len(sys.argv) > 1 else "не_найдено"
W("\n===== ВСЕ СЛУЧАИ КЛАССА «%s» =====" % target)
for i, c in enumerate(x for x in bad if x["kind"] == target):
    W("[%03d] %-28s %-9s %s" % (i, c["doc_id"], c["type"], mut(c)))
    W("      эталон : %r" % c["gold_text"])
    W("      маска  : %r  (недобор %d, перебор %d)" % (c["mask_text"], c["under"], c["over"]))
    W("      слева  : %r" % c["left"][-60:])
    W("      справа : %r" % c["right"][:60])
(HERE / ("cases_%s.txt" % target)).write_text(out.getvalue(), encoding="utf-8")
print("ok")
