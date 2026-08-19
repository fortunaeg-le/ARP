# -*- coding: utf-8 -*-
"""ЭТАП INSTR-SEAM, masking B ПО ТИПАМ тремя колонками + проверка соседних линий.

    python experiments/instr_seam_maskb_by_type.py <дамп СТАРЫМ> <дамп НОВЫМ>

Печатает:
  1. таблицу masking B по типам: ТОЧКА ОТСЧЁТА | СТАРЫЙ прибор | НОВЫЙ прибор.
     Тип, который новым прибором оказался НИЖЕ точки отсчёта, — настоящая порча
     границ, а не шов: он называется отдельной строкой в конце;
  2. masking C старым и новым прибором (обязан не двинуться: шов отвечает на
     вопрос «закрыто ли значение», а не «как оно названо»);
  3. линию «б»: есть ли в корпусе ХОТЬ ОДИН фрагмент утечки, состоящий только из
     разделителей сборки. Если их ноль — того же артефакта в линии «б» нет.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = os.path.join(ROOT, "tests", "corpus", "results_baseline.json")
SEAM_CHARS = "\n\t"


def per_type(D):
    ok, n = Counter(), Counter()
    for d in D:
        for r in d.get("masks", []):
            if r["scored"]:
                ok[r["gtype"]] += int(bool(r["b_ok"]))
                n[r["gtype"]] += 1
    return ok, n


def main(p_old, p_new):
    A = json.load(open(p_old, encoding="utf-8"))
    B = json.load(open(p_new, encoding="utf-8"))
    Z = json.load(open(BASELINE, encoding="utf-8"))
    okA, nA = per_type(A)
    okB, nB = per_type(B)
    okZ, nZ = per_type(Z)

    def cell(ok, n, t):
        return ("%6.2f%% (%d/%d)" % (100.0 * ok[t] / n[t], ok[t], n[t])) if n[t] \
            else "     — (0/0)"

    print("masking B ПО ТИПАМ — точка отсчёта | СТАРЫЙ прибор | НОВЫЙ прибор")
    print("%-16s %20s %20s %20s %8s" % ("тип", "ТОЧКА ОТСЧЁТА", "СТАРЫЙ", "НОВЫЙ", "дельта"))
    print("-" * 90)
    for t in sorted(set(nA) | set(nB) | set(nZ)):
        d = (100.0 * okB[t] / nB[t] - 100.0 * okA[t] / nA[t]) if nA[t] and nB[t] else 0.0
        print("%-16s %20s %20s %20s %+8.2f" % (t, cell(okZ, nZ, t), cell(okA, nA, t),
                                               cell(okB, nB, t), d))
    tot = lambda ok, n: (sum(ok.values()), sum(n.values()))
    (oZ, tZ), (oA, tA), (oB, tB) = tot(okZ, nZ), tot(okA, nA), tot(okB, nB)
    print("-" * 90)
    print("%-16s %20s %20s %20s %+8.2f" % (
        "АГРЕГАТ", "%6.2f%% (%d/%d)" % (100.0 * oZ / tZ, oZ, tZ),
        "%6.2f%% (%d/%d)" % (100.0 * oA / tA, oA, tA),
        "%6.2f%% (%d/%d)" % (100.0 * oB / tB, oB, tB),
        100.0 * oB / tB - 100.0 * oA / tA))

    worse = [t for t in sorted(set(nZ)) if nZ[t] and nB[t]
             and 100.0 * okB[t] / nB[t] < 100.0 * okZ[t] / nZ[t] - 1e-9]
    print("\nНИЖЕ ТОЧКИ ОТСЧЁТА НОВЫМ прибором (настоящая порча границ, если есть):")
    print("  " + (", ".join(worse) if worse else "НЕТ НИ ОДНОГО"))

    def ctally(D):
        ok = n = 0
        for d in D:
            for r in d.get("masks", []):
                if r["scored"]:
                    n += 1
                    ok += int(bool(r["c_ok"]))
        return ok, n
    cA, cn = ctally(A)
    cB, _ = ctally(B)
    print("\nmasking C: %d/%d (%.2f%%) -> %d/%d (%.2f%%)  — разница %d записей"
          % (cA, cn, 100.0 * cA / cn, cB, cn, 100.0 * cB / cn, cB - cA))

    frags = seam_frags = 0
    for d in B:
        for e in d.get("entities", []):
            for f in e["leak_v2"].get("fragments", []):
                frags += 1
                if isinstance(f, str) and f and all(ch in SEAM_CHARS for ch in f):
                    seam_frags += 1
    print("линия «б»: фрагментов утечки %d; из них состоящих ТОЛЬКО из '\\n'/'\\t': %d"
          % (frags, seam_frags))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
