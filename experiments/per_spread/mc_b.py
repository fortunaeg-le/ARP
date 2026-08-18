# -*- coding: utf-8 -*-
"""Линия «г» гейта в АБСОЛЮТНЫХ числах: B — доля масок, чья эталонная сущность
покрыта объединением масок ЦЕЛИКОМ. Доля может упасть при РОСТЕ числителя,
если знаменатель (масок, легших на эталон) вырос сильнее — это и надо показать."""
import collections
import json
import sys

before, after = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])


def agg(dump):
    ok = collections.Counter()
    n = collections.Counter()
    bad = collections.defaultdict(list)
    for r in dump:
        for m in r["masks"]:
            if not m.get("scored"):
                continue
            t = m.get("gold_type")
            n[t] += 1
            if m.get("b_ok"):
                ok[t] += 1
            else:
                bad[t].append((r["doc_id"], m["gtype"], m["text"][:50]))
    return ok, n, bad


ob, nb, bb = agg(before)
oa, na, ba = agg(after)
print("%-12s %-26s %-26s" % ("тип", "ДО  b_ok/scored (доля)", "ПОСЛЕ b_ok/scored (доля)"))
for t in sorted(set(nb) | set(na), key=lambda t: -(na[t] or 0)):
    if t is None:
        continue
    pb = 100.0 * ob[t] / nb[t] if nb[t] else 0
    pa = 100.0 * oa[t] / na[t] if na[t] else 0
    flag = ""
    if pa < pb:
        flag = "   <-- доля упала; числитель %+d, знаменатель %+d" % (oa[t] - ob[t], na[t] - nb[t])
    print("%-12s %6d/%-6d %6.2f%%     %6d/%-6d %6.2f%%%s"
          % (t, ob[t], nb[t], pb, oa[t], na[t], pa, flag))
tb, tn = sum(ob.values()), sum(nb.values())
ta, tn2 = sum(oa.values()), sum(na.values())
print("%-12s %6d/%-6d %6.2f%%     %6d/%-6d %6.2f%%"
      % ("ВСЕГО", tb, tn, 100.0 * tb / tn, ta, tn2, 100.0 * ta / tn2))

print("\nНОВЫЕ маски с b_ok=False (сверх прежних), по типу эталона:")
for t in sorted(set(bb) | set(ba)):
    d = len(ba[t]) - len(bb[t])
    if d:
        print("  %-10s %+d  (было %d, стало %d)" % (t, d, len(bb[t]), len(ba[t])))
        sb = collections.Counter(bb[t])
        sa = collections.Counter(ba[t])
        shown = 0
        for k in sorted(set(sa) | set(sb)):
            if sa[k] > sb[k] and shown < 12:
                print("      +%d %s %s %r" % (sa[k] - sb[k], k[0], k[1], k[2]))
                shown += 1
