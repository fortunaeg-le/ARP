# ARCH-PROBE, проба 7: (а) фактические списки приоритетов vs типы конфига;
# (б) объём работы арбитража на срезе (сущностей до/после, обрезки);
# (в) сквозной инвариант original_text == segment.text[start:end] после
#     детекции И после разрешения пересечений.
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = str(ROOT / "entity_types.yaml")

from extractor import extract
from pipeline import run_detection
import tokenizer
from tokenizer import _resolve_overlaps, assert_priority_contract

assert_priority_contract(CONFIG)
print("контракт приоритетов: ПРОШЁЛ")
print("_REGEX_PRIORITY:", tokenizer._REGEX_PRIORITY)
print("_NER_PRIORITY:", tokenizer._NER_PRIORITY)
cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
masked = [t for t, s in cfg["entity_types"].items() if isinstance(s, dict) and "token_prefix" in s]
inlists = set(tokenizer._REGEX_PRIORITY) | set(tokenizer._NER_PRIORITY)
print(f"маскируемых типов: {len(masked)}; в списках приоритетов: {len(inlists)}; "
      f"вне списков: {sorted(set(masked) - inlists)}")

subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))["doc_ids"][:12]
tot_in = tot_out = tot_trim = bad_inv = 0
seg_conflicts = 0
segs_total = 0
for d in subset:
    p = C / "docs" / f"{d}.txt"
    if not p.exists():
        p = C / "docs" / f"{d}.docx"
    doc = extract(str(p))
    seg_by_id = {s.id: s for s in doc.segments}
    ents = run_detection(doc, CONFIG)
    for e in ents:
        if e.original_text != seg_by_id[e.segment_id].text[e.start:e.end]:
            bad_inv += 1
            print("НАРУШЕНИЕ инварианта (детекция):", d, e.entity_type, repr(e.original_text[:40]))
    by_seg = defaultdict(list)
    for e in ents:
        by_seg[e.segment_id].append(e)
    for sid, es in by_seg.items():
        segs_total += 1
        res = _resolve_overlaps(list(es))
        tot_in += len(es)
        tot_out += len(res)
        keys_in = {(e.start, e.end, e.entity_type) for e in es}
        trimmed = [e for e in res if (e.start, e.end, e.entity_type) not in keys_in]
        tot_trim += len(trimmed)
        if len(res) != len(es) or trimmed:
            seg_conflicts += 1
        for e in res:
            if e.original_text != seg_by_id[sid].text[e.start:e.end]:
                bad_inv += 1
                print("НАРУШЕНИЕ инварианта (после арбитража):", d, e.entity_type,
                      repr(e.original_text[:40]))

print(f"\nсегментов с сущностями: {segs_total}; с конфликтами (что-то ушло/обрезано): {seg_conflicts}")
print(f"сущностей на входе арбитража: {tot_in}, на выходе: {tot_out}, обрезков создано: {tot_trim}")
print(f"нарушений инварианта original_text==срез: {bad_inv}")
