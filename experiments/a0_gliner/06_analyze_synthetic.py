"""A0 bake-off, шаг 6 — синтетика: пропуски GLiNER относительно Natasha.

Для каждого порога сетки и каждой модели: какую долю сущностей Natasha (ORG/PERSON/
ADDRESS) GLiNER покрывает, и ПОИМЁННЫЙ список пропусков. «Покрыта» = в том же
сегменте есть спан GLiNER, чей диапазон пересекается ЛИБО текст вложен (устойчиво к
рассинхрону координат на гомоглиф-документах). Отдельно: покрытие тем же ТИПОМ.
"""
import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
GRID = [0.1, 0.3, 0.5, 0.7, 0.9]
TYPES = ["ORG", "PERSON", "ADDRESS"]


def load(p):
    return json.load(open(os.path.join(HERE, p), encoding="utf-8"))


def norm(s):
    return " ".join(s.lower().split()).strip(" .,«»\"'()")


def covered(nat, g_spans):
    """nat покрыта спаном GLiNER в том же сегменте: пересечение диапазонов ИЛИ
    вложение текста (любая сторона), длиной >=3 симв."""
    nt = norm(nat["text"])
    for g in g_spans:
        if g["seg_id"] != nat["seg_id"]:
            continue
        if max(nat["start"], g["start"]) < min(nat["end"], g["end"]):
            return g["type"]
        gt = norm(g["text"])
        if len(nt) >= 3 and (nt in gt or gt in nt):
            return g["type"]
    return None


def main():
    nat = load("dumps/natasha_synthetic.json")
    nat_by_doc = nat["by_doc"]
    nat_all = [{**e, "doc": d} for d, ents in nat_by_doc.items() for e in ents]
    print(f"Natasha (синтетика): {len(nat_all)} сущн., латентность NER {nat['latency_ner_total_s']}c")
    from collections import Counter
    print("  по типам:", dict(Counter(e["type"] for e in nat_all)))

    summary = {"natasha_latency_s": nat["latency_ner_total_s"],
               "natasha_by_type": dict(Counter(e["type"] for e in nat_all)),
               "models": {}}

    for rf in sorted(f for f in os.listdir(os.path.join(HERE, "dumps"))
                     if f.startswith("raw_synth_") and f.endswith(".json")):
        raw = load(f"dumps/{rf}")
        model = raw["model"]
        print(f"\n{'='*74}\n{model}  (полная подвыборка {raw['latency_total_s']}c, "
              f"{raw['n_spans_raw']} raw-спанов)")
        print("  th | покрытие Natasha (любой тип) | тем же типом | пропущено ПОИМЁННО (distinct)")
        summary["models"][model] = {"latency_total_s": raw["latency_total_s"], "grid": {}}
        for th in GRID:
            g_by_doc = {d: [s for s in sp if s["score"] >= th] for d, sp in raw["by_doc"].items()}
            per_type = {t: {"n": 0, "any": 0, "same": 0, "missed": []} for t in TYPES}
            for e in nat_all:
                t = e["type"]
                if t not in per_type:
                    continue
                per_type[t]["n"] += 1
                got = covered(e, g_by_doc.get(e["doc"], []))
                if got is not None:
                    per_type[t]["any"] += 1
                    if got == t:
                        per_type[t]["same"] += 1
                    else:
                        per_type[t]["missed"].append(f"{norm(e['text'])} [→{got}]")
                else:
                    per_type[t]["missed"].append(norm(e["text"]))
            # агрегаты
            tot_n = sum(per_type[t]["n"] for t in TYPES)
            tot_any = sum(per_type[t]["any"] for t in TYPES)
            tot_same = sum(per_type[t]["same"] for t in TYPES)
            miss_any_pct = 100.0 * (tot_n - tot_any) / tot_n if tot_n else 0.0
            summary["models"][model]["grid"][str(th)] = {
                "total_natasha": tot_n, "covered_any": tot_any, "covered_same_type": tot_same,
                "miss_any_pct": round(miss_any_pct, 1),
                "per_type": {t: {"n": per_type[t]["n"], "any": per_type[t]["any"],
                                 "same": per_type[t]["same"],
                                 "missed_distinct": sorted(set(per_type[t]["missed"]))}
                             for t in TYPES},
            }
            # печать компактно
            missed_names = sorted(set(m for t in TYPES for m in per_type[t]["missed"]))
            print(f"  {th:.1f}| {tot_any}/{tot_n} ({100-miss_any_pct:.0f}%)"
                  f"            | {tot_same}/{tot_n} ({100*tot_same/tot_n:.0f}%)   | "
                  f"миссов {len(missed_names)}")

    json.dump(summary, open(os.path.join(HERE, "tables", "results_synthetic.json"),
                            "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
