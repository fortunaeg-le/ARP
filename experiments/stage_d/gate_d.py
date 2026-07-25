# -*- coding: utf-8 -*-
"""ЭТАП D — ГЕЙТ precision + утечка-долг + FP. Капстоун precision-хребта.

╔══════════════════════════════════════════════════════════════════════════╗
║ УСТАРЕЛ (этап S2, 2026-07-25). ВСЁ, ЧТО ДЕЛАЕТ ЭТОТ ФАЙЛ, ПЕРЕНЕСЕНО В    ║
║ tests/corpus/gate.py — единый гейт с ОДНОЙ базой (results_baseline.json). ║
║ Запускать для проверки перед коммитом надо ЕГО, а не этот файл:           ║
║     venv/Scripts/python.exe tests/corpus/gate.py                          ║
║                                                                          ║
║ Почему сведено. (1) Гейтов было два, с двумя базами и непересекающимися   ║
║ критериями: gate.py не проверял precision, gate_d не проверял leak_v2 по  ║
║ типам и masking A/B — полная картина существовала только в голове того,   ║
║ кто запустил оба (ARCHITECTURE_AUDIT §4.7). (2) gate_d без аргумента      ║
║ читает ЗАКОММИЧЕННЫЙ results_d_head.json, поэтому «запустил gate_d,       ║
║ зелёный» было валидным описанием ситуации, в которой измерялся код        ║
║ недельной давности (§5.8). Единый гейт сам гоняет корпус, а чтение        ║
║ готового дампа переведено в явный флаг --results с оговоркой о свежести.  ║
║                                                                          ║
║ Файл ОСТАВЛЕН, не удалён: baseline_d.json/results_d_head.json — провенанс ║
║ цифр этапа D, на них ссылается HANDOFF_STAGE_D_PRECISION.md. Семантика    ║
║ _missed_leak_ids перенесена в gate.py::missed_leak_ids БЕЗ изменений.     ║
╚══════════════════════════════════════════════════════════════════════════╝

Замораживает достигнутый precision (ORG/PER/ADDRESS), чтобы будущая recall-работа
(Entity.spans, R) не могла его тихо уронить. НИЧЕГО не детектирует — только меряет
готовый прогон и сравнивает с baseline_d.json.

Гейт КРАСНЕЕТ, если ЛЮБОЕ:
  (2a) precision любого NER-типа (ORG/PER/ADDRESS) упал ниже baseline − допуск (0.5пп);
  (2b) FP всего (kept-маска на объявленном негативе, единая ось) > baseline + допуск;
  (2c) утечка-долг (ADDRESS found=False & leak_v2) > baseline + допуск.
ДИАГНОСТИКА СОСТАВА (НЕ блокирует): если утечка-счёт в пределах порога, НО состав
множества изменился (сущность ушла из долга И новая пришла) — ПРЕДУПРЕЖДЕНИЕ, чтобы
подмена «N на другой N» была видна. known_leaks_stage_c.json помечает, какие из утечек
документированы (долг этапа C), а какие — пред-существующие/новые.

Запуск:  venv/Scripts/python.exe experiments/stage_d/gate_d.py [results.json]
  без аргумента — берёт experiments/stage_d/results_d_head.json.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import measure_lib as ML  # noqa: E402

BASELINE = os.path.join(HERE, "baseline_d.json")
KNOWN_LEAKS = os.path.join(ROOT, "docs", "known_leaks_stage_c.json")


# --------------------------------------------------------------------------- #
#            Состояние прогона (что меряем) — чистая функция над results       #
# --------------------------------------------------------------------------- #
def _missed_leak_ids(results):
    """Множество идентификаторов утечки-ДОЛГА: ADDRESS-сущность, НЕ детектированная
    (found=False) И утекающая (leak_v2!=none). Идентичность — (doc_id, value), та же,
    что в known_leaks_stage_c.json (value == e['text']). Это ровно тот класс, что
    закрывается на Entity.spans; found-but-partial-leak (датолайн-артефакт) СЮДА НЕ
    входит — он не про потерю детекции."""
    ids = set()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if (e["type"] == "ADDRESS" and not e["found"]
                    and e["leak_v2"]["status"] != "none"):
                ids.add((r["doc_id"], e["text"]))
    return ids


def compute_state(results):
    prec = ML.precision_by_type(results)
    agg = ML.aggregate_results(results)
    leak_ids = _missed_leak_ids(results)
    return {
        "precision": {t: prec["per_type"][t]["precision"] for t in ML.NER_SPINE_TYPES},
        "precision_full": {t: prec["per_type"][t] for t in ML.ALL_ENTITY_TYPES},
        "fp_neg_total": agg["fp_on_neg_total"],
        "fp_neg_by_type": agg["fp_on_neg"],
        "missed_leak_count": len(leak_ids),
        "missed_leak_ids": sorted(leak_ids),
        "cross_total": prec["cross_total"],
        "nothing_total": prec["nothing_total"],
        "crashed": agg["crashed"],
    }


# --------------------------------------------------------------------------- #
#                       ЯДРО ГЕЙТА — чистая функция (для само-теста)           #
# --------------------------------------------------------------------------- #
def evaluate(state, baseline, known_leaks_ids):
    """Возвращает {red, reasons, warnings, composition}. state/baseline — dict'ы
    из compute_state / baseline_d.json. known_leaks_ids — set[(doc_id, value)]."""
    tol = baseline["tolerances"]
    reasons, warnings = [], []

    # (2a) precision NER не упал
    for t in ML.NER_SPINE_TYPES:
        cur = state["precision"].get(t)
        base = baseline["precision"].get(t)
        if cur is None or base is None:
            continue
        if cur < base - tol["precision_pp"]:
            reasons.append("2a precision[%s] %.2f%% < baseline %.2f%% − %.2fпп"
                           % (t, cur, base, tol["precision_pp"]))

    # (2b) FP всего не вырос
    if state["fp_neg_total"] > baseline["fp_neg_total"] + tol["fp_neg"]:
        reasons.append("2b FP-на-негативах %d > baseline %d + %d"
                       % (state["fp_neg_total"], baseline["fp_neg_total"], tol["fp_neg"]))

    # (2c) утечка-долг не выросла (мягкий порог по числу)
    if state["missed_leak_count"] > baseline["missed_leak_count"] + tol["missed_leak"]:
        reasons.append("2c утечка-долг %d > baseline %d + %d"
                       % (state["missed_leak_count"], baseline["missed_leak_count"],
                          tol["missed_leak"]))

    # ДИАГНОСТИКА СОСТАВА (компенсатор мягкого порога): в пределах порога, но состав сменился
    cur_set = set(tuple(x) for x in state["missed_leak_ids"])
    base_set = set(tuple(x) for x in baseline["missed_leak_ids"])
    left = base_set - cur_set          # ушли из долга (перестали течь — обычно хорошо)
    joined = cur_set - base_set        # НОВЫЕ утечки (не в baseline-множестве)
    composition = {"left": sorted(left), "joined": sorted(joined),
                   "joined_documented": sorted(j for j in joined if j in known_leaks_ids),
                   "joined_undocumented": sorted(j for j in joined if j not in known_leaks_ids)}
    if not reasons and (left or joined):
        warnings.append(
            "leak-set changed at same/within-threshold count: −%d [ушли] +%d [пришли] "
            "(из пришедших НЕ в known_leaks: %d) — подмена состава, проверить вручную"
            % (len(left), len(joined), len(composition["joined_undocumented"])))

    # креши — независимый красный (историческая линия проекта)
    if state["crashed"]:
        reasons.append("КРЕШИ: %d документов упали" % len(state["crashed"]))

    return {"red": bool(reasons), "reasons": reasons,
            "warnings": warnings, "composition": composition}


# --------------------------------------------------------------------------- #
#                                 CLI                                          #
# --------------------------------------------------------------------------- #
def _known_leaks_ids():
    kl = json.load(open(KNOWN_LEAKS, encoding="utf-8"))
    return set((r["doc_id"], r["value"]) for r in kl["leaks"]), kl["count"]


def print_report(state, baseline, verdict, kl_count):
    print("=" * 78)
    print("ГЕЙТ D — precision-хребет (baseline: %s)" % baseline.get("source", "?"))
    print("=" * 78)
    print("\n%-10s | %8s %8s | %7s | %6s %8s" % (
        "тип", "tp", "fp_neg", "precis", "cross", "nothing"))
    for t in ML.ALL_ENTITY_TYPES + ("SUM",):
        d = state["precision_full"].get(t)
        if not d:
            continue
        p = ("%.2f%%" % d["precision"]) if d["precision"] is not None else "n/a"
        star = " <NER-хребет" if t in ML.NER_SPINE_TYPES else ""
        print("%-10s | %8d %8d | %7s | %6d %8d%s" % (
            t, d["tp"], d["fp_neg"], p, d["cross"], d["nothing"], star))
    print("-" * 60)
    print("FP-на-негативах ВСЕГО: %d  (baseline %d, порог +%d)" % (
        state["fp_neg_total"], baseline["fp_neg_total"], baseline["tolerances"]["fp_neg"]))
    print("over-mask прозы (on-nothing, ДИАГНОСТИКА, не в гейт): %d" % state["nothing_total"])
    print("cross-type (ошибка типа, домен masking C, не в гейт): %d" % state["cross_total"])
    print("утечка-долг ADDRESS (found=False & leak): %d  (baseline %d, порог +%d; "
          "документировано в known_leaks: %d)" % (
              state["missed_leak_count"], baseline["missed_leak_count"],
              baseline["tolerances"]["missed_leak"], kl_count))
    print("\nprecision NER-хребет vs baseline (допуск %.2fпп):" % baseline["tolerances"]["precision_pp"])
    for t in ML.NER_SPINE_TYPES:
        print("  %-8s %.2f%%  (baseline %.2f%%)" % (
            t, state["precision"][t], baseline["precision"][t]))
    comp = verdict["composition"]
    if comp["joined"] or comp["left"]:
        print("\nСОСТАВ утечки изменился: ушли %d, пришли %d (из них не в долге: %d)" % (
            len(comp["left"]), len(comp["joined"]), len(comp["joined_undocumented"])))
        for j in comp["joined_undocumented"][:10]:
            print("   +НОВАЯ (не в known_leaks): %s" % (j,))
    print("\n" + "=" * 78)
    for w in verdict["warnings"]:
        print("⚠ ПРЕДУПРЕЖДЕНИЕ:", w)
    if verdict["red"]:
        print("ГЕЙТ D — КРАСНЫЙ:")
        for r in verdict["reasons"]:
            print("  ✗", r)
    else:
        print("ГЕЙТ D — ЗЕЛЁНЫЙ (precision не упал, FP не вырос, утечка-долг в пределах).")
    print("=" * 78)


def main():
    results_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "results_d_head.json")
    results = json.load(open(results_path, encoding="utf-8"))
    baseline = json.load(open(BASELINE, encoding="utf-8"))
    kl_ids, kl_count = _known_leaks_ids()
    state = compute_state(results)
    verdict = evaluate(state, baseline, kl_ids)
    print_report(state, baseline, verdict, kl_count)
    sys.exit(1 if verdict["red"] else 0)


if __name__ == "__main__":
    main()
