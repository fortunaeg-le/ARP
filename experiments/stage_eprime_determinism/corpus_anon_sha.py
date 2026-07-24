# -*- coding: utf-8 -*-
"""Дамп per-doc sha256 анонимизированного текста по ВСЕМУ корпусу — инструмент
проверки, что правка ПОРЯДКА не изменила вывод ни на одном документе байт-в-байт.
Печатает один общий sha по всем (doc_id, anon_sha), плюс пишет детальный json."""
import os, sys, json, hashlib

ROOT = r"C:\Jesus\ARP"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
CFG = os.path.join(ROOT, "entity_types.yaml")

from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize

DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "corpus_anon_sha.json")

rows = {}
names = sorted(f for f in os.listdir(DOCS) if f.endswith(".docx"))
for i, fn in enumerate(names):
    doc = extract(os.path.join(DOCS, fn))
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    rows[fn] = hashlib.sha256(anon.encode("utf-8")).hexdigest()
    if i % 50 == 0:
        print("... %d/%d" % (i, len(names)))

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=0, sort_keys=True)

agg = hashlib.sha256(
    json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
).hexdigest()
print("docs:", len(rows))
print("AGGREGATE sha256:", agg)
print("written:", OUT)
