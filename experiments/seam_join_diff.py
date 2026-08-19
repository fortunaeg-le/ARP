# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN: поимённое сличение двух дампов замера.

    python experiments/seam_join_diff.py before.json after.json

Сравнивает по ключу (doc_id, тип, порядковый номер вхождения): found / exact /
leak_v2.status, печатает свод переходов и каждую сдвинувшуюся сущность.
Отдельно — маски, over-mask прозы (маска не легла ни на что) и ложные на
объявленном негативе: те же три величины, что судит гейт.
"""
import collections
import json
import sys


def key_rows(path):
    rows = {}
    masks = collections.Counter()
    over = collections.Counter()
    fp = collections.Counter()
    for r in json.load(open(path, encoding="utf-8")):
        cnt = collections.Counter()
        for e in r["entities"]:
            t = e["type"]
            cnt[t] += 1
            rows[(r["doc_id"], t, cnt[t])] = e
        for m in r["masks"]:
            masks[m["gtype"] or m["raw_type"]] += 1
        for f in r["false_positives"]:
            t = f["gtype"] or f["raw_type"]
            if f.get("on_negative") is not None:
                fp[t] += 1
            elif f.get("cross_type") is None:
                over[t] += 1
    return rows, masks, over, fp


def st(e):
    return (e.get("leak_v2") or {}).get("status", "none")


def table(name, ca, cb):
    print("\n== %s ==" % name)
    for t in sorted(set(ca) | set(cb)):
        if ca[t] != cb[t]:
            print(f"  {t:18s} {ca[t]:5d} -> {cb[t]:5d} ({cb[t]-ca[t]:+d})")
    print(f"  {'ВСЕГО':18s} {sum(ca.values()):5d} -> {sum(cb.values()):5d} "
          f"({sum(cb.values())-sum(ca.values()):+d})")


def main():
    a, ma, oa, fa = key_rows(sys.argv[1])
    b, mb, ob, fb = key_rows(sys.argv[2])
    moves = collections.Counter()
    lines = []
    for k in sorted(set(a) | set(b)):
        ea, eb = a.get(k), b.get(k)
        if ea is None or eb is None:
            moves[("только в одном дампе", k[1])] += 1
            continue
        if st(ea) != st(eb):
            moves[(f"утечка {st(ea)}->{st(eb)}", k[1])] += 1
            lines.append(f"  утечка {st(ea):8s}->{st(eb):8s} {k[0]:34s} "
                         f"{k[1]:11s} {ea['text']!r}")
        if bool(ea.get("found")) != bool(eb.get("found")):
            moves[(f"найдено {ea.get('found')}->{eb.get('found')}", k[1])] += 1
            lines.append(f"  найдено {str(ea.get('found')):5s}->{str(eb.get('found')):5s} "
                         f"{k[0]:34s} {k[1]:11s} {ea['text']!r}")
        if bool(ea.get("exact")) != bool(eb.get("exact")):
            moves[(f"точный спан {ea.get('exact')}->{eb.get('exact')}", k[1])] += 1
            lines.append(f"  точный  {str(ea.get('exact')):5s}->{str(eb.get('exact')):5s} "
                         f"{k[0]:34s} {k[1]:11s} {ea['text']!r}")
    print("== переходы ==")
    for (what, ty), n in sorted(moves.items()):
        print(f"  {what:28s} {ty:12s} {n}")
    table("маски по типам", ma, mb)
    table("over-mask прозы (маска не легла ни на что)", oa, ob)
    table("ложные на объявленном негативе", fa, fb)
    print("\n== поимённо ==")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
