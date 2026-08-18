# -*- coding: utf-8 -*-
"""СВОДНЫЙ СРЕЗ по каждому типу сущностей: полнота, утечка, ложные, границы.
Два прогона одного и того же прибора:
  МАКСИМУМ      — все типы включены (так меряют гейт и STATE);
  ПО УМОЛЧАНИЮ  — набор type_policy.PERSONAL, который получает покупатель.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
import measure_lib as ML  # noqa: E402

MAXD = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")
DEFD = os.path.join(HERE, "results_default_profile.json")
out = open(os.path.join(HERE, "summary_by_type.txt"), "w", encoding="utf-8")


def say(*a):
    out.write(" ".join(str(x) for x in a) + "\n")


def load(p):
    return json.load(open(p, encoding="utf-8"))


def slice_of(results):
    agg = ML.aggregate_results(results)
    prec = ML.precision_by_type(results)
    bnd = ML.boundaries_by_type(results)
    mc = ML.mc_aggregate([m for r in results for m in r.get("masks", [])])
    return agg, prec, bnd, mc


def pct(a, b):
    return (100.0 * a / b) if b else float("nan")


def table(title, results, note):
    agg, prec, bnd, mc = slice_of(results)
    say("\n" + "=" * 118)
    say(title)
    say(note)
    say("=" * 118)
    say("%-12s %6s %8s %9s %9s %8s %8s %7s %7s %8s %8s %8s %8s" % (
        "тип", "эталон", "полнота", "утечк>=6", "утечк>=8", "точн%",
        "FPнег", "недоб", "перебор", "откр.см", "маскB%", "маскC%", "масок"))
    say("-" * 118)
    order = [t for t in ML.ALL_ENTITY_TYPES if agg["per_type"].get(t, {}).get("n")]
    tail = [t for t in ML.ALL_ENTITY_TYPES if t not in order]
    for t in order + tail:
        s = agg["per_type"].get(t, {"n": 0, "found": 0, "leak_v2_6": 0, "leak_v2_8": 0})
        p = prec["per_type"].get(t, {})
        b = bnd["per_type"].get(t, ML._empty_bnd())
        m = mc["per_type"].get(t, ML._empty_mc())
        _, mb, mc_ = ML.mc_rates(m)
        n = s["n"]
        pr = p.get("precision")
        say("%-12s %6d %7.1f%% %8.1f%% %8.1f%% %7s %8d %7d %7d %8d %7.1f%% %7.1f%% %8d" % (
            t, n,
            pct(s["found"], n) if n else float("nan"),
            pct(s["leak_v2_6"], n) if n else float("nan"),
            pct(s["leak_v2_8"], n) if n else float("nan"),
            ("%.2f" % pr) if pr is not None else "н/д",
            p.get("fp_neg", 0), b["shorter"], b["longer"], b["not_found_chars"],
            mb if m["n_scored"] else float("nan"),
            mc_ if m["n_scored"] else float("nan"),
            m["n_masks"]))
    say("-" * 118)
    tot = agg["total_all"]
    te = agg["total_bik_excl"]
    say("%-12s %6d %7.1f%% %8.1f%% %8.1f%%" % (
        "ВСЕГО", tot["n"], pct(tot["found"], tot["n"]),
        pct(tot["leak_v2_6"], tot["n"]), pct(tot["leak_v2_8"], tot["n"])))
    say("%-12s %6d %7.1f%% %8.1f%% %8.1f%%" % (
        "ВСЕГО BIK-", te["n"], pct(te["found"], te["n"]),
        pct(te["leak_v2_6"], te["n"]), pct(te["leak_v2_8"], te["n"])))
    a, mb, mc_ = ML.mc_rates(mc["total"])
    say("masking: A(round-trip) %.2f%%  B(границы) %.2f%%  C(тип) %.2f%%   "
        "масок %d, из них в знаменателе B/C %d"
        % (a, mb, mc_, mc["total"]["n_masks"], mc["total"]["n_scored"]))
    say("ложные на объявленном негативе, всего: %d ; на gold чужого типа (cross): %d ; "
        "на неразмеченной прозе (over-mask): %d"
        % (prec["fp_neg_total"], prec["cross_total"], prec["nothing_total"]))
    say("границы ВСЕГО: недобор %d сущн / %d симв ; перебор %d сущн / %d симв ; "
        "ровно по эталону %d ; без маски своего типа %d (открыто %d симв)"
        % (bnd["total"]["shorter"], bnd["total"]["under_chars"],
           bnd["total"]["longer"], bnd["total"]["over_chars"],
           bnd["total"]["exact"], bnd["total"]["not_found"],
           bnd["total"]["not_found_chars"]))
    return agg, prec, bnd, mc


say("СВОДНЫЙ СРЕЗ ПО ТИПАМ — сессия MEASURE-2, корпус v1 (324 документа)")
say("")
say("ЧЕСТНОСТЬ ЦИФР. Всё ниже измерено на ПОРОЖДЁННОМ корпусе: значения")
say("синтезированы генератором, разметка — его же побочный продукт. Это ВЕРХНЯЯ")
say("ГРАНИЦА качества, а не измерение работы на реальных договорах: ручной")
say("разметки настоящих договоров не существует, сверить не с чем. На реальном")
say("тексте формы записи разнообразнее, поэтому настоящие числа могут быть")
say("только ХУЖЕ приведённых.")
say("")
say("КОЛОНКИ: полнота = доля эталонных вхождений, накрытых маской СВОЕГО типа;")
say("утечка>=N = доля вхождений, чей фрагмент длиной >=N дожил до анонимного")
say("текста (leak_v2, продуктовый критерий); точн% = TP/(TP+FP на ОБЪЯВЛЕННОМ")
say("негативе); недоб/перебор = число сущностей, у которых маска короче/длиннее")
say("эталона; откр.см = символы сущностей БЕЗ маски своего типа; маскB/маскC —")
say("корректность того, что система замаскировала (знаменатель — маски).")

mx = table("СРЕЗ 1. НАБОР «МАКСИМУМ» (все типы включены) — так меряют гейт и docs/STATE.md",
           load(MAXD),
           "дамп: tests/corpus/results_gate_current.json (свежий прогон гейта этой сессии)")
df = table("СРЕЗ 2. НАБОР ПО УМОЛЧАНИЮ (type_policy.PERSONAL) — то, что получает покупатель",
           load(DEFD),
           "дамп: measurements/results_default_profile.json (прогон этой сессии)\n"
           "включённые типы: PERSON, ADDRESS, PHONE, EMAIL, PASSPORT, SNILS,\n"
           "BIRTHDATE, INN_PERSON, PASSPORT_ISSUER. Остальные типы масок не\n"
           "получают ПО ПРАВИЛУ — их «утечка» здесь не дефект, а работа настройки.")

# --- дельта по личным типам --------------------------------------------------
say("\n" + "=" * 118)
say("СРЕЗ 3. РАЗНИЦА «ПО УМОЛЧАНИЮ» ПРОТИВ «МАКСИМУМА» — только типы набора по умолчанию")
say("(по прочим типам разница ожидаема и равна «маски нет вовсе»)")
say("=" * 118)
PERSONAL_GOLD = sorted(ML.DEFAULT_PROFILE_GOLD_TYPES)
say("типы набора в терминах эталона: %s" % (PERSONAL_GOLD,))
say("%-12s %20s %22s %22s" % ("тип", "полнота макс->умолч", "утечка>=6 макс->умолч",
                              "утечка>=8 макс->умолч"))
say("-" * 118)
for t in PERSONAL_GOLD:
    a = mx[0]["per_type"].get(t)
    b = df[0]["per_type"].get(t)
    if not a or not a["n"]:
        continue
    n = a["n"]
    say("%-12s %9.1f%% ->%8.1f%% %10.1f%% ->%10.1f%% %10.1f%% ->%10.1f%%" % (
        t, pct(a["found"], n), pct(b["found"], n),
        pct(a["leak_v2_6"], n), pct(b["leak_v2_6"], n),
        pct(a["leak_v2_8"], n), pct(b["leak_v2_8"], n)))
say("-" * 118)
for lbl, key in (("полнота", "found"), ("утечка>=6", "leak_v2_6"), ("утечка>=8", "leak_v2_8")):
    na = sum(mx[0]["per_type"][t]["n"] for t in PERSONAL_GOLD if mx[0]["per_type"].get(t))
    va = sum(mx[0]["per_type"][t][key] for t in PERSONAL_GOLD if mx[0]["per_type"].get(t))
    vb = sum(df[0]["per_type"][t][key] for t in PERSONAL_GOLD if df[0]["per_type"].get(t))
    say("ИТОГО по личным типам, %-10s %6.2f%% -> %6.2f%%   (эталонных вхождений %d)"
        % (lbl, pct(va, na), pct(vb, na), na))

out.close()
print("ok")
