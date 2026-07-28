# -*- coding: utf-8 -*-
"""
gate.py — ЕДИНЫЙ регресс-гейт SHIFRATOR (этап 0d, расширен этапом S2).

Один прогон корпуса — четыре охраняемые линии + креши + MANIFEST. До этапа S2
линий было две штуки в двух файлах: `gate.py` (leak/FP/masking, база
`results_baseline.json`) и `experiments/stage_d/gate_d.py` (precision/долг, база
`baseline_d.json`) — полная картина существовала только в голове человека,
запустившего оба, а `gate_d` можно было запустить по устаревшему дампу
(ARCHITECTURE_AUDIT §4.7, §5.8). Теперь база ОДНА (`results_baseline.json` —
полный дамп прогона, из него считаются ВСЕ метрики) и прогон ОДИН.

ЧЕТЫРЕ ЛИНИИ (любая красная -> exit 1):

  (а) PRECISION NER-хребта (ORG/PER/ADDRESS) не упал ниже baseline − допуск.
      FP = kept-маска на ОБЪЯВЛЕННОМ негативе, TP = kept-маска на gold своего
      типа (единая дефиниция этапа D, measure_lib.precision_by_type). Ловит
      «стали маскировать лишнее» — то, к чему слепа masking C.
  (б) LEAK_V2 не вырос — по каждому ИЗ 14 типов отдельно И по агрегату
      BIK-excl, оба порога (>=6 и >=8). Ловит «стали хуже прятать ПДн».
  (в) MASKING A (round-trip) не упала И равна 100%. Инвариант: замаскированное
      значение обязано восстанавливаться байт-в-байт.
  (г) MASKING B (границы: эталон закрыт масками целиком) не упала.
      C (тип маски) — МЯГКИЙ уровень по решению владельца: печатается,
      падение даёт ПРЕДУПРЕЖДЕНИЕ, гейт НЕ роняет (gate_config.MASKING_C_WARN_PP).

Линии «в»/«г» проверяются И ПО АГРЕГАТУ, И ПО КАЖДОМУ ТИПУ МАСКИ ОТДЕЛЬНО.
Причина — находка этапа S2: на дельте 2b -> HEAD агрегат B вырос 79.59% ->
86.79%, а ADDRESS-B при этом ОБВАЛИЛСЯ 75.33% -> 51.39% — агрегат вырос лишь
потому, что у ADDRESS упало число масок, то есть его вес в знаменателе.
Обвал целого типа спрятался за ростом среднего. Правило «по каждому типу
отдельно», с самого начала действовавшее для leak_v2, распространено на
masking.

ПЛЮС, независимыми условиями:
  1. КРЕШИ. outcome != "processed". Порог 0.
  3. FP ВСЕГО на размеченных негативах — не вырос сверх gate_config.FP_TOLERANCE.
  4. КОРПУС. sha256sum -c MANIFEST.sha256 — OK до И после прогона.
  5. ИЗВЕСТНЫЙ ДОЛГ (docs/known_leaks_stage_c.json): ADDRESS found=False & leak.
     Порог мягкий (решение владельца, этап D), компенсатор — ДИАГНОСТИКА
     СОСТАВА: счёт тот же, но множество сменилось -> ПРЕДУПРЕЖДЕНИЕ, чтобы
     подмена «N на другой N» была видна, а не растворялась в допуске.

Улучшения (leak/FP упали, masking/precision выросли) гейт НЕ роняют — печатаются.

Допуски — в gate_config.py, каждый с обоснованием. Все нулевые: разброс между
повторными прогонами на неизменном src/ ИЗМЕРЕН и равен нулю (этап E″ +
проверка этапа S2), значит допуск не компенсировал бы шум, а прощал бы регресс.

Запуск (минуты, полный корпус encrypt+decrypt по 324 документам — НЕ вешать
на pre-commit, место этого гейта — CI на PR, трогающем src/):
    venv/Scripts/python.exe tests/corpus/gate.py
    venv/Scripts/python.exe tests/corpus/gate.py --results tests/corpus/results_head_s2.json
      (второй вид — оценить УЖЕ СДЕЛАННЫЙ прогон, не гоняя корпус заново;
       файл прогона обязан быть свежим, гейт этого проверить не может)

Условие 1 (креши) — строгое НАДМНОЖЕСТВО того, что проверяет `pytest -m slow`
(tests/test_corpus_no_crash.py::test_full_corpus_encrypt_never_crashes). Если
gate.py уже запущен в этой сессии — отдельный `pytest -m slow` НЕ нужен, это
второй полный прогон Natasha ради уже покрытой проверки
(docs/archive/reports/EFFICIENCY_AUDIT.md §2.1).

Логика гейта (все четыре линии + креши + долг) не покрыта прогоном корпуса —
только собственными юнит-тестами, см. tests/corpus/test_gate_regression_detection.py
(быстрые, без Natasha): каждая линия краснеет от подложенного синтетического
регресса. Гейт, который никогда не краснел, непроверен.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
import gate_config as GC  # noqa: E402

MANIFEST = os.path.join(HERE, "MANIFEST.sha256")
BASELINE = os.path.join(HERE, "results_baseline.json")
CURRENT_DUMP = os.path.join(HERE, "results_gate_current.json")
KNOWN_LEAKS = os.path.join(ROOT, "docs", "known_leaks_stage_c.json")

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


# --------------------------------------------------------------------------- #
#   Линия «б» + креши + FP + masking A/B/C — историческое ядро (этап 0d)       #
# --------------------------------------------------------------------------- #
def compare(baseline_agg, current_agg, fp_tolerance,
            leak_tolerance=0, masking_a_pp=0.0, masking_b_pp=0.0,
            masking_c_pp=0.0):
    """Возвращает (rows, regressions, improvements) — БЕЗ warnings, историческая
    сигнатура сохранена (её зовёт test_gate_regression_detection). Полную оценку
    четырёх линий даёт evaluate(); эта функция закрывает креши, линию «б»
    (leak_v2), FP и линии «в»/«г» (masking A/B; C — мягкий, в warnings evaluate).

    rows — построчные данные для печати диф-таблицы (все 14 типов).
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
            if c[key] > b[key] + leak_tolerance:
                regressions.append(
                    "(б) %s[%s]: %d -> %d (+%d сущностей) — рост частичной утечки"
                    % (t, label, b[key], c[key], c[key] - b[key])
                )
            elif c[key] < b[key]:
                improvements.append(
                    "%s[%s]: %d -> %d (-%d)" % (t, label, b[key], c[key], b[key] - c[key])
                )

    bt, ct = baseline_agg["total_bik_excl"], current_agg["total_bik_excl"]
    for key, label in (("leak_v2_6", "leak_v2>=6"), ("leak_v2_8", "leak_v2>=8")):
        if ct[key] > bt[key] + leak_tolerance:
            regressions.append(
                "(б) TOTAL(BIK-excl)[%s]: %d -> %d (+%d сущностей) — рост частичной утечки"
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
            "(3) FP по негативам: %d -> %d (+%d), допуск gate_config.FP_TOLERANCE=+%d превышен"
            % (fp_b, fp_c, fp_delta, fp_tolerance)
        )
    elif fp_delta < 0:
        improvements.append("FP по негативам: %d -> %d (%d)" % (fp_b, fp_c, fp_delta))

    # --- линии «в»/«г»: masking_correctness (A жёстко, B жёстко, C мягко) ---
    mb = baseline_agg.get("masking_correctness", {}).get("total")
    mc = current_agg.get("masking_correctness", {}).get("total")
    if not mb or not mb["n_masks"]:
        # baseline снят до появления метрики (нет поля "masks") — сравнивать не с
        # чем. Молчать нельзя: иначе линии «в»/«г» «зелены» просто потому, что их
        # не с чем сопоставить.
        regressions.append(
            "masking_correctness: в baseline нет поля 'masks' — пересоберите "
            "results_baseline.json текущим run_measurement.py, иначе линии «в»/«г» слепы"
        )
    elif mc and mc["n_masks"]:
        (ab, bb, cb), (ac, bc_, cc) = ML.mc_rates(mb), ML.mc_rates(mc)
        for line, label, before, after, tol in (
            ("в", "A round-trip", ab, ac, masking_a_pp),
            ("г", "B границы", bb, bc_, masking_b_pp),
        ):
            if after < before - tol - 1e-9:
                regressions.append(
                    "(%s) masking_correctness[%s]: %.2f%% -> %.2f%% — корректность "
                    "маскировки УПАЛА (допуск %.2fпп)" % (line, label, before, after, tol)
                )
            elif after > before + 1e-9:
                improvements.append(
                    "masking_correctness[%s]: %.2f%% -> %.2f%%" % (label, before, after))
        # ИНВАРИАНТ линии «в»: A — это round-trip, он обязан быть РОВНО 100%.
        # Сравнения «не упала» мало: baseline с A<100 сделал бы порчу значения
        # неотличимой от нормы (см. docstring, линия «в»).
        if ac < 100.0 - 1e-9:
            regressions.append(
                "(в) masking_correctness[A round-trip] = %.2f%% < 100%% — "
                "значение НЕ восстанавливается байт-в-байт (инвариант проекта)" % ac
            )
        # C намеренно не роняет гейт — падение уходит в warnings у evaluate().

        # --- линии «в»/«г» ПО ТИПАМ МАСКИ, а не только по агрегату ---
        # ЗАЧЕМ (находка этапа S2). Агрегат B вырос 79.59% -> 86.79% на дельте
        # 2b -> HEAD, и это выглядело чистым улучшением. Разбор по типам показал,
        # что ОДНОВРЕМЕННО ADDRESS-B ОБВАЛИЛСЯ 75.33% -> 51.39%: агрегат вырос
        # потому, что у ADDRESS упало ЧИСЛО масок (5265 -> 1337), то есть упал его
        # вес в общем знаменателе, а PER/ORG подтянулись. Обвал целого типа
        # спрятался за ростом среднего — ровно тот же механизм, из-за которого
        # leak_v2 гейт с самого начала считает ПО КАЖДОМУ ТИПУ ОТДЕЛЬНО. Здесь то
        # же правило: падение границ одного типа не должно гаситься ростом другого.
        bt_ = baseline_agg.get("masking_correctness", {}).get("per_type", {})
        ct_ = current_agg.get("masking_correctness", {}).get("per_type", {})
        for t in ML.ALL_ENTITY_TYPES:
            sb, sc = bt_.get(t), ct_.get(t)
            if not sb or not sc or not sb["n_scored"] or not sc["n_scored"]:
                continue
            (a_b, b_b, _), (a_c, b_c2, _) = ML.mc_rates(sb), ML.mc_rates(sc)
            if a_c < a_b - masking_a_pp - 1e-9:
                regressions.append(
                    "(в) masking_correctness[A round-trip / %s]: %.2f%% -> %.2f%% — "
                    "round-trip УПАЛ на типе (допуск %.2fпп)" % (t, a_b, a_c, masking_a_pp))
            if b_c2 < b_b - masking_b_pp - 1e-9:
                regressions.append(
                    "(г) masking_correctness[B границы / %s]: %.2f%% -> %.2f%% — "
                    "границы масок типа УПАЛИ (допуск %.2fпп; агрегат мог вырасти "
                    "за счёт других типов)" % (t, b_b, b_c2, masking_b_pp))

    return rows, regressions, improvements


