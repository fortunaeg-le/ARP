# -*- coding: utf-8 -*-
"""CROSSTYPE, часть 3 — РАЗВЕДКА без правок: где ещё тип A назван типом B так,
что в наборе ПО УМОЛЧАНИЮ значение уходит открытым.

Считает по готовому дампу замера (`masks`-записи run_measurement.process_doc):
пара (gold_type A, тип маски B), где A ВХОДИТ в набор по умолчанию, а B — НЕТ.
Каждая такая пара — заряженное ружьё того же класса, что линия «з»: маска на
максимуме стоит, при настройках по умолчанию её нет.

Линия «з» показывает ПОДМНОЖЕСТВО этого: только случаи, где значение не
прикрыто ВДОБАВОК маской типа из набора по умолчанию. Разница между двумя
числами и есть «латентный» остаток — он молчит ровно до следующей правки
арбитража.

Запуск: venv/Scripts/python.exe experiments/cross_pairs.py <results.json>
"""
import io
import os
import sys
import json
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402

OUT = os.path.join(HERE, "cross_pairs.txt")


def main():
    path = sys.argv[1]
    data = json.load(open(path, encoding="utf-8"))
    results = data["results"] if isinstance(data, dict) else data
    default_types = ML.DEFAULT_PROFILE_GOLD_TYPES

    pairs = collections.Counter()
    docs = collections.defaultdict(set)
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for m in r.get("masks", ()):
            if not m.get("scored"):
                continue
            a, b = m.get("gold_type"), m.get("gtype")
            if not a or not b or a == b:
                continue
            if a in default_types and b not in default_types:
                pairs[(a, b)] += 1
                docs[(a, b)].add(r["doc_id"])

    w = io.open(OUT, "w", encoding="utf-8")
    w.write("дамп: %s\n" % os.path.basename(path))
    w.write("набор по умолчанию: %s\n\n" % sorted(default_types))
    w.write("ПАРЫ «эталон A (в наборе) назван типом B (вне набора)»\n")
    w.write("%-12s %-14s %8s %8s\n" % ("A эталон", "B маска", "масок", "док."))
    total = 0
    for (a, b), n in pairs.most_common():
        w.write("%-12s %-14s %8d %8d\n" % (a, b, n, len(docs[(a, b)])))
        total += n
    w.write("\nВСЕГО масок неверного типа с потерей в наборе по умолчанию: %d\n"
            % total)
    w.close()
    print(open(OUT, encoding="utf-8").read())


if __name__ == "__main__":
    main()
