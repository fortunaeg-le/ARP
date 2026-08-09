# -*- coding: utf-8 -*-
"""
R-GUARANTEE, шаг 2: ЧЕСТНАЯ калибровка границы утечки и проверка на отложенной
части корпуса v1.

ЧТО ЗДЕСЬ ГЛАВНОЕ — РАЗДЕЛЕНИЕ. Корпус v1 содержит порождающие документы и их
МУТАЦИИ (doc_id вида base__m1337_combo). Мутация — тот же текст с теми же
значениями. Если резать пополам по документам, мутация базового документа
попадёт в отложенную часть и граница окажется подсмотренной. Поэтому режем по
СЕМЬЕ (базовому документу), семья целиком уходит в одну часть.

Порядок ровно такой и другого не было: сначала фиксируется seed и режется
корпус, потом ВСЁ (выбор lambda, квантиль, размер калибровки) считается ТОЛЬКО
на калибровочной части, и лишь в самом конце один раз читается отложенная.

Единица риска — СУЩНОСТЬ; риск документа R = доля утёкших сущностей.
"""
import os
import json
import math
import random

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, "results_lambda.json")
OUT = os.path.join(HERE, "calibration.json")

SEED = 20260809          # зафиксирован ДО просмотра любых цифр
BETA = 0.05              # хотим покрытие 95 % документов
DELTA = 0.05             # уверенность в границе среднего риска (Хёфдинг)
COST_LEAK = 20.0         # асимметрия потерь: пропуск ПДн дороже лишней маски
COST_OVERMASK = 1.0


def family(doc_id):
    return doc_id.split("__")[0]


def load():
    raw = json.load(open(IN, encoding="utf-8"))
    docs = [d for d in raw["docs"] if "crashed" not in d]
    return raw["lambdas"], docs


def risks(docs, lam):
    """R_i — доля утёкших ЭТАЛОННЫХ СУЩНОСТЕЙ документа при пороге lam."""
    out = []
    for d in docs:
        b = d["by_lambda"][str(lam)]
        n = b["n_entities"]
        out.append(b["leaked_v2"] / n if n else 0.0)
    return out


def loss(docs, lam):
    """Асимметричная потеря документа: 20 x утёкшая сущность + 1 x лишняя маска."""
    out = []
    for d in docs:
        b = d["by_lambda"][str(lam)]
        out.append(COST_LEAK * b["leaked_v2"] + COST_OVERMASK * b["overmask"])
    return out


def conformal_upper(vals, beta):
    """Раздельно-конформная верхняя граница риска НОВОГО документа на уровне
    1-beta: k-я порядковая статистика, k = ceil((n+1)(1-beta)). k > n -> границы
    нет (данных не хватает даже на 1-beta), возвращаем 1.0 = «не гарантируем»."""
    n = len(vals)
    k = math.ceil((n + 1) * (1 - beta))
    if k > n:
        return 1.0, None
    return sorted(vals)[k - 1], k


def hoeffding_ucb(vals, delta):
    """Верхняя доверительная граница СРЕДНЕГО риска (RCPS, Хёфдинг; R_i in [0,1])."""
    n = len(vals)
    return min(1.0, sum(vals) / n + math.sqrt(math.log(1 / delta) / (2 * n)))


