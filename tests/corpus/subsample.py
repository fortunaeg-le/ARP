# -*- coding: utf-8 -*-
"""
subsample.py — быстрая подвыборка корпуса для ОТЛАДКИ между правками этапа.

============================================================================
ПОДВЫБОРКА — ЧЕРНОВИК ДЛЯ ОТЛАДКИ. НЕ ЗАМЕНЯЕТ tests/corpus/gate.py.
Полный прогон перед коммитом, трогающим src/, ОБЯЗАТЕЛЕН как прежде —
регресс на редком типе документа, не попавшем в подвыборку, здесь МОЖЕТ
БЫТЬ НЕ ВИДЕН.
============================================================================

Обоснование (docs/archive/reports/EFFICIENCY_AUDIT.md §2.2): без подвыборки
итерации внутри сессии либо гоняют полный корпус (8-20 мин за прогон), либо
проверяются вручную на одном документе — HANDOFF_STAGE_4.md §3.1
документирует случай, где именно точечная проверка одного документа
пропустила регрессию (гомоглиф-мутация якорного слова, редкий класс),
которую поймал только полный прогон.

Запуск:
    venv/Scripts/python.exe tests/corpus/subsample.py
    venv/Scripts/python.exe tests/corpus/subsample.py --trick homoglyph_cyrillic_in_digits
    venv/Scripts/python.exe tests/corpus/subsample.py --type INN --type OGRN
    venv/Scripts/python.exe tests/corpus/subsample.py --feature nested_table

Состав подвыборки — ОБЪЕДИНЕНИЕ:
  * по 2 base-документа каждого contract_type (детерминированно — первые два
    в отсортированном порядке doc_id; те же 18, что в
    tests/test_corpus_no_crash.py::_fast_doc_ids);
  * граничные документы, явно названные в docs/FINDINGS.md как редкие
    вскрытые дефекты (Stage4-B, PER-B) — точечная проверка одного документа
    исторически их пропускала, поэтому они в подвыборке ВСЕГДА, независимо
    от фильтров;
  * ВСЕ документы, где хоть одна gold-сущность имеет entities[].trick,
    входящий в --trick (если задан, можно указать несколько раз);
  * ВСЕ документы, где хоть одна gold-сущность имеет entities[].type,
    входящий в --type (если задан);
  * ВСЕ документы, у которых features содержит хоть один из --feature
    (если задан).

Без фильтров — типично 20-25 документов. С --trick/--type/--feature — до
30-50, в зависимости от того, сколько документов корпуса несут эту фичу.

Гоняет ТОТ ЖЕ харнесс (run_measurement.process_doc/run_all), что gate.py —
формат отчёта (recall/leak_v2/FP по типу) сопоставим построчно с
results_gate_current.json, НО НЕ по абсолютным числам (другой, меньший n) и
БЕЗ сравнения с results_baseline.json — только срез по текущей подвыборке.
Финальную приёмку/сравнение с baseline делает исключительно gate.py.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402

_WARNING = (
    "\n" + "=" * 78 + "\n"
    "ПОДВЫБОРКА — ЧЕРНОВИК ДЛЯ ОТЛАДКИ.\n"
    "НЕ заменяет финальный полный прогон tests/corpus/gate.py перед коммитом.\n"
    "Регресс на редком типе, не попавшем в подвыборку, здесь может быть НЕ виден.\n"
    + "=" * 78 + "\n"
)

# Документы, явно названные в docs/FINDINGS.md как редкие вскрытые дефекты
# (Stage4-B, PER-B) — всегда часть подвыборки, независимо от --trick/--type/
# --feature: иначе один из мотивов появления этого скрипта (точечная проверка
# одного документа пропускает именно такие классы) остаётся непокрытым самой
# подвыборкой.
_BOUNDARY_DOC_IDS = [
    "loan_0006__m2718_homoglyph",       # Stage4-B: PHONE не детектируется под гомоглифом
    "services_0002__m1337_linebreak",   # Stage4-B: то же
    "works_0009",                       # PER-B: косвенный падеж рвётся двумя спанами
    "sale_0006",                        # PER-B: то же
]


def _base_two_per_type(gold):
    """Те же 18 документов, что tests/test_corpus_no_crash.py::_fast_doc_ids
    (без учёта _PREVIOUSLY_CRASHED — те не относятся к подвыборке метрик)."""
    seen = {}
    picked = []
    for d in gold:
        if d["source"] != "base":
            continue
        ct = d["contract_type"]
        n = seen.get(ct, 0)
        if n < 2:
            picked.append(d["doc_id"])
            seen[ct] = n + 1
    return picked


def _matches_trick(d, tricks):
    return any(e.get("trick") in tricks for e in d["entities"])


def _matches_type(d, types):
    return any(e["type"] in types for e in d["entities"])


def _matches_feature(d, features):
    return bool(set(d.get("features") or []) & set(features))


def select_doc_ids(gold, tricks=None, types=None, features=None):
    by_id = {d["doc_id"]: d for d in gold}
    picked = list(_base_two_per_type(gold))
    for doc_id in _BOUNDARY_DOC_IDS:
        if doc_id in by_id and doc_id not in picked:
            picked.append(doc_id)
    if tricks:
        tricks = set(tricks)
        for d in gold:
            if d["doc_id"] not in picked and _matches_trick(d, tricks):
                picked.append(d["doc_id"])
    if types:
        types = set(types)
        for d in gold:
            if d["doc_id"] not in picked and _matches_type(d, types):
                picked.append(d["doc_id"])
    if features:
        features = set(features)
        for d in gold:
            if d["doc_id"] not in picked and _matches_feature(d, features):
                picked.append(d["doc_id"])
    return sorted(set(picked))


def print_report(agg, n_docs):
    header = "%-10s %6s | %10s | %12s | %12s | %8s" % (
        "тип", "n", "recall", "leak_v2>=6", "leak_v2>=8", "FP-негат")
    print(header)
    print("-" * len(header))
    for t in ML.ALL_ENTITY_TYPES:
        s = agg["per_type"][t]
        n = s["n"]
        recall = (100.0 * s["found"] / n) if n else 0.0
        l6 = (100.0 * s["leak_v2_6"] / n) if n else 0.0
        l8 = (100.0 * s["leak_v2_8"] / n) if n else 0.0
        fp = agg["fp_on_neg"].get(t, 0)
        print("%-10s %6d | %9.1f%% | %11.1f%% | %11.1f%% | %8d" % (t, n, recall, l6, l8, fp))
    print("-" * len(header))
    bt = agg["total_bik_excl"]
    n = bt["n"]
    recall = (100.0 * bt["found"] / n) if n else 0.0
    l6 = (100.0 * bt["leak_v2_6"] / n) if n else 0.0
    l8 = (100.0 * bt["leak_v2_8"] / n) if n else 0.0
    print("%-10s %6d | %9.1f%% | %11.1f%% | %11.1f%% | %8d" % (
        "TOTAL*", n, recall, l6, l8, agg["fp_on_neg_total"]))
    print("* TOTAL — агрегат BIK-excl (как в gate.py). Крешей: %d/%d документов."
          % (len(agg["crashed"]), n_docs))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trick", action="append", default=[],
                     help="включить все документы с этим entities[].trick (можно несколько раз)")
    ap.add_argument("--type", action="append", default=[],
                     help="включить все документы с сущностью этого типа (PER/INN/...)")
    ap.add_argument("--feature", action="append", default=[],
                     help="включить все документы с этим features[] (см. gold.json)")
    args = ap.parse_args()

    print(_WARNING)
    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    doc_ids = select_doc_ids(gold, tricks=args.trick, types=args.type, features=args.feature)
    subset = [by_id[i] for i in doc_ids]

    print(f"Подвыборка: {len(subset)}/{len(gold)} документов корпуса.")
    if args.trick or args.type or args.feature:
        print(f"  фильтры: trick={args.trick} type={args.type} feature={args.feature}")

    results = RM.run_all(subset, verbose=True)
    agg = ML.aggregate_results(results)
    print()
    print_report(agg, len(subset))
    print(_WARNING)


if __name__ == "__main__":
    main()
