# -*- coding: utf-8 -*-
"""DEBT-1 часть 1: профиль разбора адреса. Куда именно уходит время внутри.

Запуск:
    venv/Scripts/python.exe experiments/debt1_profile.py [N_DOCS]

Однопроцессный (Pool исказил бы профиль), поэтому берём срез документов.
"""
import cProfile
import io
import os
import pstats
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

import run_measurement as RM  # noqa: E402
import subset_lib as SL  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402


def load_docs(n):
    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    ids = [i for i in SL.load_subset_ids() if i in by_id][:n]
    out = []
    for doc_id in ids:
        rec = by_id[doc_id]
        path = os.path.join(DOCS, rec["file"]) if "file" in rec else None
        if path is None or not os.path.isfile(path):
            for cand in os.listdir(DOCS):
                if cand.startswith(doc_id):
                    path = os.path.join(DOCS, cand)
                    break
        out.append((doc_id, extract(path)))
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    docs = load_docs(n)
    print("документов: %d, сегментов: %d" % (
        len(docs), sum(len(d.segments) for _, d in docs)))

    # прогрев (загрузка моделей Natasha не должна попасть в профиль)
    run_detection(docs[0][1], CONFIG)

    t0 = time.perf_counter()
    pr = cProfile.Profile()
    pr.enable()
    for _, d in docs:
        run_detection(d, CONFIG)
    pr.disable()
    wall = time.perf_counter() - t0
    print("wall: %.2f c" % wall)

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("tottime")
    ps.print_stats(35)
    print(s.getvalue())

    s2 = io.StringIO()
    ps2 = pstats.Stats(pr, stream=s2).sort_stats("cumulative")
    ps2.print_stats(45)
    print(s2.getvalue())


if __name__ == "__main__":
    main()
