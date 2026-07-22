# -*- coding: utf-8 -*-
"""Фронт-2: замер на РЕПРЕЗЕНТАТИВНОЙ ПОДВЫБОРКЕ (не полный корпус).
Подвыборка = base×2/тип + граничные + ВСЕ документы с ADDRESS + мутационные
(homoglyph/linebreak/invisible/cell_split/combo) + разножанровые.

Пишет results в experiments/stage_c/<tag>.json и печатает:
  * per-type recall / leak_v2>=6 / FP-негатив;
  * ПОИМЕННЫЕ ложные ADDRESS (on-negative + cross-type) — цель этапа C;
  * ADDRESS exact-границы среди found;
  * round-trip fails;
  * ложные PER (для B-ADDR-HOMONYM).
Запуск: venv/Scripts/python.exe experiments/stage_c/measure_sub.py <tag>
"""
import os, sys, json, collections
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, CORPUS)
import measure_lib as ML  # noqa
import run_measurement as RM  # noqa
import subsample as SUB  # noqa

# Мутационные trick'и (перенос/омоглиф/невидимые/раздел ячейки/combo) + адрес + жанры.
TRICKS = ["homoglyph_cyrillic_in_digits", "linebreak_in_entity", "invisible_in_entity",
          "cell_split", "combo", "case_swap", "lowercase_all", "uppercase_all"]


def select(gold):
    ids = set(SUB.select_doc_ids(gold, tricks=TRICKS, types=["ADDRESS", "PER"]))
    return sorted(ids)


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "cur"
    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    ids = select(gold)
    subset = [by_id[i] for i in ids]
    print("Подвыборка: %d/%d документов" % (len(subset), len(gold)))
    results = RM.run_all(subset, verbose=False)
    out = os.path.join(HERE, "sub_%s.json" % tag)
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False)

    agg = ML.aggregate_results(results)
    print("\n%-10s %6s | %8s | %10s | %8s" % ("тип", "n", "recall", "leak6", "FP-нег"))
    for t in ML.ALL_ENTITY_TYPES:
        s = agg["per_type"][t]; n = s["n"]
        rec = 100.0*s["found"]/n if n else 0
        l6 = 100.0*s["leak_v2_6"]/n if n else 0
        fp = agg["fp_on_neg"].get(t, 0)
        print("%-10s %6d | %7.1f%% | %9.1f%% | %8d" % (t, n, rec, l6, fp))
    print("crashes: %d  fp_total: %d" % (len(agg["crashed"]), agg["fp_on_neg_total"]))

    # ADDRESS exact-границы среди found
    na = fa = ea = 0
    for r in results:
        if r.get("outcome") != "processed": continue
        for e in r.get("entities", []):
            if e["type"] != "ADDRESS": continue
            na += 1
            if e["found"]:
                fa += 1
                if e["exact"]: ea += 1
    print("\nADDRESS: n=%d found=%d exact(found)=%d (%.1f%%)" % (
        na, fa, ea, 100.0*ea/fa if fa else 0))

    # round-trip
    rtf = [r["doc_id"] for r in results if r.get("outcome") == "processed" and not r["roundtrip_ok"]]
    print("round-trip fails: %d %s" % (len(rtf), rtf[:10]))

    # поимённые ложные ADDRESS и PER
    for want in ("ADDRESS", "PER"):
        print("\nЛожные %s (fp on non-%s):" % (want, want))
        c = 0
        for r in results:
            if r.get("outcome") != "processed": continue
            for x in r.get("false_positives", []):
                if x["gtype"] == want:
                    c += 1
                    print("  %-30s %-45r neg=%s cross=%s" % (
                        r["doc_id"], x["text"][:43], x.get("on_negative"), x.get("cross_type")))
        print("  ИТОГО ложных %s: %d" % (want, c))


if __name__ == "__main__":
    main()
