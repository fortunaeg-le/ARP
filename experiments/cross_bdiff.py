# -*- coding: utf-8 -*-
"""CROSSTYPE — откуда 4 новые красные строки «masking B границы».

Сличает маски типов INN/INN_PER/OGRN до и после правки: сколько масок
появилось, сколько потеряло b_ok, и как выглядит текст тех, что потеряли.
"""
import io
import os
import sys
import json
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cross_bdiff.txt")
TYPES = ("INN", "INN_PER", "OGRN", "BANK_ACCOUNT", "SNILS")


def load(p):
    d = json.load(open(p, encoding="utf-8"))
    return d["results"] if isinstance(d, dict) else d


def index(results):
    out = {}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        cnt = collections.Counter()
        for m in r.get("masks", ()):
            if m["gtype"] not in TYPES:
                continue
            key = (r["doc_id"], m["gtype"], m["text"], cnt[(m["gtype"], m["text"])])
            cnt[(m["gtype"], m["text"])] += 1
            out[key] = m
    return out


def main():
    before, after = index(load(sys.argv[1])), index(load(sys.argv[2]))
    w = io.open(OUT, "w", encoding="utf-8")
    per = collections.Counter()
    for t in TYPES:
        b = [m for k, m in before.items() if k[1] == t]
        a = [m for k, m in after.items() if k[1] == t]
        def stat(xs):
            sc = [x for x in xs if x.get("scored")]
            return len(xs), len(sc), sum(1 for x in sc if x.get("b_ok"))
        w.write("%-13s было масок/оценено/b_ok %s   стало %s\n"
                % (t, stat(b), stat(a)))
    w.write("\nНОВЫЕ маски (есть после, нет до):\n")
    for k, m in sorted(after.items()):
        if k not in before:
            per[(k[1], bool(m.get("b_ok")))] += 1
            w.write("  + %-28s %-9s b_ok=%-5s c_ok=%-5s gold=%-9s %r\n"
                    % (k[0], k[1], m.get("b_ok"), m.get("c_ok"),
                       m.get("gold_type"), m["text"]))
    w.write("\nИСЧЕЗНУВШИЕ маски (есть до, нет после):\n")
    for k, m in sorted(before.items()):
        if k not in after:
            w.write("  - %-28s %-9s b_ok=%-5s gold=%-9s %r\n"
                    % (k[0], k[1], m.get("b_ok"), m.get("gold_type"), m["text"]))
    w.write("\nИТОГ по новым маскам (тип, b_ok): %s\n" % dict(per))
    w.close()
    print(open(OUT, encoding="utf-8").read()[-4000:])


if __name__ == "__main__":
    main()
