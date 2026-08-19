# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN, разведка: разбор класса вёрстки по дампу полного гейта.

Считает по results_gate_current.json: вхождения / утечки >=6 / не найдено
в разрезе приёма порчи и типа, и выписывает поимённо утекающие значения
класса linebreak / cell_split — с исходным окном из документа корпуса.
Корпус v1 синтетический (запрет 7 не затрагивается).
"""
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")


def leaks(e):
    return (e.get("leak_v2") or {}).get("status", "none") != "none"


def main():
    dump = sys.argv[1] if len(sys.argv) > 1 else DUMP
    docs = json.load(open(dump, encoding="utf-8"))
    tot = collections.Counter()
    lk = collections.Counter()
    nf = collections.Counter()
    bytype = collections.defaultdict(collections.Counter)
    rows = []
    for r in docs:
        for e in r["entities"]:
            t = e.get("trick") or "none"
            tot[t] += 1
            if leaks(e):
                lk[t] += 1
            if not e.get("found"):
                nf[t] += 1
            if t in ("mut:linebreak", "mut:cell_split"):
                bt = bytype[t][e["type"]]
                bytype[t][e["type"]] = bt + 1
                if leaks(e):
                    bytype[t + ":leak"][e["type"]] += 1
                    bytype[t + ":nf"][e["type"]] += 0 if e.get("found") else 1
                    rows.append((r["doc_id"], t, e["type"], e.get("found"),
                                 e.get("exact"), e.get("miss_reason"),
                                 e["text"], e.get("bnd"),
                                 (e.get("leak_v2") or {}).get("status")))
    out = []
    out.append("== по приёму порчи ==")
    for k, v in tot.most_common():
        out.append(f"{k:26s} {v:6d}  утечка {lk[k]:5d} ({100*lk[k]/v:5.1f}%)  ненайдено {nf[k]:5d}")
    for t in ("mut:linebreak", "mut:cell_split"):
        out.append(f"\n== {t} по типам ==")
        for ty, n in bytype[t].most_common():
            l = bytype[t + ":leak"][ty]
            out.append(f"  {ty:18s} {n:5d}  утечка {l:4d} ({100*l/n:5.1f}%)  ненайдено {bytype[t+':nf'][ty]:4d}")
    out.append("\n== поимённо (утекающие) ==")
    for doc, t, ty, found, exact, mr, text, bnd, st in sorted(rows):
        out.append(f"{doc:34s} {t:15s} {ty:16s} found={found} exact={exact} "
                   f"miss={mr} leak={st} bnd={bnd} :: {text!r}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
