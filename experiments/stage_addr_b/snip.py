# -*- coding: utf-8 -*-
"""
snip.py — детекция на КУСКЕ текста через настоящий конвейер (pipeline.run_detection).

    venv/Scripts/python.exe experiments/stage_addr_b/snip.py "просп. Кирова, д. 57"
    ... < file.txt          (текст со стандартного ввода, если аргумента нет)

Печатает найденные сущности со спанами и размеченный текст. Нужен для разбора
причин: показывает, ЧТО именно детектор считает границей на конкретной строке,
не гоняя корпус.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")


def run(text: str):
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        doc = extract(path)
        ents = run_detection(doc, CONFIG)
    finally:
        os.unlink(path)
    by_seg = {}
    for e in ents:
        by_seg.setdefault(e.segment_id, []).append(e)
    for seg in doc.segments:
        es = sorted(by_seg.get(seg.id, []), key=lambda e: (e.start, e.end))
        print("СЕГМЕНТ %s: %r" % (seg.id, seg.text))
        dt = seg.metadata.get("detection_text")
        if dt is not None and dt != seg.text:
            print("   detection_text: %r" % dt)
        for e in es:
            print("   %-10s [%3d:%3d] %r%s" % (e.entity_type, e.start, e.end,
                                               e.original_text,
                                               "  spans=%s" % (e.spans,) if e.spans else ""))
        if not es:
            print("   (пусто)")
    return ents


if __name__ == "__main__":
    txt = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    run(txt.replace("\\n", "\n"))