def main():
    lambdas, docs = load()

    # ---------- 1. РАЗРЕЗ (до любых цифр) ----------
    fams = sorted({family(d["doc_id"]) for d in docs})
    rnd = random.Random(SEED)
    rnd.shuffle(fams)
    cut = len(fams) // 2
    cal_f, hold_f = set(fams[:cut]), set(fams[cut:])
    cal = [d for d in docs if family(d["doc_id"]) in cal_f]
    hold = [d for d in docs if family(d["doc_id"]) in hold_f]

    rep = {"seed": SEED, "beta": BETA, "delta": DELTA,
           "n_families": len(fams), "n_cal_docs": len(cal),
           "n_hold_docs": len(hold), "n_cal_families": len(cal_f),
           "n_hold_families": len(hold_f), "by_lambda": {}}

    # ---------- 2. КАЛИБРОВКА (только cal) ----------
    for lam in lambdas:
        rc = risks(cal, lam)
        lc = loss(cal, lam)
        bound, k = conformal_upper(rc, BETA)
        rep["by_lambda"][str(lam)] = {
            "cal_mean_risk": sum(rc) / len(rc),
            "cal_conformal_bound": bound,
            "cal_order_stat_k": k,
            "cal_hoeffding_ucb_mean": hoeffding_ucb(rc, DELTA),
            "cal_mean_loss": sum(lc) / len(lc),
            "cal_zero_leak_docs": sum(1 for r in rc if r == 0) / len(rc),
        }

    # выбор lambda: минимум средней асимметричной потери НА КАЛИБРОВКЕ
    lam_star = min(lambdas,
                   key=lambda L: rep["by_lambda"][str(L)]["cal_mean_loss"])
    rep["lambda_star"] = lam_star
    alpha = rep["by_lambda"][str(lam_star)]["cal_conformal_bound"]
    rep["alpha_claimed"] = alpha

    # ---------- 3. РАЗМЕР КАЛИБРОВКИ (по калибровочной части, подвыборками) ----------
    curve = []
    rc_all = risks(cal, lam_star)
    fam_of = [family(d["doc_id"]) for d in cal]
    for m in (10, 20, 30, 40, 60, 80, len(cal_f)):
        m = min(m, len(cal_f))
        vals_b, viol_b = [], []
        for rep_i in range(40):
            r2 = random.Random(SEED + rep_i)
            sub = set(r2.sample(sorted(cal_f), m))
            v = [x for x, f in zip(rc_all, fam_of) if f in sub]
            rest = [x for x, f in zip(rc_all, fam_of) if f not in sub]
            b, _ = conformal_upper(v, BETA)
            vals_b.append(b)
            if rest:
                viol_b.append(sum(1 for x in rest if x > b) / len(rest))
        curve.append({"n_cal_families": m, "n_cal_docs": len(v),
                      "bound_mean": sum(vals_b) / len(vals_b),
                      "bound_max": max(vals_b),
                      "inner_violation_mean": (sum(viol_b) / len(viol_b))
                      if viol_b else None})
    rep["calibration_size_curve"] = curve

    # ---------- 4. СМЕНА РАСПРЕДЕЛЕНИЯ (внутри калибровочной части) ----------
    # калибруемся на одном жанре договора, меряем на другом — БЕЗ отложенной части
    genres = sorted({d["contract_type"] for d in cal if d["contract_type"]})
    shift = []
    for g in genres:
        ing = [d for d in cal if d["contract_type"] == g]
        outg = [d for d in cal if d["contract_type"] != g]
        if len(ing) < 20 or not outg:
            continue
        b, _ = conformal_upper(risks(ing, lam_star), BETA)
        rv = risks(outg, lam_star)
        shift.append({"calibrated_on": g, "n": len(ing), "bound": b,
                      "violation_rate_elsewhere":
                          sum(1 for x in rv if x > b) / len(rv)})
    rep["distribution_shift_within_cal"] = shift

    # ---------- 5. ОТЛОЖЕННАЯ ЧАСТЬ — ЧИТАЕТСЯ ОДИН РАЗ, В САМОМ КОНЦЕ ----------
    rh = risks(hold, lam_star)
    rep["holdout"] = {
        "lambda": lam_star,
        "alpha_claimed": alpha,
        "n_docs": len(rh),
        "mean_risk": sum(rh) / len(rh),
        "violation_rate": sum(1 for x in rh if x > alpha) / len(rh),
        "target_violation_rate": BETA,
        "holds": (sum(1 for x in rh if x > alpha) / len(rh)) <= BETA,
        "max_risk": max(rh),
        "hoeffding_ucb_mean_on_cal":
            rep["by_lambda"][str(lam_star)]["cal_hoeffding_ucb_mean"],
        "mean_risk_vs_ucb_holds":
            (sum(rh) / len(rh)) <=
            rep["by_lambda"][str(lam_star)]["cal_hoeffding_ucb_mean"],
    }

    # по типам на отложенной (для отчёта: где именно граница держится хуже)
    tb = {}
    for d in hold:
        for t, (n, lk) in d["by_lambda"][str(lam_star)]["leaked_by_type"].items():
            a = tb.setdefault(t, [0, 0])
            a[0] += n
            a[1] += lk
    rep["holdout_by_type"] = {t: {"entities": n, "leaked": l,
                                  "leak_rate": l / n if n else 0.0}
                              for t, (n, l) in sorted(tb.items())}

    # ---------- 6. ЕДИНИЦА ОБМЕНИВАЕМОСТИ: не документ, а СЕМЬЯ ----------
    # Документы одной семьи — один и тот же текст под мутациями, их риски
    # зависимы. Конформная квантиль по 162 ЗАВИСИМЫМ значениям заявляет точность,
    # которой нет. Пересчитываем, взяв за единицу семью (риск семьи = максимум
    # по её документам — консервативно: гарантия на ЛЮБУЮ версию документа).
    def fam_risks(dset, lam):
        agg = {}
        for d, r in zip(dset, risks(dset, lam)):
            f = family(d["doc_id"])
            agg[f] = max(agg.get(f, 0.0), r)
        return [agg[f] for f in sorted(agg)]

    fr_cal = fam_risks(cal, lam_star)
    fr_hold = fam_risks(hold, lam_star)
    b_fam, k_fam = conformal_upper(fr_cal, BETA)
    rep["family_level"] = {
        "n_cal_families": len(fr_cal), "n_hold_families": len(fr_hold),
        "cal_mean_risk": sum(fr_cal) / len(fr_cal),
        "conformal_bound": b_fam, "order_stat_k": k_fam,
        "holdout_violation_rate":
            sum(1 for x in fr_hold if x > b_fam) / len(fr_hold),
        "holdout_max_risk": max(fr_hold),
        "holds": (sum(1 for x in fr_hold if x > b_fam) / len(fr_hold)) <= BETA,
    }

    # ---------- 7. ТОЛЕРАНТНАЯ ГРАНИЦА (content 1-beta, confidence 1-delta) ----
    # X_(k) — верхняя толерантная граница, если P(Bin(n, 1-beta) >= k) <= delta.
    def binom_tail_ge(n, p, k):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                   for i in range(k, n + 1))

    def tolerance_k(n, beta, delta):
        for k in range(n, 0, -1):
            if binom_tail_ge(n, 1 - beta, k) <= delta:
                return k
        return None

    def min_n(beta, delta):
        n = 1
        while n < 5000:
            if tolerance_k(n, beta, delta) is not None:
                return n
            n += 1
        return None

    tol = {}
    for (bt, dl) in ((0.05, 0.05), (0.10, 0.05), (0.10, 0.10), (0.20, 0.05)):
        n = len(fr_cal)
        k = tolerance_k(n, bt, dl)
        tol["%g/%g" % (1 - bt, 1 - dl)] = {
            "k_needed": k,
            "n_available": n,
            "bound": sorted(fr_cal)[k - 1] if k else None,
            "min_n_for_this_claim": min_n(bt, dl),
        }
    rep["tolerance_bounds_family_level"] = tol

    json.dump(rep, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
