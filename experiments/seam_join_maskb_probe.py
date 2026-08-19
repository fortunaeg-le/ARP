# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN, разведка №6: сколько линии «г» отнимает НЕМАСКИРУЕМЫЙ ШОВ.

Дефект прибора `CHARNORM-MASKB-B3-SEPARATOR`. Значение, разорванное вёрсткой и
закрытое ПАРОЙ масок B3, спрятано целиком — но разделитель между половинами
('\n' между строками PT-1, '\t' между ячейками) не принадлежит ни одной маске,
и `_covered_by_union` считает эталон незакрытым. Обе маски пары идут в брак.

Проба гоняет ТОТ ЖЕ харнесс дважды: как есть и с объединением, терпящим зазор
в ОДИН символ (ровно шов, больше ничего), и печатает обе линии «г» по типам.
Прибор в гейте НЕ меняется — это замер цены, а не правка.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML          # noqa: E402
import run_measurement as RM      # noqa: E402

_orig = ML._covered_by_union


def _tolerant(gs, ge, spans):
    """То же покрытие, но зазор в ОДИН символ между масками не считается дырой."""
    cur = gs
    for s, e in sorted(spans):
        if s > cur + 1:
            return False
        cur = max(cur, e)
        if cur >= ge:
            return True
    return cur >= ge


def report(results, title):
    agg = ML.aggregate_results(results)
    mc = agg["masking_correctness"]
    tot = mc["total"]
    print(f"\n=== {title} ===")
    print("  агрегат B: %.2f%% (%d/%d)" % (
        100.0 * tot["b_ok"] / tot["n_scored"] if tot["n_scored"] else 0.0,
        tot["b_ok"], tot["n_scored"]))
    for t, s in sorted(mc["per_type"].items()):
        if not s["n_scored"]:
            continue
        print("    %-16s %6.2f%%  (%d/%d)" % (
            t, 100.0 * s["b_ok"] / s["n_scored"], s["b_ok"], s["n_scored"]))


def main():
    # workers=1 ОБЯЗАТЕЛЕН: подмена процедуры живёт в памяти этого процесса и до
    # воркеров Pool на Windows не доезжает — с воркерами проба молча вернула бы
    # те же числа, что гейт, и это выглядело бы как «шов ни при чём».
    ML._covered_by_union = _tolerant
    gold = RM.load_gold()
    print("документов: %d (однопоточно, подмена _covered_by_union)" % len(gold))
    res = RM.run_all(gold, verbose=True, workers=1)
    report(res, "линия «г» С ТЕРПИМЫМ ШВОМ (зазор 1 символ)")
    with open(os.path.join(HERE, "sj_maskb_tolerant.json"), "w",
              encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
