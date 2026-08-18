# -*- coding: utf-8 -*-
"""Прогон конвейера на ОДНОМ документе корпуса: печатает найденные PER/ORG-спаны
и эталонные PER, чтобы видеть промах поимённо."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
from extractor import extract
from pipeline import run_detection

doc_id = sys.argv[1]
gold = json.load(open(os.path.join(ROOT, "tests/corpus/gold.json"), encoding="utf-8"))
g = [x for x in gold if x["doc_id"] == doc_id][0]
import glob
c = glob.glob(os.path.join(ROOT, "tests/corpus/docs", doc_id + ".*"))
path = c[0]
doc = extract(path)
ents = run_detection(doc, os.path.join(ROOT, "entity_types.yaml"))
by_seg = {s.id: s for s in doc.segments}
print("### ЭТАЛОН PER:")
for e in g["entities"]:
    if e["type"] == "PER":
        print("   ", repr(e["text"]))
print("### НАЙДЕНО (PERSON/ORG):")
for e in sorted(ents, key=lambda e: (e.segment_id, e.start)):
    if e.entity_type in ("PERSON", "ORG"):
        print("   %-8s %-30r seg=%s [%d:%d] det=%s gk=%s"
              % (e.entity_type, e.original_text, e.segment_id, e.start, e.end,
                 e.detector, getattr(e, "group_key", None)))
print("### СЕГМЕНТЫ, где эталонный PER есть в тексте, но спана нет:")
targets = sys.argv[2:] 
for s in doc.segments:
    for t in targets:
        if t in (s.text or ""):
            print("   seg=%s: %r" % (s.id, s.text[:300]))
