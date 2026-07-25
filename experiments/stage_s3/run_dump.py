# -*- coding: utf-8 -*-
"""Прогон полного корпуса в ПРОИЗВОЛЬНЫЙ файл-дамп (этап S3).

`run_measurement.main()` пишет в прибитый `results_stageb.json`, а `gate.py`
всегда перезаписывает отслеживаемый git'ом `results_gate_current.json`. Для
серии замеров этапа S3 нужен свой путь, не трогающий ни то, ни другое.

    venv/Scripts/python.exe experiments/stage_s3/run_dump.py <out.json>
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, CORPUS)

import run_measurement as RM  # noqa: E402


def main():
    out = os.path.abspath(sys.argv[1])
    gold = RM.load_gold()
    t0 = time.time()
    results = RM.run_all(
        gold, verbose=True,
        on_checkpoint=lambda res: json.dump(
            res, open(out, "w", encoding="utf-8"), ensure_ascii=False),
    )
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {len(results)} docs in {time.time()-t0:.0f}s -> {out}", flush=True)


if __name__ == "__main__":
    main()
