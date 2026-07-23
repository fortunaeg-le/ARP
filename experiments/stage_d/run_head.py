# -*- coding: utf-8 -*-
"""Один полный прогон текущего HEAD (этап C+фикс) → results для baseline D."""
import os, sys, json, time
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.abspath(os.path.join(HERE,"..",".."))
sys.path.insert(0, os.path.join(ROOT,"src")); sys.path.insert(0, os.path.join(ROOT,"tests","corpus"))
import run_measurement as RM
gold=RM.load_gold(); t0=time.time()
res=RM.run_all(gold, verbose=False)
json.dump(res, open(os.path.join(HERE,"results_d_head.json"),"w",encoding="utf-8"), ensure_ascii=False)
print("DONE %d docs %.0fs"%(len(res),time.time()-t0))
