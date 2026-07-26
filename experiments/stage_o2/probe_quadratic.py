# -*- coding: utf-8 -*-
"""
O2 — стоят ли чего-нибудь два снятых квадратичных прохода.

На корпусе и обеих фикстурах шаг 4 не дал ИЗМЕРИМОГО выигрыша (разница в
пределах шума ±2%). Это не значит, что менять было нечего: значит, ни один
документ этих нагрузок не создаёт плотности, при которой квадратичность
проявляется. Скрипт меряет ровно её — на синтетической плотности, а не на
догадках, чтобы решение «оставить или откатить» принималось по числам.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import tokenizer as TK              # noqa: E402
from models import Entity           # noqa: E402


def old_has(entities):
    """Реализация до шага 4 — попарное сравнение."""
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            if TK._overlaps(entities[i], entities[j]):
                return True
    return False


def mk(k, step, length):
    return [
        Entity(id="x", segment_id="s", start=i * step, end=i * step + length,
               original_text="", entity_type="INN" if i % 2 else "PHONE",
               detector="regex", confidence=1.0)
        for i in range(k)
    ]


def main():
    new_has = TK._has_overlapping_pair
    for title, step, length in (
        ("соседние ПЕРЕСЕКАЮТСЯ (ранний выход попарного поиска)", 10, 15),
        ("НИ ОДНА не пересекается (попарный поиск идёт до конца)", 10, 5),
    ):
        print("--- _resolve_overlaps: один сегмент, K сущностей; %s ---" % title)
        print("  %5s %12s %12s %10s" % ("K", "было, мс", "стало, мс", "ускорение"))
        for k in (20, 50, 100, 200, 400):
            ents = mk(k, step, length)
            t = time.perf_counter(); TK._resolve_overlaps(list(ents)); tn = time.perf_counter() - t
            TK._has_overlapping_pair = old_has
            t = time.perf_counter(); TK._resolve_overlaps(list(ents)); to = time.perf_counter() - t
            TK._has_overlapping_pair = new_has
            print("  %5d %12.2f %12.2f %9.1fx" % (k, to * 1000, tn * 1000, (to / tn) if tn else 0))
        print()

    print()
    print("--- инвариант detect_ner: N сегментов x E сущностей ---")
    print("  %6s %6s %12s %12s %10s" % ("N", "E", "было, мс", "стало, мс", "ускорение"))

    class S:
        __slots__ = ("id", "text")

        def __init__(self, i):
            self.id = "p%d" % i
            self.text = "x" * 50

    for N, E in ((1000, 200), (5000, 500), (5000, 2000)):
        segs = [S(i) for i in range(N)]
        eids = ["p%d" % (i % N) for i in range(E)]
        t = time.perf_counter()
        for eid in eids:
            next(s for s in segs if s.id == eid)
        to = time.perf_counter() - t
        t = time.perf_counter()
        by = {}
        for s in segs:
            by.setdefault(s.id, s)
        for eid in eids:
            by[eid]
        tn = time.perf_counter() - t
        print("  %6d %6d %12.1f %12.2f %9.0fx" % (N, E, to * 1000, tn * 1000, (to / tn) if tn else 0))


if __name__ == "__main__":
    main()
