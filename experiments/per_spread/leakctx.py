# -*- coding: utf-8 -*-
"""Где именно фрагмент доживает до анонимного текста (максимум масок)."""
import glob, os, re, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
from extractor import extract
from pipeline import run_detection
from tokenizer import resolve_for_masking, apply_masking
import type_policy
import measure_lib as ML

CONFIG = os.path.join(ROOT, "entity_types.yaml")
doc_id = sys.argv[1]
path = glob.glob(os.path.join(ROOT, "tests/corpus/docs", doc_id + ".*"))[0]
doc = extract(path)
ents = run_detection(doc, CONFIG)
resolved = resolve_for_masking(doc, ents, CONFIG, suppression_log=[])
anon, kept = apply_masking(doc, resolved, CONFIG, type_policy.MAXIMUM)
if len(sys.argv) == 2:
    print(anon)
    sys.exit()
low = ML.v2_norm_text(anon)
for needle in sys.argv[2:]:
    n = ML.v2_norm_text(needle)
    print("### %r -> %r" % (needle, n))
    for m in re.finditer(re.escape(n), low):
        print("   ...%s..." % low[max(0, m.start()-110):m.end()+110].replace("\n", "\n"))
