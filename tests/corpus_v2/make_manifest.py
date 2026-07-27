# -*- coding: utf-8 -*-
"""
make_manifest.py — свои контрольные суммы корпуса V2.

У корпуса V2 СВОЙ манифест, отдельный от tests/corpus/MANIFEST.sha256.
Старый манифест — 656 файлов старого корпуса, он заморожен навсегда, и
добавление в него хоть одной строки обесценило бы всю историю байтовых сверок.

Запуск:
    venv/Scripts/python.exe tests/corpus_v2/make_manifest.py          # записать
    venv/Scripts/python.exe tests/corpus_v2/make_manifest.py --check  # сверить
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "MANIFEST.sha256")
# Сам манифест в себя не входит; кеш питона и результаты прогонов — тоже.
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {"MANIFEST.sha256"}


def files():
    for root, dirs, names in os.walk(HERE):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            if n in SKIP_FILES or n.endswith(".pyc"):
                continue
            p = os.path.join(root, n)
            yield p, os.path.relpath(p, HERE).replace("\\", "/")


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    rows = [(digest(p), rel) for p, rel in files()]
    text = "".join("%s  %s\n" % (h, rel) for h, rel in rows)

    if args.check:
        if not os.path.exists(OUT):
            print("MANIFEST.sha256 отсутствует")
            sys.exit(1)
        with open(OUT, encoding="utf-8", newline="") as f:
            old = f.read()
        want = dict((r[1], r[0]) for r in rows)
        got = {}
        for line in old.splitlines():
            if line.strip():
                h, rel = line.split("  ", 1)
                got[rel] = h
        bad = [r for r in sorted(set(want) | set(got))
               if want.get(r) != got.get(r)]
        print("MANIFEST V2: файлов %d | расхождений %d" % (len(want), len(bad)))
        for r in bad[:20]:
            print("   %s" % r)
        sys.exit(1 if bad else 0)

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("записано: %s (%d файлов)" % (OUT, len(rows)))


if __name__ == "__main__":
    main()
