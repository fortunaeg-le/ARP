# -*- coding: utf-8 -*-
"""Тот же probe2, но на src из HEAD (до правки) — чтобы отличить «маска появилась
из-за правки» от «маска была и раньше»."""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
tmp = tempfile.mkdtemp()
srcdir = os.path.join(tmp, "src")
shutil.copytree(os.path.join(ROOT, "src"), srcdir, ignore=shutil.ignore_patterns("__pycache__"))
blob = subprocess.check_output(["git", "show", "HEAD:src/anchor_registry.py"], cwd=ROOT)
open(os.path.join(srcdir, "anchor_registry.py"), "wb").write(blob)
sys.path.insert(0, srcdir)

from extractor import extract          # noqa: E402
from pipeline import run_detection     # noqa: E402

doc_id = sys.argv[1]
path = glob.glob(os.path.join(ROOT, "tests/corpus/docs", doc_id + ".*"))[0]
doc = extract(path)
ents = run_detection(doc, os.path.join(ROOT, "entity_types.yaml"))
print("### СПАНЫ PERSON/ORG (src из HEAD):")
for e in sorted(ents, key=lambda e: (e.segment_id, e.start)):
    if e.entity_type in ("PERSON", "ORG"):
        print("   %-7s %r" % (e.entity_type, e.original_text))
