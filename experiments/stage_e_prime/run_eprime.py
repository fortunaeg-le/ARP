# -*- coding: utf-8 -*-
"""Полный прогон корпуса на коде этапа E′ (сборка рваных ADDRESS/ORG).
Пишет results_d_eprime.json. Гейт: gate_d.py <этот файл>."""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
import run_measurement as RM

OUT = os.path.join(HERE, "results_d_eprime.json")
gold = RM.load_gold()
t0 = time.time()
res = RM.run_all(gold, verbose=False)
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print("DONE %d docs %.0fs -> %s" % (len(res), time.time() - t0, OUT))
