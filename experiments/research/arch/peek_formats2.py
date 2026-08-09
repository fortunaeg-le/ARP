import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "tests" / "corpus"

r = json.load(open(C / "results_baseline.json", encoding="utf-8"))
print("len:", len(r))
print(json.dumps(r[0], ensure_ascii=False)[:1500])
