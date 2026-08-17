# -*- coding: utf-8 -*-
"""E'' repro: same-process N-run determinism check on the synthetic large fixture."""
import os, sys, hashlib
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
CFG = os.path.join(ROOT, "entity_types.yaml")

from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
DOC_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    ROOT, "tests", "fixtures", "synthetic_corporate_large.docx")


def sig_of_entities(ents):
    rows = []
    for e in ents:
        spans = tuple(tuple(s) for s in (e.spans or []))
        rows.append((e.entity_type, e.segment_id, e.start, e.end, spans, e.token))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return rows


results = []
for i in range(N):
    doc = extract(DOC_PATH)
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    sig = sig_of_entities(kept)
    h = hashlib.sha256(repr(sig).encode("utf-8")).hexdigest()
    anon_h = hashlib.sha256(anon.encode("utf-8")).hexdigest()
    results.append((len(kept), h, anon_h, sig))
    print("run %2d: masks=%d sig_sha=%s anon_sha=%s" % (i, len(kept), h[:16], anon_h[:16]))

counts = [r[0] for r in results]
sigs = [r[1] for r in results]
print("\ncounts:", counts)
print("unique sig hashes:", len(set(sigs)))
if len(set(sigs)) == 1:
    print("DETERMINISTIC across %d in-process runs" % N)
else:
    print("NONDETERMINISTIC")
    if N > 2:
        rest_same = len(set(sigs[1:])) == 1
        print("first_differs_from_rest_which_agree:", sigs[0] != sigs[1] and rest_same)

# сохранить сигнатуры для диффа (только типы/позиции/токены, без текста)
import json
out = os.path.join(os.path.dirname(__file__), "run_signatures.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump([r[3] for r in results], f, ensure_ascii=False)
print("signatures dumped:", out)
