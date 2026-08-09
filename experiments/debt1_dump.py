# -*- coding: utf-8 -*-
"""DEBT-1: отпечаток ДЕТЕКЦИИ по документам корпуса + время.

Запускается ОДИНАКОВО в обоих рабочих деревьях; файлы сравниваются побайтно.
    venv/Scripts/python.exe experiments/debt1_dump.py <out.json> [N]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

import run_measurement as RM  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402


def main():
    out_path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    gold = RM.load_gold()
    ids = sorted(d["doc_id"] for d in gold)[:n]
    files = sorted(os.listdir(DOCS))
    dump = {}
    docs = []
    for doc_id in ids:
        path = next((os.path.join(DOCS, c) for c in files if c.startswith(doc_id)), None)
        if path:
            docs.append((doc_id, extract(path)))
    run_detection(docs[0][1], CONFIG)   # прогрев
    t0 = time.perf_counter()
    for doc_id, d in docs:
        ents = run_detection(d, CONFIG)
        dump[doc_id] = sorted(
            [e.segment_id, e.start, e.end, e.entity_type, e.detector,
             e.original_text, [list(s) for s in (e.spans or [])]]
            for e in ents
        )
    wall = time.perf_counter() - t0
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, sort_keys=True, indent=0)
    print("документов %d, сущностей %d, детекция %.2f c"
          % (len(dump), sum(len(v) for v in dump.values()), wall))


if __name__ == "__main__":
    main()
