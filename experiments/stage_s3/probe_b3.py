# -*- coding: utf-8 -*-
"""Зонд этапа S3: что даёт ПОСЕГМЕНТНАЯ детекция и что добавляет B3-путь
(_detect_boundary_entities) — раздельно, до разрешения пересечений.

    venv/Scripts/python.exe experiments/stage_s3/probe_b3.py <doc_id> [TYPE]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract          # noqa: E402
from pipeline import run_detection     # noqa: E402
import tokenizer as TK                 # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")


def main():
    doc_id = sys.argv[1]
    want = sys.argv[2] if len(sys.argv) > 2 else "ORG"
    path = None
    for ext in (".txt", ".docx"):
        p = os.path.join(DOCS, doc_id + ext)
        if os.path.isfile(p):
            path = p
            break
    doc = extract(path)
    segs = {s.id: s.text for s in doc.segments}
    ents = run_detection(doc, CONFIG)
    print("=== ПОСЕГМЕНТНО ===")
    for e in sorted(ents, key=lambda e: (e.segment_id, e.start)):
        if want != "*" and e.entity_type != want:
            continue
        print(f"  {e.segment_id:>8} [{e.start}:{e.end}] {e.original_text!r}")
    print("=== B3 (граничные окна) ===")
    for e in TK._detect_boundary_entities(doc, CONFIG):
        if want != "*" and e.entity_type != want:
            continue
        t = segs[e.segment_id]
        print(f"  {e.segment_id:>8} [{e.start}:{e.end}] {e.original_text!r}"
              f"   ctx={t[max(0, e.start-30):e.end+30]!r}")


if __name__ == "__main__":
    main()
