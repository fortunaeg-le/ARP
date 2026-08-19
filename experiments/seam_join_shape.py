# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN, разведка №3: ФОРМА разрыва у каждого утекающего значения.

Для каждой эталонной сущности класса вёрстки с утечкой определяет, лежит ли
значение ВНУТРИ одного сегмента (домен layout_repair) или разорвано ГРАНИЦЕЙ
сегментов (домен граничного окна B3), и какими символами.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract   # noqa: E402

DUMP = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
_LAYOUT = set("\n\r\t        ­​")


def sep_shape(text):
    """Прогоны вёрсточных символов внутри значения — как последовательность."""
    out = []
    i = 0
    while i < len(text):
        if text[i] in _LAYOUT:
            j = i
            while j < len(text) and text[j] in _LAYOUT:
                j += 1
            left = text[i - 1] if i else ""
            right = text[j] if j < len(text) else ""
            out.append((repr(text[i:j]), left.isalnum(), right.isalnum()))
            i = j
        else:
            i += 1
    return out


def main():
    docs = json.load(open(DUMP, encoding="utf-8"))
    shapes = collections.Counter()
    place = collections.Counter()
    rows = []
    for r in docs:
        want = [e for e in r["entities"]
                if (e.get("trick") or "") in ("mut:linebreak", "mut:cell_split")
                and (e.get("leak_v2") or {}).get("status", "none") != "none"]
        if not want:
            continue
        path = os.path.join(DOCS, r["doc_id"] + "." + r["format"])
        doc = extract(path)
        segs = doc.segments
        for e in want:
            t = e["text"]
            loc = None
            for s in segs:
                if t in s.text:
                    loc = "intra"
                    break
            if loc is None:
                for a, b in zip(segs, segs[1:]):
                    for sep in ("\n", "", " "):
                        if t in (a.text + sep + b.text):
                            loc = "cross:" + repr(sep)
                            break
                    if loc:
                        break
            if loc is None:
                loc = "?"
            sh = tuple(sep_shape(t))
            shapes[(e["type"], loc, sh)] += 1
            place[(loc, e["type"])] += 1
            rows.append((r["doc_id"], r["format"], e["type"], loc, sh, t))
    print("== где лежит разрыв ==")
    for (loc, ty), n in sorted(place.items(), key=lambda x: -x[1]):
        print(f"  {loc:12s} {ty:12s} {n}")
    print("\n== форма разрыва (тип, место, прогоны) ==")
    for k, n in shapes.most_common(60):
        print(f"  {n:4d}  {k[0]:11s} {k[1]:12s} {k[2]}")
    print("\n== поимённо ==")
    for row in sorted(rows):
        print(f"{row[0]:34s} {row[1]:5s} {row[2]:11s} {row[3]:12s} {row[4]} :: {row[5]!r}")


if __name__ == "__main__":
    main()