# --------------------------------------------------------------------------- #
#   Линия «а» (precision) + известный долг + диагностика состава (из этапа D)  #
# --------------------------------------------------------------------------- #
def missed_leak_ids(results):
    """Множество идентификаторов утечки-ДОЛГА: ADDRESS-сущность, НЕ детектированная
    (found=False) И утекающая (leak_v2!=none). Идентичность — (doc_id, value), та же,
    что в docs/known_leaks_stage_c.json (value == e['text']). Это ровно тот класс,
    что закрывается расширением мультиспана; found-but-partial-leak
    (датолайн-артефакт) СЮДА НЕ входит — он не про потерю детекции.
    Перенесено из experiments/stage_d/gate_d.py::_missed_leak_ids без изменения
    семантики (этап S2, сведение двух гейтов в один)."""
    ids = set()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if (e["type"] == "ADDRESS" and not e["found"]
                    and e["leak_v2"]["status"] != "none"):
                ids.add((r["doc_id"], e["text"]))
    return ids


def compare_precision(baseline_prec, current_prec, tol_pp):
    """Линия «а». Возвращает (regressions, improvements) по NER-хребту."""
    regressions, improvements = [], []
    for t in ML.NER_SPINE_TYPES:
        base = baseline_prec["per_type"].get(t, {}).get("precision")
        cur = current_prec["per_type"].get(t, {}).get("precision")
        if base is None or cur is None:
            continue
        if cur < base - tol_pp - 1e-9:
            regressions.append(
                "(а) precision[%s]: %.2f%% -> %.2f%% — упал ниже baseline − %.2fпп"
                % (t, base, cur, tol_pp))
        elif cur > base + 1e-9:
            improvements.append("precision[%s]: %.2f%% -> %.2f%%" % (t, base, cur))
    return regressions, improvements


