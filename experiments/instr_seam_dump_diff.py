# -*- coding: utf-8 -*-
"""ЭТАП INSTR-SEAM, сличение ДВУХ ДАМПОВ на НЕИЗМЕННОМ src/ (приём METRIC-FIX).

    python experiments/instr_seam_dump_diff.py <дамп СТАРЫМ прибором> <дамп НОВЫМ>

Рекурсивно сравнивает записи всех 324 документов ПОЛЕ В ПОЛЕ и печатает, какие
именно пути разошлись и сколько раз. Доказательство узости состоит в том, что
разойтись обязано РОВНО ОДНО поле — `masks[].b_ok`, — а число масок, число
оценённых масок и `c_ok` (masking C) не двинуться ни на единицу.

Дамп «старым прибором» — это `tests/corpus/results_gate_current.json` из
коммита ДО правки; достать его можно так:
    git show <коммит>:tests/corpus/results_gate_current.json > /tmp/before.json
"""
import json
import sys
from collections import Counter


def walk(a, b, path, paths):
    if type(a) is not type(b):
        paths[path + " :ТИП"] += 1
        return
    if isinstance(a, dict):
        if set(a) != set(b):
            paths[path + " :КЛЮЧИ"] += 1
            return
        for k in a:
            walk(a[k], b[k], path + "." + k, paths)
    elif isinstance(a, list):
        if len(a) != len(b):
            paths[path + " :ДЛИНА"] += 1
            return
        for x, y in zip(a, b):
            walk(x, y, path + "[]", paths)
    elif a != b:
        paths[path] += 1


def tally(D):
    n = s = bok = cok = 0
    for d in D:
        for r in d.get("masks", []):
            n += 1
            if r["scored"]:
                s += 1
                bok += int(bool(r["b_ok"]))
                cok += int(bool(r["c_ok"]))
    return n, s, bok, cok


def main(p_old, p_new):
    A = json.load(open(p_old, encoding="utf-8"))
    B = json.load(open(p_new, encoding="utf-8"))
    assert len(A) == len(B), "дампы разной длины — сличать нечего"
    paths = Counter()
    for da, db in zip(A, B):
        assert da["doc_id"] == db["doc_id"], "порядок документов разъехался"
        walk(da, db, "", paths)
    print("документов сличено: %d" % len(A))
    print("\nРАСХОЖДЕНИЯ ПО ПОЛЯМ (путь -> число):")
    for p, n in sorted(paths.items(), key=lambda kv: -kv[1]):
        print("  %-50s %d" % (p, n))
    print("\nвсего расхождений: %d" % sum(paths.values()))
    nA, sA, bA, cA = tally(A)
    nB, sB, bB, cB = tally(B)
    print("\n                 масок  оценено   b_ok   c_ok")
    print("  СТАРЫЙ прибор: %6d %8d %6d %6d" % (nA, sA, bA, cA))
    print("  НОВЫЙ  прибор: %6d %8d %6d %6d" % (nB, sB, bB, cB))
    print("\n  масок не изменилось: %s;  оценено не изменилось: %s;  "
          "masking C не изменился: %s" % (nA == nB, sA == sB, cA == cB))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
