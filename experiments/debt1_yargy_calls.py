# -*- coding: utf-8 -*-
"""DEBT-1 часть 1: перепись ВЫЗОВОВ yargy-адреса — сколько, на чём, почём.

Отвечает на три вопроса профиля:
  1. сколько вызовов приходится на ПОВТОРЯЮЩИЙСЯ текст (кеш их снимет);
  2. как стоимость зависит от длины текста (Earley — суперлинеен);
  3. сколько вызовов дают ПУСТОЙ результат (отсев их снимет).
"""
import collections
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
import subset_lib as SL  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
import ner_detector as ND  # noqa: E402

CALLS = []          # (len, dt, n_matches, text)
_orig = ND._addr_extractor


def _spy(text):
    t0 = time.perf_counter()
    res = list(_orig(text))
    dt = time.perf_counter() - t0
    CALLS.append((len(text), dt, len(res), text))
    return res


def load_docs(n):
    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    ids = [i for i in SL.load_subset_ids() if i in by_id][:n]
    out = []
    for doc_id in ids:
        path = None
        for cand in os.listdir(DOCS):
            if cand.startswith(doc_id):
                path = os.path.join(DOCS, cand)
                break
        out.append((doc_id, extract(path)))
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    docs = load_docs(n)
    run_detection(docs[0][1], CONFIG)   # прогрев
    ND._addr_extractor = _spy
    CALLS.clear()
    t0 = time.perf_counter()
    for _, d in docs:
        run_detection(d, CONFIG)
    wall = time.perf_counter() - t0

    total = sum(c[1] for c in CALLS)
    print("документов %d, wall %.2f c, вызовов yargy %d, в них %.2f c (%.1f%%)"
          % (n, wall, len(CALLS), total, 100.0 * total / wall))

    # 1. повторы текста
    by_text = collections.defaultdict(list)
    for ln, dt, nm, txt in CALLS:
        by_text[txt].append(dt)
    dup_calls = sum(len(v) - 1 for v in by_text.values())
    dup_time = sum(sum(v[1:]) for v in by_text.values())
    print("  уникальных текстов %d; ПОВТОРНЫХ вызовов %d, их время %.2f c (%.1f%%)"
          % (len(by_text), dup_calls, dup_time, 100.0 * dup_time / total))

    # 2. пустой результат
    empty_n = sum(1 for c in CALLS if c[2] == 0)
    empty_t = sum(c[1] for c in CALLS if c[2] == 0)
    print("  пустых результатов %d, их время %.2f c (%.1f%%)"
          % (empty_n, empty_t, 100.0 * empty_t / total))

    # 3. распределение по длине
    print("\n  длина текста      вызовов    время, c   доля")
    buckets = [(0, 200), (200, 500), (500, 1000), (1000, 2000),
               (2000, 5000), (5000, 10 ** 9)]
    for lo, hi in buckets:
        sel = [c for c in CALLS if lo <= c[0] < hi]
        if not sel:
            continue
        t = sum(c[1] for c in sel)
        print("  %6d..%-9d %7d %10.2f  %5.1f%%"
              % (lo, hi, len(sel), t, 100.0 * t / total))

    # 4. самые дорогие вызовы
    print("\n  10 самых дорогих вызовов:")
    for ln, dt, nm, txt in sorted(CALLS, key=lambda c: -c[1])[:10]:
        print("    %7.3f c  len=%-6d matches=%-3d  %r" % (dt, ln, nm, txt[:70]))


if __name__ == "__main__":
    main()
