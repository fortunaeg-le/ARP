# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN, разведка №5: журнал стража сборки на ремонте стыка B3.

Оборачивает layout_repair._strict_seam_ok и печатает каждое решение на
документах класса вёрстки: что собралось из половин и что страж отверг.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract                        # noqa: E402
from pipeline import run_detection                   # noqa: E402
from tokenizer import _detect_boundary_entities      # noqa: E402
import layout_repair as LR                           # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

_orig = LR._strict_seam_ok
LOG = []


def _wrapped(matched, etype, ctx, start, config_path):
    ok = _orig(matched, etype, ctx, start, config_path)
    LOG.append((etype, matched, ok, ctx[max(0, start - 45):start]))
    return ok


LR._strict_seam_ok = _wrapped


def main():
    names = sys.argv[1:]
    if not names:
        names = sorted(f[:-4] if f.endswith(".txt") else f[:-5]
                       for f in os.listdir(DOCS)
                       if "linebreak" in f or "cell_split" in f)
    stat = collections.Counter()
    for n in names:
        path = os.path.join(DOCS, n + ".txt")
        if not os.path.exists(path):
            path = os.path.join(DOCS, n + ".docx")
        LOG.clear()
        doc = extract(path)
        prim = run_detection(doc, CONFIG)
        _detect_boundary_entities(doc, CONFIG, prim)
        for etype, matched, ok, before in LOG:
            stat[(etype, ok)] += 1
            if not ok:
                print(f"  ОТКАЗ {etype:12s} {matched!r:28s} слева: {before!r}")
    print("\n== свод стража сборки ==")
    for (etype, ok), n in sorted(stat.items()):
        print(f"  {etype:12s} {'пропущен' if ok else 'ОТВЕРГНУТ':10s} {n}")


if __name__ == "__main__":
    main()
