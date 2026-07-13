# -*- coding: utf-8 -*-
"""Независимый пересчёт метрик golden — не через test_golden_addresses, а свой код."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from collections import Counter
from models import SourceDocument, TextSegment
from ner_detector import detect_ner
from golden_addresses import address_items, nonaddress_items

CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")

def addr_spans(text):
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<g>")
    return [(e.start, e.end, e.original_text) for e in detect_ner(doc, CFG) if e.entity_type == "ADDRESS"]

def covers(spans, gs, ge):
    cov = [False]*(ge-gs)
    for s,e,_ in spans:
        for i in range(max(s,gs), min(e,ge)):
            cov[i-gs]=True
    return all(cov)

def overlaps(spans, gs, ge):
    return any(max(s,gs)<min(e,ge) for s,e,_ in spans)

det_by_cat=Counter(); full_by_cat=Counter(); n_by_cat=Counter()
det=full=0
over_capture=[]  # excessive capture cases
for cat,text,span in address_items():
    gs=text.index(span); ge=gs+len(span)
    spans=addr_spans(text)
    d=overlaps(spans,gs,ge); f=covers(spans,gs,ge)
    n_by_cat[cat]+=1; det_by_cat[cat]+=d; full_by_cat[cat]+=f
    det+=d; full+=f
    if not f:
        print(f"[NOT CLOSED] {cat}: {text!r}")
        print(f"    эталон={span!r} закрыто={[s[2] for s in spans]}")
    # measure over-capture: total captured chars beyond golden span
    for s,e,t in spans:
        extra_left = max(0, gs-s)
        extra_right = max(0, e-ge)
        if extra_left+extra_right > 3:  # more than trivial
            over_capture.append((cat,text,span,t,extra_left,extra_right))

print("\n=== ADDRESS DETECTION / FULL-CLOSURE (мой пересчёт) ===")
for cat in ["marker","nomarker","cell","abbr","legal"]:
    print(f"  {cat:9s} N={n_by_cat[cat]:2d}  detect={det_by_cat[cat]:2d}  full={full_by_cat[cat]:2d}")
print(f"  {'ВСЕГО':9s} N={sum(n_by_cat.values()):2d}  detect={det}  full={full}")

fp=0
print("\n=== FALSE POSITIVES on non-addr ===")
for cat,text,span in nonaddress_items():
    spans=addr_spans(text)
    if spans:
        fp+=1
        print(f"[FP] {text!r} -> {[s[2] for s in spans]}")
print(f"  ложных: {fp}/{len(nonaddress_items())}")

print("\n=== OVER-CAPTURE (захват >3 символов за эталон) ===")
for cat,text,span,t,el,er in over_capture:
    print(f"  {cat}: эталон={span!r}\n      захвачено={t!r} (лишку слева={el}, справа={er})")
if not over_capture:
    print("  нет случаев захвата >3 символов")
