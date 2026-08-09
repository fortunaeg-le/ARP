# -*- coding: utf-8 -*-
"""ЭТАП DEBTS, долг AUD1-Z-SUPPRESS. Сколько раз линия «ж» вообще срабатывает.

Считает подавления (отрицательный класс победил маскируемого кандидата) на
корпусе v2 — тем же путём, что гейт считает их на v1: `tokenize(...,
suppression_log=...)`. Печатает и общее число (диагностика), и пересечение с
ЭТАЛОНОМ (блокирующее число линии «ж», обязано быть 0).

Запуск: venv/Scripts/python.exe experiments/stage_debts/suppression_probe.py [N]
"""
import os
import sys
import glob
import json
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract          # noqa: E402
from pipeline import run_detection     # noqa: E402
from tokenizer import tokenize         # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
CORPUS = os.path.join(ROOT, "tests", "corpus_v2", "docs")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    limit = int(argv[0]) if argv else 0
    files = sorted(glob.glob(os.path.join(CORPUS, "*.txt")))
    if limit:
        files = files[:limit]
    by_pair = collections.Counter()
    total = 0
    for path in files:
        doc = extract(path)
        entities = run_detection(doc, CONFIG)
        log = []
        tokenize(doc, entities, CONFIG, suppression_log=log)
        for s in log:
            by_pair[(s["suppressor_type"], s["suppressed_type"])] += 1
            total += 1
    print("документов:", len(files))
    print("всего подавлений:", total)
    for (sup, victim), n in by_pair.most_common():
        print("   %-12s подавил %-12s x%d" % (sup, victim, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
