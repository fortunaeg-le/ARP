# ARCH-PROBE, проба 8: встречается ли на срезе конфигурация цикла _winner
# (ADDRESS x голый PERSON x третий ner-спан, все попарно пересекаются) и
# вообще пересечения ADDRESS с голым PERSON.
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = str(ROOT / "entity_types.yaml")

from extractor import extract
from pipeline import run_detection
from tokenizer import _overlaps

subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))["doc_ids"]

pairs = 0
triples = 0
for d in subset:
    p = C / "docs" / f"{d}.txt"
    if not p.exists():
        p = C / "docs" / f"{d}.docx"
    doc = extract(str(p))
    ents = run_detection(doc, CONFIG)
    by_seg = defaultdict(list)
    for e in ents:
        if e.detector == "ner":
            by_seg[e.segment_id].append(e)
    for es in by_seg.values():
        for a, b in combinations(es, 2):
            if not _overlaps(a, b):
                continue
            ab = {a.entity_type, b.entity_type}
            bare = any(e.entity_type == "PERSON" and " " not in e.original_text
                       and "." not in e.original_text for e in (a, b))
            if "ADDRESS" in ab and bare:
                pairs += 1
        for a, b, c in combinations(es, 3):
            if _overlaps(a, b) and _overlaps(b, c) and _overlaps(a, c):
                triples += 1

print(f"докуменов просмотрено: {len(subset)} (весь срез)")
print(f"пересечений ADDRESS x голый PERSON: {pairs}")
print(f"тройных взаимных ner-пересечений: {triples}")
