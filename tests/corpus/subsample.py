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

Состав подвыборки — ЗАФИКСИРОВАННЫЙ СПИСОК tests/corpus/subset_iter.json
(33 документа; потолок рос 25 -> 28 -> 31 -> 33 вместе с новыми типами, история
в subset_lib.MAX_SUBSET_DOCS), читаемый через subset_lib. Состав БОЛЬШЕ НЕ ВЫЧИСЛЯЕТСЯ при
запуске: он покрывает 100% страт корпуса (все 191 класс «тип x категория x
trick / features / contract_type / parties / формат», все 14 структурных
конфигураций .docx и все 14 adversarial-мутаций по обоим маркерам), и
пересчёт при каждом запуске обесценил бы сравнение с замороженным
results_iter_baseline.json. Пересобирается только вручную —
tests/corpus/build_subset_iter.py.

Прежний состав («2 base-документа на contract_type» + 4 граничных, 22 док.)
покрывал 70.2% классов и 64% структурных конфигураций и не видел почти всю
мутационную матрицу реквизитов — docs/archive/reports/PERF_REPORT.md §4.2. Граничные
документы (Stage4-B, PER-B) в новом составе тоже есть: они включаются
принудительно, см. subset_lib.BOUNDARY_DOC_IDS.

Фильтры --trick/--type/--feature работают КАК ПРЕЖДЕ — расширяют состав
объединением (ВСЕ документы корпуса с этим trick/type/feature добавляются к
зафиксированному списку). Менялась только база объединения: раньше 22
вычисляемых документа, теперь 25 зафиксированных. Без фильтров — ровно 25.

Гоняет ТОТ ЖЕ харнесс (run_measurement.process_doc/run_all), что gate.py —
формат отчёта (recall/leak_v2/FP по типу) сопоставим построчно с
results_gate_current.json, НО НЕ по абсолютным числам (другой, меньший n) и
БЕЗ сравнения с results_baseline.json — только срез по текущей подвыборке.
Финальную приёмку/сравнение с baseline делает исключительно gate.py.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
import subset_lib as SL  # noqa: E402

_WARNING = (
    "\n" + "=" * 78 + "\n"
    "ПОДВЫБОРКА — ЧЕРНОВИК ДЛЯ ОТЛАДКИ.\n"
    "НЕ заменяет финальный полный прогон tests/corpus/gate.py перед коммитом.\n"
    "Регресс на редком типе, не попавшем в подвыборку, здесь может быть НЕ виден.\n"
    + "=" * 78 + "\n"
)

# Граничные документы (Stage4-B, PER-B) переехали в subset_lib.BOUNDARY_DOC_IDS
# и включены в зафиксированный состав subset_iter.json принудительно. Имя ниже
# оставлено как алиас: на него ссылаются docs/archive/reports/PERF_REPORT.md §4.1 и отчёты.
_BOUNDARY_DOC_IDS = SL.BOUNDARY_DOC_IDS


def _matches_trick(d, tricks):
    return any(e.get("trick") in tricks for e in d["entities"])


def _matches_type(d, types):
    return any(e["type"] in types for e in d["entities"])


def _matches_feature(d, features):
    return bool(set(d.get("features") or []) & set(features))


def select_doc_ids(gold, tricks=None, types=None, features=None):
    """База — ЗАФИКСИРОВАННЫЙ список subset_iter.json, а не вычисление.

    Раньше здесь считались «2 base-документа на contract_type» — 22 документа,
    70.2% классов (PERF_REPORT §4.2). Теперь состав читается из файла; логика
    расширения фильтрами --trick/--type/--feature не изменилась.
    """
    by_id = {d["doc_id"]: d for d in gold}
    picked = [i for i in SL.load_subset_ids() if i in by_id]
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


ITER_BASELINE = os.path.join(HERE, "results_iter_baseline.json")


