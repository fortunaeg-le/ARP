# -*- coding: utf-8 -*-
"""DEBT-1 часть 1: проверка гипотезы «predict в колонке идемпотентен».

Гипотеза: Parser.predict(column, next_column, rule) при повторном вызове с ТЕМ ЖЕ
rule в ТОЙ ЖЕ колонке не может добавить ни одного состояния — все порождаемые им
State имеют dot_index=0, start=stop=column, node.children=(), то есть их хэш
совпадает с хэшем состояний первого вызова и column.append их отбрасывает.

Скрипт: (1) считает долю повторных predict; (2) сверяет РЕЗУЛЬТАТ findall
до/после дедупликации на всех сегментах среза — побайтно по спанам и фактам.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

import run_measurement as RM  # noqa: E402
from extractor import extract  # noqa: E402
from normalizer import detection_view  # noqa: E402
from yargy import parser as YP  # noqa: E402
from natasha import AddrExtractor, MorphVocab  # noqa: E402

_ORIG_PREDICT = YP.Parser.predict
STATS = {"calls": 0, "dup": 0}


def _counting_predict(self, column, next_column, rule):
    STATS["calls"] += 1
    seen = getattr(column, "_dbg", None)
    if seen is None:
        seen = column._dbg = set()
    if id(rule) in seen:
        STATS["dup"] += 1
    else:
        seen.add(id(rule))
    return _ORIG_PREDICT(self, column, next_column, rule)


def _dedup_predict(self, column, next_column, rule):
    seen = getattr(column, "_pred", None)
    if seen is None:
        seen = column._pred = set()
    key = id(rule)
    if key in seen:
        return
    seen.add(key)
    return _ORIG_PREDICT(self, column, next_column, rule)


def texts(n):
    gold = RM.load_gold()
    ids = [d["doc_id"] for d in gold][:n]
    out = []
    for doc_id in ids:
        path = None
        for cand in os.listdir(DOCS):
            if cand.startswith(doc_id):
                path = os.path.join(DOCS, cand)
                break
        if not path:
            continue
        for seg in extract(path).segments:
            if seg.text:
                out.append(detection_view(seg)[0])
    return out


def fingerprint(matches):
    return [(m.start, m.stop, repr(m.fact)) for m in matches]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    ex = AddrExtractor(MorphVocab())
    tt = texts(n)
    print("сегментов: %d" % len(tt))

    # 1. доля повторных predict
    YP.Parser.predict = _counting_predict
    for t in tt[:400]:
        list(ex(t))
    YP.Parser.predict = _ORIG_PREDICT
    print("predict: всего %d, из них ПОВТОРНЫХ %d (%.1f%%)"
          % (STATS["calls"], STATS["dup"], 100.0 * STATS["dup"] / max(1, STATS["calls"])))

    # 2. эквивалентность результата + время
    t0 = time.perf_counter()
    before = [fingerprint(list(ex(t))) for t in tt]
    t_before = time.perf_counter() - t0

    YP.Parser.predict = _dedup_predict
    t0 = time.perf_counter()
    after = [fingerprint(list(ex(t))) for t in tt]
    t_after = time.perf_counter() - t0
    YP.Parser.predict = _ORIG_PREDICT

    diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    print("время: было %.2f c, стало %.2f c (x%.2f)"
          % (t_before, t_after, t_before / max(1e-9, t_after)))
    print("расхождений результата: %d из %d сегментов" % (len(diff), len(tt)))
    for i in diff[:5]:
        print("  сегмент %d:\n    было  %r\n    стало %r" % (i, before[i], after[i]))


if __name__ == "__main__":
    main()
