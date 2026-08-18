# -*- coding: utf-8 -*-
"""Для документа: эталонные PER, найденные PERSON/ORG-спаны, и КЛЮЧИ реестра
(что вообще заякорилось)."""
import glob, json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from extractor import extract
from pipeline import run_detection
import anchor_registry as AR

doc_id = sys.argv[1]
path = glob.glob(os.path.join(ROOT, "tests/corpus/docs", doc_id + ".*"))[0]
doc = extract(path)
gold = json.load(open(os.path.join(ROOT, "tests/corpus/gold.json"), encoding="utf-8"))
g = [x for x in gold if x["doc_id"] == doc_id][0]

# перехват реестра
keys = []
orig_add = AR.Registry.add
def spy(self, key, et, canon, ev, conf, **kw):
    keys.append((et, key, tuple(sorted(ev)), conf))
    return orig_add(self, key, et, canon, ev, conf, **kw)
AR.Registry.add = spy
ents = run_detection(doc, os.path.join(ROOT, "entity_types.yaml"))
AR.Registry.add = orig_add

print("### ЭТАЛОН PER (%d):" % sum(1 for e in g["entities"] if e["type"] == "PER"))
for e in g["entities"]:
    if e["type"] == "PER":
        print("   ", repr(e["text"]))
print("### ЯКОРЯ (реестр):")
for k in sorted(set(keys)):
    print("   ", k)
print("### СПАНЫ PERSON/ORG:")
for e in sorted(ents, key=lambda e: (e.segment_id, e.start)):
    if e.entity_type in ("PERSON", "ORG"):
        print("   %-7s %r" % (e.entity_type, e.original_text))