def compare_debt(baseline_results, current_results, known_leaks_ids, tol):
    """Условие 5. Возвращает (regressions, warnings, composition)."""
    base_set = missed_leak_ids(baseline_results)
    cur_set = missed_leak_ids(current_results)
    regressions, warnings = [], []
    if len(cur_set) > len(base_set) + tol:
        regressions.append(
            "(5) утечка-долг ADDRESS (found=False & leak): %d -> %d, допуск "
            "gate_config.MISSED_LEAK_TOLERANCE=+%d превышен"
            % (len(base_set), len(cur_set), tol))
    left = base_set - cur_set          # ушли из долга (перестали течь — обычно хорошо)
    joined = cur_set - base_set        # НОВЫЕ утечки (не в baseline-множестве)
    composition = {
        "base_count": len(base_set), "cur_count": len(cur_set),
        "left": sorted(left), "joined": sorted(joined),
        "joined_documented": sorted(j for j in joined if j in known_leaks_ids),
        "joined_undocumented": sorted(j for j in joined if j not in known_leaks_ids),
        "registry_not_leaking": sorted(known_leaks_ids - cur_set),
        "cur_not_in_registry": sorted(cur_set - known_leaks_ids),
    }
    if not regressions and (left or joined):
        warnings.append(
            "(5) состав утечки-долга сменился при том же/непревышенном счёте: "
            "−%d [ушли] +%d [пришли] (из пришедших НЕ в known_leaks: %d) — "
            "подмена «N на другой N», проверить вручную"
            % (len(left), len(joined), len(composition["joined_undocumented"])))
    return regressions, warnings, composition


