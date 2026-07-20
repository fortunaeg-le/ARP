# -*- coding: utf-8 -*-
"""
gate.py — регресс-гейт этапа 0d.

Прогоняет полный замер по замороженному корпусу (tests/corpus/run_measurement)
и сравнивает результат с results_baseline.json (точка отсчёта — коммит
2982f6f, этап 0b-fix). Печатает человекочитаемый диф и завершается кодом 1,
если стало хуже хоть по одному из условий:

  1. КРЕШИ. Появился документ, упавший с необработанным исключением
     (outcome != "processed"). Порог — 0.
  2. LEAK_V2 РОС. Частичная утечка (leak_v2, порог >=6 И порог >=8, ОБА)
     выросла — по корпусу в целом (агрегат BIK-excl, см. measure_lib.
     aggregate_results) ИЛИ по любому ИЗ 13 отдельных типов сущностей
     (включая BIK — агрегат его исключает по методологической причине,
     регресс именно в BIK-детекторе гейт всё равно обязан ловить). Порог
     допуска — 0, считается по каждому типу отдельно, чтобы рост одного типа
     не спрятался за падением другого.
  3. FP ВЫРОС. Ложные срабатывания на РАЗМЕЧЕННЫХ негативах (не на всей
     непомеченной прозе) выросли больше tests/corpus/gate_config.FP_TOLERANCE.
  4. КОРПУС ИЗМЕНИЛСЯ. sha256sum -c MANIFEST.sha256 не OK — до ИЛИ после
     прогона.
  5. MASKING_CORRECTNESS УПАЛА. Доля A (round-trip замаскированного значения)
     или B (эталонный спан закрыт масками ЦЕЛИКОМ) по агрегату стала НИЖЕ
     baseline. Знаменатель — МАСКИ системы, не gold (см. measure_lib, блок
     masking_correctness): условия 2-3 ловят «плохо ловим», условие 5 ловит
     «поймали, но спрятали криво» — этап, который начнёт маскировать криво ради
     recall, обязан покраснеть именно здесь. Допуск — 0 (корректность только
     растёт). C (совпадение типа маски с эталонным) печатается, но гейт НЕ
     роняет: это МЯГКИЙ уровень по решению владельца продукта.

Улучшения (leak_v2 упал, FP упал) гейт НЕ роняют — только печатаются.

Запуск (минуты, полный корпус encrypt+decrypt по 324 документам — НЕ вешать
на pre-commit, место этого гейта — CI на PR, трогающем src/):
    venv/Scripts/python.exe tests/corpus/gate.py

Условие 1 (креши) — строгое НАДМНОЖЕСТВО того, что проверяет
`pytest -m slow` (tests/test_corpus_no_crash.py::test_full_corpus_encrypt_
never_crashes: тот же полный корпус, та же проверка «0 крешей», без
leak_v2/FP/masking). Если gate.py уже будет запущен в этой сессии —
отдельный `pytest -m slow` НЕ нужен, это второй полный прогон Natasha ради
уже покрытой проверки (docs/archive/reports/EFFICIENCY_AUDIT.md §2.1). Оба
пути к крешу существуют для разных контекстов: gate.py — full-signature
проверка перед коммитом, `pytest -m slow` — если по какой-то причине
запускается ТОЛЬКО pytest-набор без gate.py. Гонять оба в одной сессии
избыточно, не исключайте ни один из них по отдельности.

Логика условия 1 (сравнение множеств crashed до/после) не покрыта прогоном
корпуса — только собственным юнит-тестом, см.
tests/corpus/test_gate_regression_detection.py (быстрый, без Natasha).
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
from gate_config import FP_TOLERANCE  # noqa: E402

MANIFEST = os.path.join(HERE, "MANIFEST.sha256")
BASELINE = os.path.join(HERE, "results_baseline.json")
CURRENT_DUMP = os.path.join(HERE, "results_gate_current.json")

EMPTY_STATS = {"n": 0, "found": 0, "leak_v1": 0, "leak_v2_6": 0, "leak_v2_8": 0}


def check_manifest():
    """Независимая от shell coreutils реализация `sha256sum -c MANIFEST.sha256`
    (нужна на Windows, где sha256sum не всегда есть в PATH). Возвращает
    (ok: bool, problems: list[str], n_checked: int)."""
    problems = []
    n = 0
    with open(MANIFEST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            digest, rel = line.split(None, 1)
            n += 1
            path = os.path.join(HERE, rel)
            if not os.path.isfile(path):
                problems.append(f"{rel}: файл отсутствует")
                continue
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                problems.append(f"{rel}: sha256 не совпадает с MANIFEST.sha256")
    return (not problems), problems, n


def _rate(hits, n):
    return (100.0 * hits / n) if n else 0.0


def _delta_mark(before, after):
    """'+' — стало лучше (утечка/FP упали), '-' — стало хуже (выросли), ' ' — не изменилось."""
    if after < before:
        return "+"
    if after > before:
        return "-"
    return " "


def compare(baseline_agg, current_agg, fp_tolerance):
    """Возвращает (rows, regressions, improvements).
    rows — построчные данные для печати диф-таблицы (все 13 типов).
    regressions/improvements — списки человекочитаемых строк."""
    regressions = []
    improvements = []

    if len(current_agg["crashed"]) > len(baseline_agg["crashed"]):
        new_crashes = sorted(set(current_agg["crashed"]) - set(baseline_agg["crashed"]))
        regressions.append(
            "КРЕШИ: %d документ(ов) упали с необработанным исключением (было %d): %s"
            % (len(current_agg["crashed"]), len(baseline_agg["crashed"]),
               ", ".join(new_crashes[:20]) + (" …" if len(new_crashes) > 20 else ""))
        )

    rows = []
    for t in ML.ALL_ENTITY_TYPES:
        b = baseline_agg["per_type"].get(t, dict(EMPTY_STATS))
        c = current_agg["per_type"].get(t, dict(EMPTY_STATS))
        rows.append((t, b, c))
        for key, label in (("leak_v2_6", "leak_v2>=6"), ("leak_v2_8", "leak_v2>=8")):
            if c[key] > b[key]:
                regressions.append(
                    "%s[%s]: %d -> %d (+%d сущностей) — рост частичной утечки"
                    % (t, label, b[key], c[key], c[key] - b[key])
                )
            elif c[key] < b[key]:
                improvements.append(
                    "%s[%s]: %d -> %d (-%d)" % (t, label, b[key], c[key], b[key] - c[key])
                )

    bt, ct = baseline_agg["total_bik_excl"], current_agg["total_bik_excl"]
    for key, label in (("leak_v2_6", "leak_v2>=6"), ("leak_v2_8", "leak_v2>=8")):
        if ct[key] > bt[key]:
            regressions.append(
                "TOTAL(BIK-excl)[%s]: %d -> %d (+%d сущностей) — рост частичной утечки"
                % (label, bt[key], ct[key], ct[key] - bt[key])
            )
        elif ct[key] < bt[key]:
            improvements.append(
                "TOTAL(BIK-excl)[%s]: %d -> %d (-%d)" % (label, bt[key], ct[key], bt[key] - ct[key])
            )

    fp_b = baseline_agg["fp_on_neg_total"]
    fp_c = current_agg["fp_on_neg_total"]
    fp_delta = fp_c - fp_b
    if fp_delta > fp_tolerance:
        regressions.append(
            "FP по негативам: %d -> %d (+%d), допуск gate_config.FP_TOLERANCE=+%d превышен"
            % (fp_b, fp_c, fp_delta, fp_tolerance)
        )
    elif fp_delta < 0:
        improvements.append("FP по негативам: %d -> %d (%d)" % (fp_b, fp_c, fp_delta))

    # --- условие 5: masking_correctness (A жёстко, B жёстко, C информационно) ---
    mb = baseline_agg.get("masking_correctness", {}).get("total")
    mc = current_agg.get("masking_correctness", {}).get("total")
    if not mb or not mb["n_masks"]:
        # baseline снят до появления метрики (нет поля "masks") — сравнивать не с
        # чем. Молчать нельзя: иначе условие 5 «зелено» просто потому, что его не
        # с чем сопоставить.
        regressions.append(
            "masking_correctness: в baseline нет поля 'masks' — пересоберите "
            "results_baseline.json текущим run_measurement.py, иначе условие 5 слепо"
        )
    elif mc and mc["n_masks"]:
        (ab, bb, cb), (ac, bc_, cc) = ML.mc_rates(mb), ML.mc_rates(mc)
        for label, before, after in (("A round-trip", ab, ac), ("B границы", bb, bc_)):
            if after < before - 1e-9:
                regressions.append(
                    "masking_correctness[%s]: %.2f%% -> %.2f%% — корректность "
                    "маскировки УПАЛА (допуск 0)" % (label, before, after)
                )
            elif after > before + 1e-9:
                improvements.append(
                    "masking_correctness[%s]: %.2f%% -> %.2f%%" % (label, before, after))
        # C намеренно не участвует ни в regressions, ни в improvements — он
        # печатается в отчёте (print_masking_correctness) и не влияет на код возврата.

    return rows, regressions, improvements


def print_report(rows, baseline_agg, current_agg):
    header = "%-10s %6s | %16s | %20s | %20s | %14s" % (
        "тип", "n", "recall б->т", "leak_v2>=6 б->т", "leak_v2>=8 б->т", "FP-негат б->т")
    print(header)
    print("-" * len(header))
    for t, b, c in rows:
        n = c["n"] if c["n"] else b["n"]
        rb, rc = _rate(b["found"], b["n"]), _rate(c["found"], c["n"])
        l6b, l6c = _rate(b["leak_v2_6"], b["n"]), _rate(c["leak_v2_6"], c["n"])
        l8b, l8c = _rate(b["leak_v2_8"], b["n"]), _rate(c["leak_v2_8"], c["n"])
        fpb = baseline_agg["fp_on_neg"].get(t, 0)
        fpc = current_agg["fp_on_neg"].get(t, 0)
        m6 = _delta_mark(b["leak_v2_6"], c["leak_v2_6"])
        m8 = _delta_mark(b["leak_v2_8"], c["leak_v2_8"])
        mfp = _delta_mark(fpb, fpc)
        print("%-10s %6d | %6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%5d->%-5d" % (
            t, n, rb, rc, m6, l6b, l6c, m8, l8b, l8c, mfp, fpb, fpc))

    print("-" * len(header))
    bt, ct = baseline_agg["total_bik_excl"], current_agg["total_bik_excl"]
    n = ct["n"] if ct["n"] else bt["n"]
    rb, rc = _rate(bt["found"], bt["n"]), _rate(ct["found"], ct["n"])
    l6b, l6c = _rate(bt["leak_v2_6"], bt["n"]), _rate(ct["leak_v2_6"], ct["n"])
    l8b, l8c = _rate(bt["leak_v2_8"], bt["n"]), _rate(ct["leak_v2_8"], ct["n"])
    m6 = _delta_mark(bt["leak_v2_6"], ct["leak_v2_6"])
    m8 = _delta_mark(bt["leak_v2_8"], ct["leak_v2_8"])
    fpb, fpc = baseline_agg["fp_on_neg_total"], current_agg["fp_on_neg_total"]
    mfp = _delta_mark(fpb, fpc)
    print("%-10s %6d | %6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%5d->%-5d" % (
        "TOTAL*", n, rb, rc, m6, l6b, l6c, m8, l8b, l8c, mfp, fpb, fpc))
    print("* TOTAL — агрегат BIK-excl (см. BASELINE.md §1); FP-негат TOTAL — по ВСЕМ типам, гейт условия 3 считает по этой строке.")
    print("  recall показан для контекста, регресс-условием НЕ является (см. docstring этого файла).")


def print_masking_correctness(baseline_agg, current_agg):
    """Отчёт по masking_correctness РЯДОМ с recall/leak. Знаменатели печатаются
    явно: у A это все маски, у B/C — маски, легшие хоть на одну эталонную
    сущность (маске-ложняку покрывать нечего). Их нельзя читать как проценты от
    gold — это другая метрика (см. measure_lib, блок masking_correctness)."""
    mb = baseline_agg.get("masking_correctness", {}).get("total") or ML._empty_mc()
    mc = current_agg.get("masking_correctness", {}).get("total") or ML._empty_mc()
    print("\nmasking_correctness — корректность ТОГО, ЧТО СИСТЕМА ЗАМАСКИРОВАЛА")
    print("  (знаменатель — маски системы, НЕ gold; A/B роняют гейт, C — информационно)")
    ab, bb, cb = ML.mc_rates(mb)
    ac, bc_, cc = ML.mc_rates(mc)
    print("  масок: %d -> %d   (из них легли на эталон: %d -> %d)"
          % (mb["n_masks"], mc["n_masks"], mb["n_scored"], mc["n_scored"]))
    for label, before, after, hard in (
        ("A round-trip (значение восстановлено байт-в-байт)", ab, ac, True),
        ("B границы  (эталон закрыт масками целиком)", bb, bc_, True),
        ("C тип      (тип маски == тип эталона)", cb, cc, False),
    ):
        mark = " " if abs(after - before) < 1e-9 else ("+" if after > before else "-")
        print("  %s %-52s %6.2f%% -> %6.2f%%%s"
              % (mark, label, before, after, "" if hard else "   [мягкий]"))
    ch_b = (100.0 * mb["a_channel_ok"] / mb["n_masks"]) if mb["n_masks"] else 100.0
    ch_c = (100.0 * mc["a_channel_ok"] / mc["n_masks"]) if mc["n_masks"] else 100.0
    print("    справочно A-канал (restored == plain на месте маски): "
          "%.2f%% -> %.2f%% — расхождение с A выше даёт схлопывание '\\n'->' ' "
          "в ячейке таблицы при сборке plain, само значение при этом цело." % (ch_b, ch_c))


def main():
    print("=== gate.py — регресс-гейт этапа 0d ===\n")

    ok_before, bad_before, n_checked = check_manifest()
    print(f"MANIFEST.sha256 (до прогона): {n_checked} файлов — {'OK' if ok_before else 'FAIL'}")
    if not ok_before:
        for p in bad_before[:20]:
            print("  !!", p)

    if not os.path.isfile(BASELINE):
        print(f"\nНет baseline {BASELINE} — гейту не с чем сравнивать.")
        return 2

    baseline_results = json.load(open(BASELINE, encoding="utf-8"))
    baseline_agg = ML.aggregate_results(baseline_results)

    gold = RM.load_gold()
    print(f"\nПрогон измерения: {len(gold)} документов (encrypt+decrypt, изолированное хранилище)…")
    current_results = RM.run_all(gold, verbose=True)

    # Отладочный снимок текущего прогона — НЕ results_baseline.json, точку
    # отсчёта гейт никогда не перезаписывает.
    json.dump(current_results, open(CURRENT_DUMP, "w", encoding="utf-8"), ensure_ascii=False)

    ok_after, bad_after, _ = check_manifest()
    print(f"\nMANIFEST.sha256 (после прогона): {'OK' if ok_after else 'FAIL'}")
    if not ok_after:
        for p in bad_after[:20]:
            print("  !!", p)

    current_agg = ML.aggregate_results(current_results)

    print()
    rows, regressions, improvements = compare(baseline_agg, current_agg, FP_TOLERANCE)
    print_report(rows, baseline_agg, current_agg)
    print_masking_correctness(baseline_agg, current_agg)

    manifest_ok = ok_before and ok_after
    if not manifest_ok:
        regressions.insert(0, "MANIFEST.sha256 не совпал (см. вывод выше) — корпус изменился, замер недействителен")

    print()
    if improvements:
        print(f"Улучшения ({len(improvements)}):")
        for m in improvements:
            print("  + " + m)
    if regressions:
        print(f"\nРЕГРЕССЫ ({len(regressions)}) — ГЕЙТ КРАСНЫЙ:")
        for m in regressions:
            print("  - " + m)
        return 1

    print("\nГейт ЗЕЛЁНЫЙ: регрессов не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
