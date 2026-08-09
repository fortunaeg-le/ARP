# ARCH-PROBE: разведка форматов (ТОЛЬКО ЧТЕНИЕ).
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "tests" / "corpus"

g = json.load(open(C / "gold.json", encoding="utf-8"))
print("gold:", type(g).__name__, len(g))
print(json.dumps(g[0], ensure_ascii=False)[:700])

r = json.load(open(C / "results_baseline.json", encoding="utf-8"))
print("\nresults_baseline:", type(r).__name__)
if isinstance(r, dict):
    print(list(r.keys())[:15])
    for k in list(r.keys())[:2]:
        v = r[k]
        print(k, "->", type(v).__name__, (list(v.keys())[:10] if isinstance(v, dict) else str(v)[:200]))
