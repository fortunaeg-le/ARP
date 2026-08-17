# -*- coding: utf-8 -*-
"""ЭТАП DEBTS. Перебор границ ПО МЕХАНИЗМУ, а не одним числом.

Линия «е» гейта печатает «перебор: N сущностей / M символов» — этого хватает,
чтобы заметить регресс, и не хватает, чтобы чинить: неизвестно, слева лишнее
или справа и какой это текст. Здесь то же поле `bnd` разложено по механизмам:
сторона (лево/право), длина хвоста, сам захваченный текст.

Читает готовый дамп прогона (`tests/corpus/results_gate_current.json` или
переданный первым аргументом) — корпус заново не гоняет.

Запуск: venv/Scripts/python.exe experiments/stage_debts/overcapture_breakdown.py [ТИП] [дамп]
"""
import os
import sys
import json
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_DUMP = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    want_type = argv[0] if argv else "ADDRESS"
    dump = argv[1] if len(argv) > 1 else DEFAULT_DUMP
    results = json.load(open(dump, encoding="utf-8"))

    by_len = collections.Counter()
    n = chars = 0
    samples = []
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if e["type"] != want_type:
                continue
            b = e.get("bnd")
            if not b or b["direction"] not in ("longer", "both"):
                continue
            n += 1
            chars += b["over"]
            by_len[b["over"]] += 1
            if len(samples) < 40:
                samples.append((r["doc_id"], b["over"], e["text"]))

    print("тип: %s   дамп: %s" % (want_type, os.path.basename(dump)))
    print("сущностей с перебором: %d, символов: %d" % (n, chars))
    print("распределение длины перебора (символов -> сущностей):")
    for k, v in sorted(by_len.items()):
        print("   %3d -> %d" % (k, v))
    print("примеры (документ | лишних символов | эталон):")
    for doc_id, over, text in samples:
        print("   %-34s %3d  %r" % (doc_id, over, text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