def evaluate(baseline_results, current_results, known_leaks_ids, cfg=GC):
    """ПОЛНАЯ оценка четырёх линий — чистая функция над двумя списками results.
    Ничего не печатает и не гоняет корпус: ровно эту функцию дёргает само-тест
    гейта (tests/corpus/test_gate_regression_detection.py), подкладывая
    синтетические регрессы в копию results.

    Возвращает dict:
      red          — bool, ронять ли прогон
      regressions  — список строк (каждая помечена линией «а»/«б»/«в»/«г» или условием)
      improvements — список строк
      warnings     — не роняющие сигналы (masking C, подмена состава долга)
      rows         — данные диф-таблицы по 14 типам
      composition  — состав утечки-долга
      base_agg/cur_agg, base_prec/cur_prec — сводки для печати
    """
    base_agg = ML.aggregate_results(baseline_results)
    cur_agg = ML.aggregate_results(current_results)
    base_prec = ML.precision_by_type(baseline_results)
    cur_prec = ML.precision_by_type(current_results)

    rows, regressions, improvements = compare(
        base_agg, cur_agg,
        fp_tolerance=cfg.FP_TOLERANCE,
        leak_tolerance=cfg.LEAK_V2_TOLERANCE,
        masking_a_pp=cfg.MASKING_A_TOLERANCE_PP,
        masking_b_pp=cfg.MASKING_B_TOLERANCE_PP,
    )
    warnings = []

    p_reg, p_imp = compare_precision(base_prec, cur_prec, cfg.PRECISION_TOLERANCE_PP)
    regressions += p_reg
    improvements += p_imp

    d_reg, d_warn, composition = compare_debt(
        baseline_results, current_results, known_leaks_ids, cfg.MISSED_LEAK_TOLERANCE)
    regressions += d_reg
    warnings += d_warn

    # masking C — мягкий уровень (решение владельца, STATE §6): предупреждение
    mb = base_agg.get("masking_correctness", {}).get("total")
    mc = cur_agg.get("masking_correctness", {}).get("total")
    if mb and mb["n_masks"] and mc and mc["n_masks"]:
        cb = ML.mc_rates(mb)[2]
        cc = ML.mc_rates(mc)[2]
        if cc < cb - cfg.MASKING_C_WARN_PP - 1e-9:
            warnings.append(
                "masking_correctness[C тип]: %.2f%% -> %.2f%% — упал. МЯГКИЙ "
                "уровень (решение владельца, STATE §6): гейт не роняю, но тип "
                "маски стал чаще расходиться с эталоном" % (cb, cc))

    return {
        "red": bool(regressions), "regressions": regressions,
        "improvements": improvements, "warnings": warnings,
        "rows": rows, "composition": composition,
        "base_agg": base_agg, "cur_agg": cur_agg,
        "base_prec": base_prec, "cur_prec": cur_prec,
    }


