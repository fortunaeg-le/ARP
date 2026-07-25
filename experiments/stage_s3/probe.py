# -*- coding: utf-8 -*-
"""Отладочный зонд этапа S3: детекция по одному документу корпуса, печать масок
выбранного типа с контекстом. Ничего не пишет, только читает.

    venv/Scripts/python.exe experiments/stage_s3/probe.py <doc_id> [TYPE]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract          # noqa: E402
from pipeline import run_detection     # noqa: E402
from tokenizer import tokenize         # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")


def probe(doc_id, want="ADDRESS"):
    path = None
    for ext in (".txt", ".docx"):
        p = os.path.join(DOCS, doc_id + ext)
        if os.path.isfile(p):
            path = p
            break
    if path is None:
        raise SystemExit(f"нет документа {doc_id}")
    doc = extract(path)
    ents = run_detection(doc, CONFIG)
    anon, kept = tokenize(doc, ents, CONFIG)
    segs = {s.id: s.text for s in doc.segments}
    out = []
    for e in kept:
        if want != "*" and e.entity_type != want:
            continue
        t = segs[e.segment_id]
        out.append({
            "seg": e.segment_id, "type": e.entity_type,
            "span": (e.start, e.end), "text": e.original_text,
            "spans": e.spans,
            "ctx": t[max(0, e.start - 45):e.end + 45],
            "detector": e.detector,
        })
    return out


if __name__ == "__main__":
    doc_id = sys.argv[1]
    want = sys.argv[2] if len(sys.argv) > 2 else "ADDRESS"
    for r in probe(doc_id, want):
        print(f"{r['seg']:>10} {r['type']:<8} {r['span']} {r['text']!r}")
        print(f"           spans={r['spans']}")
        print(f"           ctx={r['ctx']!r}")
        print()
