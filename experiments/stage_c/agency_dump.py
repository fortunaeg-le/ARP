# -*- coding: utf-8 -*-
"""Точечный дамп agency_0009: показывает ADDRESS-маски и анон-строку l18 (безмаркерный
адрес «Москва Ленина 47/3 кв 67»). Round-trip проверяется тут же."""
import os, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.abspath(os.path.join(HERE,"..",".."))
sys.path.insert(0, os.path.join(ROOT,"src")); sys.path.insert(0, os.path.join(ROOT,"tests","corpus"))
from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize, build_plain_text
from session_store import save_session
from detokenizer import detokenize
import tempfile
CFG=os.path.join(ROOT,"entity_types.yaml")
doc=extract(os.path.join(ROOT,"tests","corpus","docs","agency_0009.txt"))
ents=run_detection(doc,CFG)
anon,kept=tokenize(doc,ents,CFG)
print("ADDRESS-маски:")
for e in ents:
    if e.entity_type=="ADDRESS": print("   [%s] %r"%(e.segment_id, e.original_text))
for line in anon.split("\n"):
    if "исполнения обязательств" in line: print("l18 анон:", repr(line))
# round-trip
st=tempfile.mkdtemp()
sid=save_session(kept, session_id=None, ttl_hours=24, storage_dir=st)
restored,unres=detokenize(anon, sid, storage_dir=st)
print("round-trip OK:", restored==build_plain_text(doc), "| unresolved:", unres)
