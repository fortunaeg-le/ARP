# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN: прогон харнесса замера по выбранным документам с дампом.

    python experiments/seam_join_run.py out.json [--layout|--all|--subset]

--layout (умолчание) — 33 документа мутаций linebreak/cell_split;
--subset — зафиксированный срез subset_iter.json; --all — весь корпус.
Дамп той же формы, что results_gate_current.json (сравним поимённо).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))


def main():
    import run_measurement as RM
    import subset_lib as SL

    out = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "--layout"
    gold = RM.load_gold()
    if mode == "--all":
        subset = gold
    elif mode == "--subset":
        ids = set(SL.load_subset_ids())
        subset = [d for d in gold if d["doc_id"] in ids]
    else:
        subset = [d for d in gold
                  if "linebreak" in d["doc_id"] or "cell_split" in d["doc_id"]]
    print(f"документов: {len(subset)}")
    results = RM.run_all(subset, verbose=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print("записано:", out)


if __name__ == "__main__":
    main()