# --------------------------------------------------------------------------- #
#                                  ПЕЧАТЬ                                      #
# --------------------------------------------------------------------------- #
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
    print("* TOTAL — агрегат BIK-excl (см. BASELINE.md §1); FP-негат TOTAL — по ВСЕМ типам, условие 3 считает по этой строке.")
    print("  recall показан для контекста, регресс-условием НЕ является (см. docstring этого файла).")


def print_precision(base_prec, cur_prec, tol_pp):
    print("\n(а) PRECISION — FP = маска на ОБЪЯВЛЕННОМ негативе (единая ось этапа D)")
    print("  %-10s | %14s | %16s | %8s %8s" % (
        "тип", "precision б->т", "tp / fp_neg б->т", "cross", "nothing"))
    for t in ML.ALL_ENTITY_TYPES:
        db = base_prec["per_type"].get(t) or {}
        dc = cur_prec["per_type"].get(t) or {}
        if not dc:
            continue
        f = lambda d: ("%.2f%%" % d["precision"]) if d.get("precision") is not None else "n/a"
        star = " <NER-хребет (линия «а», допуск %.2fпп)" % tol_pp if t in ML.NER_SPINE_TYPES else ""
        print("  %-10s | %6s->%-6s | %4d->%-4d %4d->%-4d | %8d %8d%s" % (
            t, f(db), f(dc), db.get("tp", 0), dc.get("tp", 0),
            db.get("fp_neg", 0), dc.get("fp_neg", 0),
            dc.get("cross", 0), dc.get("nothing", 0), star))
    print("  over-mask прозы (on-nothing): %d -> %d   cross-type (домен masking C): %d -> %d"
          % (base_prec["nothing_total"], cur_prec["nothing_total"],
             base_prec["cross_total"], cur_prec["cross_total"]))
    print("  — cross/nothing ДИАГНОСТИКА, в precision и в гейт НЕ входят (этап D).")


def print_masking_correctness(baseline_agg, current_agg):
    """Отчёт по masking_correctness РЯДОМ с recall/leak. Знаменатели печатаются
    явно: у A это все маски, у B/C — маски, легшие хоть на одну эталонную
    сущность (маске-ложняку покрывать нечего). Их нельзя читать как проценты от
    gold — это другая метрика (см. measure_lib, блок masking_correctness)."""
    mb = baseline_agg.get("masking_correctness", {}).get("total") or ML._empty_mc()
    mc = current_agg.get("masking_correctness", {}).get("total") or ML._empty_mc()
    print("\n(в)/(г) masking_correctness — корректность ТОГО, ЧТО СИСТЕМА ЗАМАСКИРОВАЛА")
    print("  (знаменатель — маски системы, НЕ gold; A/B роняют гейт, C — мягкий уровень)")
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


