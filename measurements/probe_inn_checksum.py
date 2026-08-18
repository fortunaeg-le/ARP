# -*- coding: utf-8 -*-
"""Почему INN отбирает спан у PASSPORT: проверка контрольной суммы ИНН-10
на тех паспортных номерах, которые перехвачены."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

VALS = ["31 21 174310", "88 04 537618", "07 23 184021", "10 10 995117",
        "84 02 932671", "20 17 088328"]

def inn10_ok(s):
    d = [int(c) for c in s if c.isdigit()]
    if len(d) != 10:
        return None
    w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    return (sum(a*b for a, b in zip(w, d[:9])) % 11) % 10 == d[9]

out = open(os.path.join(HERE, "probe_inn_checksum.txt"), "w", encoding="utf-8")
out.write("значение          цифр  контрольная сумма ИНН-10\n")
for v in VALS:
    d = [c for c in v if c.isdigit()]
    out.write("%-18s %2d    %s\n" % (v, len(d), inn10_ok(v)))
out.write("\nКонтроль: доля СЛУЧАЙНЫХ 10-значных чисел, проходящих контроль ИНН-10, ~1/10.\n")
out.close()
print("ok")
