# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 1 — ГЛАЗАМИ: правда ли новые случаи утечки.

Калибровка сказала: 41 вхождение, невидимое действующему прибору, становится
видно правилом «соседние куски». Число ничего не стоит, пока не показано, что
это ИМЕННО раздробленное значение, а не совпадение двух чужих чисел.

Скрипт печатает по каждому образцу: эталонное значение, найденные куски и
окрестность в анонимном тексте. Корпус синтетический (текст уже лежит в
репозитории), реальных ПДн здесь нет.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import type_policy  # noqa: E402
from calib_chain import runs_with_pos, best_chain_floor  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

WANT = ["agency_0005__m2718_linebreak", "labor_0011__m2718_linebreak",
        "services_0005__m2718_linebreak", "cession_0001__m2718_linebreak"]


def main():
    gold = {d["doc_id"]: d for d in RM.load_gold()}
    shown = 0
    for doc_id in WANT:
        d = gold.get(doc_id)
        if d is None:
            continue
        path = os.path.join(DOCS, doc_id + "." + d["format"])
        doc = extract(path)
        ents = run_detection(doc, CONFIG)
        anon, kept = tokenize(doc, ents, CONFIG, enabled_types=type_policy.MAXIMUM)
        field = ML.v2_digit_runs(anon)
        runs = runs_with_pos(anon)
        for e in d["entities"]:
            if e["type"] not in RM.V2_NUMERIC_TYPES:
                continue
            core = ML.v2_core_digits(e["text"])
            if not core:
                continue
            soft = min(6, len(core))
            old = ML.longest_surviving_window(core, field)
            tot, parts, gaps = best_chain_floor(core, runs, 3, 2)
            if not (tot >= soft and len(old) < soft):
                continue
            # где эта цепочка стоит в анонимном тексте
            pos = None
            for k in range(len(runs)):
                if runs[k][0] == parts[0]:
                    ok = True
                    for m, p in enumerate(parts):
                        if k + m >= len(runs) or runs[k + m][0] != p:
                            ok = False
                            break
                    if ok:
                        pos = (runs[k][1], runs[k + len(parts) - 1][2])
                        break
            print("=" * 70)
            print(f"{doc_id}  {e['type']}  etalon={e['text']!r}  core={core}")
            print(f"  staryi pribor: samoe dlinnoe okno {len(old)} < porog {soft} -> 'utechki net'")
            print(f"  kuski podryad: {parts} zazory {gaps} vsego {tot}")
            if pos:
                a, b = pos
                print("  okrestnost anon: " + repr(anon[max(0, a - 60):b + 60]))
            shown += 1
            if shown >= 8:
                return


if __name__ == "__main__":
    main()
