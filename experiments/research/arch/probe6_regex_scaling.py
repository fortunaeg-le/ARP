# ARCH-PROBE, проба 6 (часть 5): цена regex-слоя на тип и экстраполяция 24 -> 50.
# Меряем: время каждого скомпилированного паттерна entity_types.yaml на
# конкатенации текстов 12 документов среза; итог — мс/тип/док и доля detect_regex
# в стенке детекции.
import json
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = ROOT / "entity_types.yaml"

from extractor import extract

subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))["doc_ids"][:12]
texts = []
for d in subset:
    p = C / "docs" / f"{d}.txt"
    if not p.exists():
        p = C / "docs" / f"{d}.docx"
    doc = extract(str(p))
    texts.append("\n".join(s.text for s in doc.segments))
blob = "\n\n".join(texts)
print(f"символов: {len(blob)}")

cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
types = cfg["entity_types"]
n_types = len(types)
n_masked = sum(1 for s in types.values() if isinstance(s, dict) and "token_prefix" in s)
print(f"типов в конфиге: {n_types}, с token_prefix (маскируемых): {n_masked}")

rows = []
total = 0.0
for tname, spec in types.items():
    if not isinstance(spec, dict):
        continue
    pats = spec.get("patterns") or ([spec["pattern"]] if "pattern" in spec else [])
    if not pats:
        continue
    t0 = time.perf_counter()
    n_hits = 0
    for ptn in pats:
        if isinstance(ptn, dict):
            sub = [ptn.get("pattern")] + [ptn.get(k) for k in ("anchor", "anti_anchor") if ptn.get(k)]
        else:
            sub = [ptn]
        for s in sub:
            rx = re.compile(s)
            for _ in range(3):
                n_hits = len(rx.findall(blob))
    dt = (time.perf_counter() - t0) / 3
    total += dt
    rows.append((dt, tname, len(pats), n_hits))

rows.sort(reverse=True)
print(f"\nвесь regex-слой: {total*1000:.0f} мс на {len(texts)} доков "
      f"({total*1000/len(texts):.1f} мс/док); типов с паттернами: {len(rows)}")
for dt, t, np_, nh in rows:
    print(f"  {dt*1000:7.1f} мс  {t} ({np_} паттернов, {nh} хитов)")
mean = total / max(len(rows), 1)
print(f"\nсреднее на тип: {mean*1000:.1f} мс/12 доков; "
      f"экстраполяция +26 типов: +{mean*26*1000/len(texts):.1f} мс/док")
