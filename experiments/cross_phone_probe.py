# -*- coding: utf-8 -*-
"""CROSSTYPE — случай 6: телефон, названный ИНН организации."""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
OUT = os.path.join(HERE, "cross_phone_probe.txt")

DOC = "works_0011__m2718_linebreak"


def main():
    w = io.open(OUT, "w", encoding="utf-8")
    for ext in ("txt", "docx"):
        path = os.path.join(DOCS, DOC + "." + ext)
        if not os.path.exists(path):
            continue
        doc = extract(path)
        for s in doc.segments:
            if "499" in (s.text or ""):
                w.write("SEG %s %r\n" % (s.id, s.text))
        for e in run_detection(doc, CONFIG):
            if "499" in (e.original_text or "") or "293" in (e.original_text or ""):
                w.write("ENT %-12s seg=%-8s [%d:%d] det=%s spans=%s %r\n"
                        % (e.entity_type, e.segment_id, e.start, e.end,
                           e.detector, e.spans, e.original_text))
    w.close()
    print("ok")


if __name__ == "__main__":
    main()