def print_debt(composition, kl_count):
    c = composition
    print("\n(5) ИЗВЕСТНЫЙ ДОЛГ — ADDRESS found=False & leak_v2 (реестр docs/known_leaks_stage_c.json)")
    print("  долг: %d -> %d  (допуск +%d; реестр документирует %d)"
          % (c["base_count"], c["cur_count"], GC.MISSED_LEAK_TOLERANCE, kl_count))
    print("  из текущего долга НЕ документировано реестром: %d" % len(c["cur_not_in_registry"]))
    print("  из реестра больше НЕ течёт: %d" % len(c["registry_not_leaking"]))
    if c["left"] or c["joined"]:
        print("  СОСТАВ ИЗМЕНИЛСЯ: ушли %d, пришли %d (из пришедших не в реестре: %d)"
              % (len(c["left"]), len(c["joined"]), len(c["joined_undocumented"])))
        for j in c["joined_undocumented"][:10]:
            print("     +НОВАЯ (не в known_leaks): %s" % (j,))


def _known_leaks():
    kl = json.load(open(KNOWN_LEAKS, encoding="utf-8"))
    return set((r["doc_id"], r["value"]) for r in kl["leaks"]), len(kl["leaks"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    results_path = None
    if argv and argv[0] == "--results":
        results_path = argv[1]

    print("=== gate.py — ЕДИНЫЙ регресс-гейт (4 линии: precision / leak_v2 / masking A / masking B) ===\n")

    ok_before, bad_before, n_checked = check_manifest()
    print(f"MANIFEST.sha256 (до прогона): {n_checked} файлов — {'OK' if ok_before else 'FAIL'}")
    if not ok_before:
        for p in bad_before[:20]:
            print("  !!", p)

    if not os.path.isfile(BASELINE):
        print(f"\nНет baseline {BASELINE} — гейту не с чем сравнивать.")
        return 2

    baseline_results = json.load(open(BASELINE, encoding="utf-8"))

    if results_path:
        print(f"\nОценка ГОТОВОГО прогона: {results_path} (корпус не гоняется; "
              "свежесть файла гейт проверить не может — отвечает запускающий)")
        current_results = json.load(open(results_path, encoding="utf-8"))
    else:
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

    kl_ids, kl_count = _known_leaks()
    v = evaluate(baseline_results, current_results, kl_ids)

    print()
    print_report(v["rows"], v["base_agg"], v["cur_agg"])
    print_precision(v["base_prec"], v["cur_prec"], GC.PRECISION_TOLERANCE_PP)
    print_masking_correctness(v["base_agg"], v["cur_agg"])
    print_debt(v["composition"], kl_count)

    regressions = list(v["regressions"])
    if not (ok_before and ok_after):
        regressions.insert(0, "(4) MANIFEST.sha256 не совпал (см. вывод выше) — корпус изменился, замер недействителен")

    print()
    if v["improvements"]:
        print(f"Улучшения ({len(v['improvements'])}):")
        for m in v["improvements"]:
            print("  + " + m)
    if v["warnings"]:
        print(f"\nПРЕДУПРЕЖДЕНИЯ ({len(v['warnings'])}) — не роняют гейт:")
        for m in v["warnings"]:
            print("  ⚠ " + m)
    if regressions:
        print(f"\nРЕГРЕССЫ ({len(regressions)}) — ГЕЙТ КРАСНЫЙ:")
        for m in regressions:
            print("  - " + m)
        return 1

    print("\nГейт ЗЕЛЁНЫЙ: регрессов не найдено по всем четырём линиям "
          "(а precision / б leak_v2 / в masking A / г masking B) + креши/FP/MANIFEST/долг.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
