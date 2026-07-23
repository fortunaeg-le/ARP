# -*- coding: utf-8 -*-
"""Полный прогон корпуса на коде этапа C′ (ужесточение якоря кавычек/формы).
Пишет results_d_cprime.json — НЕ трогает baseline_d.json / results_d_head.json.
Гейт: venv/Scripts/python.exe experiments/stage_d/gate_d.py <этот файл>."""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
import run_measurement as RM

OUT = os.path.join(HERE, "results_d_cprime.json")
gold = RM.load_gold()
t0 = time.time()
res = RM.run_all(gold, verbose=False)
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print("DONE %d docs %.0fs -> %s" % (len(res), time.time() - t0, OUT))
