# -*- coding: utf-8 -*-
"""Разведка PER-SPREAD: какие PER-вхождения не найдены и есть ли у них
однофамильный СОСЕД, который в том же документе НАЙДЕН."""
import json, collections, re, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "src"))
import measure_lib as ML
from ner_detector import _morph_vocab as M

W = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*")

def lem(w):
    f = M.normal_forms(w)
    return f[0].lower() if f else w.lower()

def surn_lemmas(text):
    """леммы токенов, которым pymorphy даёт помету Surn (порог как в src)"""
    out = set()
    for w in W.findall(text):
        if len(w) < 2:
            continue
        for p in M(w):
            if p.score >= 0.01 and "Surn" in p.tag:
                out.add(lem(w))
                break
    return out

dump = json.load(open(sys.argv[1] if len(sys.argv) > 1 else
                      os.path.join(ROOT, "tests/corpus/results_gate_current.json"),
                      encoding="utf-8"))

cnt = collections.Counter()
samples = collections.defaultdict(list)
for r in dump:
    pers = [e for e in r["entities"] if e["type"] == "PER"]
    found_sl = set()
    for e in pers:
        if e["found"]:
            found_sl |= surn_lemmas(e["text"])
    for e in pers:
        if e["found"]:
            continue
        mr = e.get("miss_reason") or "nothing"
        sl = surn_lemmas(e["text"])
        has_twin = bool(sl & found_sl)
        key = (mr, "ЕСТЬ найденный однофамилец" if has_twin
               else ("фамилия не опознана морфологией" if not sl else "нет найденного однофамильца"))
        cnt[key] += 1
        if len(samples[key]) < 12:
            samples[key].append((r["doc_id"], e["text"]))

for k, c in cnt.most_common():
    print("%5d  %-16s | %s" % (c, k[0], k[1]))
print()
for k in sorted(samples, key=lambda k: -cnt[k]):
    print("=== %s | %s (%d) ===" % (k[0], k[1], cnt[k]))
    for d, t in samples[k]:
        print("   ", d, "|", repr(t))