def compare_with_iter_baseline(results, doc_ids, filtered):
    """Сверка среза с ЕГО СОБСТВЕННЫМ замороженным baseline.

    Сравнение идёт по ПЕРЕСЕЧЕНИЮ документов: с --trick/--type/--feature состав
    шире зафиксированного, и лишние документы просто не с чем сопоставлять —
    молча включить их в агрегат значило бы сравнить разные знаменатели, ровно
    ту ошибку, из-за которой срез нельзя сверять с results_baseline.json.

    Оценивает gate.evaluate — ТОТ ЖЕ код, что в полном гейте, чтобы «регресс на
    срезе» и «регресс в гейте» означали одно и то же. Но это НЕ приёмка:
    зелёный срез не заменяет полный прогон (см. баннер)."""
    if not os.path.isfile(ITER_BASELINE):
        print("\n[baseline среза] %s нет — сверять не с чем. "
              "Снять: venv/Scripts/python.exe tests/corpus/run_iter_baseline.py"
              % os.path.basename(ITER_BASELINE))
        return
    import gate as G  # импорт, не правка: gate.py в этом этапе не изменялся

    with open(ITER_BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    base_ids = {r["doc_id"] for r in base}
    cur_ids = {r["doc_id"] for r in results}
    common = base_ids & cur_ids
    if base_ids != cur_ids:
        print("\n[baseline среза] наборы документов различаются "
              "(baseline %d, прогон %d, общих %d) — сверяется пересечение."
              % (len(base_ids), len(cur_ids), len(common)))
        if base_ids - cur_ids:
            print("  ВНИМАНИЕ: baseline содержит документы, которых нет в прогоне: %d "
                  "— состав среза менялся без пересборки baseline?"
                  % len(base_ids - cur_ids))
    base_sub = [r for r in base if r["doc_id"] in common]
    cur_sub = [r for r in results if r["doc_id"] in common]

    kl_ids, _ = G._known_leaks()
    v = G.evaluate(base_sub, cur_sub, kl_ids)
    print("\n=== СРЕЗ против results_iter_baseline.json (%d док.) ===" % len(common))
    G.print_report(v["rows"], v["base_agg"], v["cur_agg"])
    G.print_precision(v["base_prec"], v["cur_prec"], 0.0)
    G.print_masking_correctness(v["base_agg"], v["cur_agg"])
    if v["improvements"]:
        print("\nУлучшения на срезе (%d):" % len(v["improvements"]))
        for m in v["improvements"]:
            print("  + " + m)
    if v["regressions"]:
        print("\nРЕГРЕССЫ НА СРЕЗЕ (%d) — это НАХОДКА, а не приёмка:" % len(v["regressions"]))
        for m in v["regressions"]:
            print("  - " + m)
    else:
        print("\nНа срезе регрессов нет. Это НЕ значит, что их нет в корпусе: "
              "срез видит все классы, но ~8-10% конкретных значений "
              "(docs/archive/reports/PERF_REPORT.md, «Что при этом теряется», п.1).")
    if filtered:
        print("  (прогон был с фильтрами: документы сверх зафиксированного "
              "состава в сверку не вошли)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trick", action="append", default=[],
                     help="включить все документы с этим entities[].trick (можно несколько раз)")
    ap.add_argument("--type", action="append", default=[],
                     help="включить все документы с сущностью этого типа (PER/INN/...)")
    ap.add_argument("--feature", action="append", default=[],
                     help="включить все документы с этим features[] (см. gold.json)")
    ap.add_argument("--no-compare", action="store_true",
                     help="не сверять с results_iter_baseline.json (только срез)")
    ap.add_argument("--workers", type=int, default=None,
                     help="ЭТАП SPEED: число процессов (умолчание — авто по ядрам/памяти, "
                          "1 — прежнее однопоточное поведение)")
    args = ap.parse_args()

    print(_WARNING)
    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    doc_ids = select_doc_ids(gold, tricks=args.trick, types=args.type, features=args.feature)
    subset = [by_id[i] for i in doc_ids]

    filtered = bool(args.trick or args.type or args.feature)
    print(f"Подвыборка: {len(subset)}/{len(gold)} документов корпуса "
          f"(зафиксированный состав subset_iter.json"
          f"{' + фильтры' if filtered else ''}).")
    if filtered:
        print(f"  фильтры: trick={args.trick} type={args.type} feature={args.feature}")

    results = RM.run_all(subset, verbose=True, workers=args.workers)
    agg = ML.aggregate_results(results)
    print()
    print_report(agg, len(subset))
    if not args.no_compare:
        compare_with_iter_baseline(results, doc_ids, filtered)
    print(_WARNING)


if __name__ == "__main__":
    main()
