# -*- coding: utf-8 -*-
"""Фронт-1 дамп: прогоняет run_detection на .txt/.docx и печатает КАЖДУЮ найденную
сущность (тип + точный original_text + сегмент), затем маскированный текст.
Ничего не пишет в корпус. Запуск:
    venv/Scripts/python.exe experiments/stage_c/dump.py experiments/stage_c/dogovor_test.txt
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract  # noqa
from pipeline import run_detection  # noqa
from tokenizer import tokenize  # noqa

CONFIG = os.path.join(ROOT, "entity_types.yaml")


def main():
    path = sys.argv[1]
    doc = extract(path)
    entities = run_detection(doc, CONFIG)
    anon_text, kept = tokenize(doc, entities, CONFIG)

    seg_by_id = {s.id: s for s in doc.segments}
    # печатаем KEPT (после разрешения пересечений в tokenize) — ровно то, что маскируется
    ents = sorted(kept, key=lambda e: (e.segment_id, e.start))
    print("=" * 78)
    print("НАЙДЕННЫЕ СУЩНОСТИ (kept=%d, raw=%d):" % (len(kept), len(entities)))
    print("=" * 78)
    cur_seg = None
    for e in ents:
        if e.segment_id != cur_seg:
            cur_seg = e.segment_id
            seg = seg_by_id[e.segment_id]
            print("\n[%s] %s" % (e.segment_id, seg.text.strip()[:110]))
        print("    %-10s [%3d:%3d] %r" % (e.entity_type, e.start, e.end, e.original_text))

    print("\n" + "=" * 78)
    print("МАСКИРОВАННЫЙ ТЕКСТ:")
    print("=" * 78)
    print(anon_text)


if __name__ == "__main__":
    main()
