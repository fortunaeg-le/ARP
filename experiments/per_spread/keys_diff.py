# -*- coding: utf-8 -*-
"""Сравнение РЕЕСТРА ЯКОРЕЙ до/после правки на чистых документах корпуса.

Запускается ДВАЖДЫ разными процессами (флаг --orig подкладывает src из HEAD),
результат пишется в JSON, третий запуск (--diff) печатает расхождение.
"""
import collections
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT_NEW = os.path.join(ROOT, "experiments/per_spread/keys_new.json")
OUT_OLD = os.path.join(ROOT, "experiments/per_spread/keys_old.json")


def collect(srcdir, out):
    sys.path.insert(0, srcdir)
    from extractor import extract          # noqa: E402
    import anchor_registry as AR           # noqa: E402

    gold = json.load(open(os.path.join(ROOT, "tests/corpus/gold.json"), encoding="utf-8"))
    res = {}
    for g in gold:
        if "__m" in g["doc_id"]:
            continue
        path = glob.glob(os.path.join(ROOT, "tests/corpus/docs", g["doc_id"] + ".*"))[0]
        doc = extract(path)
        keys = []
        orig_add = AR.Registry.add
        AR.Registry.add = lambda self, key, et, canon, ev, conf, **kw: (
            keys.append([et, list(key)]), orig_add(self, key, et, canon, ev, conf, **kw))[1]
        try:
            AR.detect_structural(doc, regex_entities=[])
        finally:
            AR.Registry.add = orig_add
        res[g["doc_id"]] = sorted({(e, tuple(k)) for e, k in keys})
        res[g["doc_id"]] = [[e, list(k)] for e, k in res[g["doc_id"]]]
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print("записано:", out)


if "--orig" in sys.argv:
    tmp = tempfile.mkdtemp()
    srcdir = os.path.join(tmp, "src")
    shutil.copytree(os.path.join(ROOT, "src"), srcdir,
                    ignore=shutil.ignore_patterns("__pycache__"))
    blob = subprocess.check_output(["git", "show", "HEAD:src/anchor_registry.py"], cwd=ROOT)
    open(os.path.join(srcdir, "anchor_registry.py"), "wb").write(blob)
    collect(srcdir, OUT_OLD)
elif "--diff" in sys.argv:
    old = json.load(open(OUT_OLD, encoding="utf-8"))
    new = json.load(open(OUT_NEW, encoding="utf-8"))
    add = collections.Counter()
    rem = collections.Counter()
    for d in old:
        o = {(e, tuple(k)) for e, k in old[d]}
        n = {(e, tuple(k)) for e, k in new[d]}
        for x in n - o:
            add[x] += 1
        for x in o - n:
            rem[x] += 1
    print("ПРОПАЛО ключей (типов уникальных %d):" % len(rem))
    for x, c in rem.most_common():
        print("   -%3d  %s %s" % (c, x[0], "|".join(x[1])))
    print()
    print("ПОЯВИЛОСЬ ключей (типов уникальных %d):" % len(add))
    for x, c in add.most_common():
        print("   +%3d  %s %s" % (c, x[0], "|".join(x[1])))
else:
    collect(os.path.join(ROOT, "src"), OUT_NEW)
