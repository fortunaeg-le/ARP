# -*- coding: utf-8 -*-
"""causal_report.py — сводка причинной пробы (`causal.py`) в людях, не вхождениях."""
import io
import json
import os
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
import by_entity as BE                          # noqa: E402

VARIANTS = ["base", "homo", "case", "lb", "all"]


def people(rows_by_variant, doc_id, variant):
    """{ключ человека: защищён ли} для одного документа и варианта."""
    rows = rows_by_variant.get(variant)
    if not isinstance(rows, list):
        return None
    g = collections.defaultdict(lambda: True)
    for r in rows:
        k = BE.person_key(r["text"])
        if (not r["found"]) or r["leak"]:
            g[k] = False
        else:
            g[k] = g[k]
    return dict(g)


def main():
    data = json.load(open(os.path.join(HERE, "causal.json"), encoding="utf-8"))
    ok = [d for d in data if "fatal" not in d
          and all(isinstance(d["types"].get(v), list) for v in VARIANTS)]
    out = io.open(os.path.join(HERE, "causal_report.txt"), "w", encoding="utf-8")
    w = out.write
    w("документов в пробе: %d (из %d; остальные — ошибка прогона)\n"
      % (len(ok), len(data)))
    bad_docs = [d["doc_id"] for d in data if d not in ok]
    w("исключены: %d\n\n" % len(bad_docs))

    per_variant = {}
    for v in VARIANTS:
        tot = prot = 0
        occ_found = occ_tot = occ_leak = 0
        for d in ok:
            pp = people(d["types"], d["doc_id"], v)
            tot += len(pp)
            prot += sum(1 for x in pp.values() if x)
            for r in d["types"][v]:
                occ_tot += 1
                occ_found += int(r["found"])
                occ_leak += int(r["leak"])
        per_variant[v] = (tot, prot, occ_tot, occ_found, occ_leak)

    w("=== ЗАЩИТА ЛЮДЕЙ ПРИ ИДЕАЛЬНОЙ ПОЧИНКЕ МЕХАНИЗМА ===\n")
    w("(case и lb — починка ПО ЭТАЛОНУ, то есть ВЕРХНЯЯ ГРАНИЦА механизма;\n"
      " homo — общее правило вслепую по всему тексту, границей не является)\n\n")
    w("%-6s %8s %8s %9s %10s %9s\n"
      % ("вар.", "людей", "защищ.", "защита", "вхожд.найд", "утечка"))
    b = per_variant["base"]
    for v in VARIANTS:
        tot, prot, ot, of, ol = per_variant[v]
        w("%-6s %8d %8d %8.2f%% %9.2f%% %8.2f%%\n"
          % (v, tot, prot, 100.0 * prot / tot, 100.0 * of / ot, 100.0 * ol / ot))
    w("\nдельта к base, процентных пунктов защиты людей:\n")
    for v in VARIANTS[1:]:
        tot, prot, _, _, _ = per_variant[v]
        w("  %-5s %+6.2f пп  (%+d человек)\n"
          % (v, 100.0 * prot / tot - 100.0 * b[1] / b[0], prot - b[1]))

    # кто именно вылечился
    w("\n=== ПЕРЕСЕЧЕНИЕ: кого лечит какой вариант ===\n")
    cure = {v: set() for v in VARIANTS[1:]}
    base_bad = set()
    for d in ok:
        pb = people(d["types"], d["doc_id"], "base")
        for k, okk in pb.items():
            if not okk:
                base_bad.add((d["doc_id"], k))
        for v in VARIANTS[1:]:
            pv = people(d["types"], d["doc_id"], v)
            for k, okk in pv.items():
                if okk and not pb.get(k, True):
                    cure[v].add((d["doc_id"], k))
    w("незащищённых в base: %d\n" % len(base_bad))
    for v in VARIANTS[1:]:
        w("  %-5s лечит %d\n" % (v, len(cure[v])))
    w("  homo+case+lb по отдельности, объединение: %d\n"
      % len(cure["homo"] | cure["case"] | cure["lb"]))
    w("  вариант all лечит: %d — разница с объединением показывает, "
      "сколько людей требуют ДВУХ починок сразу\n"
      % len(cure["all"]))
    w("  НЕ лечится ничем из трёх: %d\n" % len(base_bad - cure["all"]))
    out.close()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
