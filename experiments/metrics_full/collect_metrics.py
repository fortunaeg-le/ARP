# -*- coding: utf-8 -*-
"""collect_metrics.py — СБОРКА отчёта METRICS-FULL из дампов прогона.

    venv/Scripts/python.exe experiments/metrics_full/collect_metrics.py

ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА. Ни одно число отчёта не пишется руками и не
переносится из журнала, из STATE или из памяти: всё считается ЗДЕСЬ, из дампов,
снятых `measure_all.py`. Строки, которые перенести всё-таки пришлось (потому что
пересчитать их нечем — например замер на реальном документе владельца, которого
на машине нет), собраны в ОДНОМ месте — словаре `CARRIED` — и печатаются с датой
снимка и пометкой «перенесено».

ЧТО ЧИТАЕТ:
  tests/corpus/results_gate_current.json   — прогон корпуса v1 этого этапа;
  tests/corpus/results_baseline.json       — точка отсчёта гейта (для линий);
  experiments/metrics_full/results_v2.json — прогон корпуса v2 этого этапа;
  experiments/metrics_full/results_profiles.json — четыре набора типов;
  experiments/metrics_full/runs.json       — хронометраж прогонов;
  реестры: known_single_guard.json, known_default_leaks.json,
           overmask_ledger.json, docs/known_leaks_stage_c.json;
  docs/FINDINGS.md, docs/STATE.md          — СЧЁТ строк реестра долгов и
           решений владельца (счёт, а не числа метрик).

ЧТО ПИШЕТ: METRICS_FULL.md (человеку) и metrics_full.json (машине, чтобы
следующий срез сравнивался автоматически, а не глазами).
"""
import json
import os
import platform
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS1 = os.path.join(ROOT, "tests", "corpus")
CORPUS2 = os.path.join(ROOT, "tests", "corpus_v2")
sys.path.insert(0, CORPUS1)
sys.path.insert(0, os.path.join(ROOT, "src"))

import measure_lib as ML       # noqa: E402
import gate as G               # noqa: E402
import gate_config as GC       # noqa: E402
import by_entity as BE         # noqa: E402

DUMP_V1 = os.path.join(CORPUS1, "results_gate_current.json")
BASELINE_V1 = os.path.join(CORPUS1, "results_baseline.json")
DUMP_V2 = os.path.join(HERE, "results_v2.json")
DUMP_PROF = os.path.join(HERE, "results_profiles.json")
RUNS = os.path.join(HERE, "runs.json")
OUT_MD = os.path.join(HERE, "METRICS_FULL.md")
OUT_JSON = os.path.join(HERE, "metrics_full.json")

#: ЕДИНСТВЕННОЕ место, где стоят числа, ПЕРЕНЕСЁННЫЕ с прошлых дат. Пересчитать
#: их этим этапом нечем, и рядом с каждым обязана стоять дата снимка.
CARRIED = {
    "колонка «реальный документ» (число масок по типам)": {
        "date": "2026-08-19 (этап SEAM-JOIN; сам замер — снимок DEFAULT-GATE)",
        "why": "реального договора владельца на этой машине нет (запрет 7 CLAUDE.md: "
               "реальные документы не коммитить), разметки у него нет вовсе — "
               "по нему считаются только КОЛИЧЕСТВА масок",
        "source": "docs/STATE.md §2.1 (колонка «реальный документ»)",
    },
    "§2.3 STATE — счёт по сущностям (для НАЗВАНИЯ ДЕЛЬТЫ, не для таблиц)": {
        "date": "снимок этапа DEFAULT-GATE (в docs/STATE.md отмечено: "
                "«by_entity.py этим этапом НЕ гонялся»)",
        "why": "прежний прогон не сохранён; пересчитать «как было» нечем — можно "
               "только назвать разницу с тем, что напечатано в STATE",
        "source": "docs/STATE.md §2.3",
        "values": {
            "direct_found_occ": 86.17, "direct_found_ent": 85.82,
            "direct_prot_occ": 79.41, "direct_prot_ent": 81.35,
            "indirect_found_occ": 94.33, "indirect_found_ent": 94.04,
            "indirect_prot_occ": 88.64, "indirect_prot_ent": 88.72,
            "total_found_occ": 89.31, "total_found_ent": 89.36,
            "total_prot_occ": 82.96, "total_prot_ent": 84.52,
            "per_surface": 2272, "per_by_surname": 1824,
            "per_strict_pct": 66.50, "per_product_pct": 82.35,
            "per_open_any": 611,
        },
    },
    "круг на собранной программе (54/54, размер поставки)": {
        "date": "2026-08-19 (этап SEAM-JOIN)",
        "why": "круг на собранной программе — Windows + PyInstaller + настоящий "
               "браузер; в этом рабочем дереве не воспроизводится",
        "source": "docs/STATE.md §5",
    },
}


# --------------------------------------------------------------------------- #
#                                 мелочи                                       #
# --------------------------------------------------------------------------- #
def pct(a, b, dash="—"):
    return dash if not b else 100.0 * a / b


def f2(x, dash="—"):
    return dash if x is None or isinstance(x, str) else "%.2f" % x


def i(x):
    return "%d" % x


def sh(cmd, cwd=ROOT):
    try:
        return subprocess.check_output(cmd, cwd=cwd, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "—"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
#                        1. ШАПКА ПРОГОНА                                      #
# --------------------------------------------------------------------------- #
def manifest_state(corpus_dir):
    """(ok, n_files) — sha256sum -c своими силами, без внешней утилиты."""
    import hashlib
    path = os.path.join(corpus_dir, "MANIFEST.sha256")
    n = bad = 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        digest, rel = line.split("  ", 1)
        n += 1
        p = os.path.join(corpus_dir, rel)
        if not os.path.exists(p):
            bad += 1
            continue
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if h != digest:
            bad += 1
    return (bad == 0), n, bad


def head_block(v1, v2, gold1, gold2, runs):
    n_ent1 = sum(len(d["entities"]) for d in gold1)
    n_neg1 = sum(len(d.get("negatives", [])) for d in gold1)
    n_ent2 = sum(len(d["entities"]) for d in gold2)
    n_neg2 = sum(len(d.get("negatives", [])) for d in gold2)
    ok1, nf1, bad1 = manifest_state(CORPUS1)
    ok2, nf2, bad2 = manifest_state(CORPUS2)
    rt1 = [r for r in v1 if r.get("outcome") == "processed"]
    rt2 = [r for r in v2 if r.get("outcome") == "processed"]
    return {
        "commit": sh(["git", "rev-parse", "HEAD"]),
        "commit_short": sh(["git", "rev-parse", "--short", "HEAD"]),
        "branch": sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": sh(["git", "status", "--porcelain"]) not in ("", "—"),
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": "%s / %s ядер" % (platform.machine(), os.cpu_count()),
        "runs": runs,
        "v1": {"docs": len(gold1), "gold_entities": n_ent1, "negatives": n_neg1,
               "manifest_ok": ok1, "manifest_files": nf1, "manifest_bad": bad1,
               "processed": len(rt1),
               "crashed": [r["doc_id"] for r in v1 if r.get("outcome") == "crashed"],
               "refused": [r["doc_id"] for r in v1 if r.get("outcome") == "refused"],
               "roundtrip_ok": sum(1 for r in rt1 if r["roundtrip_ok"]),
               "unresolved": sum(r.get("unresolved", 0) or 0 for r in rt1)},
        "v2": {"docs": len(gold2), "gold_entities": n_ent2, "negatives": n_neg2,
               "manifest_ok": ok2, "manifest_files": nf2, "manifest_bad": bad2,
               "processed": len(rt2),
               "crashed": [r["doc_id"] for r in v2 if r.get("outcome") == "crashed"],
               "refused": [r["doc_id"] for r in v2 if r.get("outcome") == "refused"],
               "roundtrip_ok": sum(1 for r in rt2 if r["roundtrip_ok"]),
               "unresolved": sum(r.get("unresolved", 0) or 0 for r in rt2)},
    }


# --------------------------------------------------------------------------- #
#                  2-3. АГРЕГАТЫ И ТАБЛИЦА ПО ТИПАМ                            #
# --------------------------------------------------------------------------- #
def leak_hits(corpus, etype, rec):
    """(утекло, утекло по СТРОГОМУ счёту) — РАЗНЫМИ приборами на разных корпусах,
    и это не небрежность, а устройство замера.

    v1: оконная метрика `leak_v2` с двумя порогами (>= 6 и >= 8 символов ядра) —
        ровно то, что судит линия «б» гейта.
    v2: ПОЗИЦИОННАЯ метрика `leak_pos` — сколько цифр эталонного спана не
        закрыто НИ ОДНОЙ маской; порогов у неё нет, «строгий» счёт — это доля
        `full` (значение открыто целиком). Оконную метрику на v2 брать НЕЛЬЗЯ:
        у фабричных типов (`DATE`, `REG_ID`, `REGISTRY_NAME`, `PASSPORT_ISSUER`,
        `CONTRACT_NO`…) её диспетчер не имеет ветви и КОНСЕРВАТИВНО ставит
        `full` каждому вхождению — то есть дал бы «утечка 100 %» у типа,
        закрытого маской целиком. Так этот отчёт и ошибся на первом прогоне.
    """
    if corpus == "v1":
        return ML._leak_v2_hits(etype, rec["leak_v2"])
    st = rec["leak_pos"]["status"]
    return st != "none", st == "full"


def leak_labels(corpus):
    if corpus == "v1":
        return ("утечка ≥6", "утечка ≥8", "оконная метрика leak_v2, пороги ≥6 и ≥8")
    return ("утечка (позиц.)", "из них full",
            "позиционная метрика leak_pos: цифры эталона, не закрытые ни одной маской")


def leak_by_type(results, corpus):
    """{тип: {n, found, leak, leak_strict}} — своим счётом, тем прибором,
    который на этом корпусе честен (см. leak_hits)."""
    out = defaultdict(Counter)
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            s = out[e["type"]]
            s["n"] += 1
            s["found"] += int(e["found"])
            a, b = leak_hits(corpus, e["type"], e)
            s["leak"] += int(a)
            s["leak_strict"] += int(b)
    return out


def per_type_block(results, corpus):
    """Полная строка по каждому типу: эталон, полнота, утечка, точность, fp,
    masking B/C, границы обеих сторон, разбор причины промаха."""
    agg = ML.aggregate_results(results)
    lk = leak_by_type(results, corpus)
    prec = ML.precision_by_type(results)
    bnd = ML.boundaries_by_type(results)
    mc = agg["masking_correctness"]

    miss = defaultdict(Counter)
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if e["found"]:
                continue
            miss[e["type"]][e["miss_reason"] or "nothing"] += 1

    types = sorted(set(list(ML.ALL_ENTITY_TYPES)
                       + [t for t, s in agg["per_type"].items() if s["n"]]
                       + [t for t, s in prec["per_type"].items()
                          if s["tp"] or s["fp_neg"] or s["nothing"] or s["cross"]]
                       + list(mc["per_type"])))
    rows = {}
    for t in types:
        a = agg["per_type"].get(t, ML._empty_type_stats())
        p = prec["per_type"].get(t, ML._empty_prec())
        b = bnd["per_type"].get(t, ML._empty_bnd())
        m = mc["per_type"].get(t, ML._empty_mc())
        den = p["tp"] + p["fp_neg"]
        lt = lk.get(t, Counter())
        rows[t] = {
            "n_gold": a["n"], "found": a["found"],
            "recall": pct(a["found"], a["n"], None),
            "leak6": lt["leak"], "leak8": lt["leak_strict"],
            "leak6_pct": pct(lt["leak"], a["n"], None),
            "leak8_pct": pct(lt["leak_strict"], a["n"], None),
            "precision": (100.0 * p["tp"] / den) if den else None,
            "tp": p["tp"], "fp_neg": p["fp_neg"],
            "cross": p["cross"], "nothing": p["nothing"],
            "n_masks": m["n_masks"], "n_scored": m["n_scored"],
            "masking_b": pct(m["b_ok"], m["n_scored"], None),
            "masking_c": pct(m["c_ok"], m["n_scored"], None),
            "under_n": b["shorter"], "under_ch": b["under_chars"],
            "over_n": b["longer"], "over_ch": b["over_chars"],
            "not_found_bnd": b["not_found"], "not_found_ch": b["not_found_chars"],
            "miss": dict(miss.get(t, {})),
        }
    return {"rows": rows, "agg": agg, "prec": prec, "bnd": bnd, "mc": mc,
            "leak": {t: dict(v) for t, v in lk.items()}, "corpus": corpus}


def product_block(results, blk):
    agg, prec, bnd, mc = blk["agg"], blk["prec"], blk["bnd"], blk["mc"]
    corpus = blk["corpus"]
    ta = agg["total_all"]
    a, b, c = ML.mc_rates(mc["total"])
    lk = blk["leak"]
    n_all = sum(v["n"] for v in lk.values())
    f_all = sum(v["found"] for v in lk.values())
    l_all = sum(v["leak"] for v in lk.values())
    s_all = sum(v["leak_strict"] for v in lk.values())
    n_bik = sum(v["n"] for t, v in lk.items() if t != "BIK")
    f_bik = sum(v["found"] for t, v in lk.items() if t != "BIK")
    l_bik = sum(v["leak"] for t, v in lk.items() if t != "BIK")
    s_bik = sum(v["leak_strict"] for t, v in lk.items() if t != "BIK")
    leaked_types = Counter({t: v["leak"] for t, v in lk.items() if v["leak"]})
    fp_by_type = {t: n for t, n in agg["fp_on_neg"].items() if n}
    return {
        "corpus": corpus, "leak_metric": leak_labels(corpus)[2],
        "gold_occurrences_all": n_all, "gold_occurrences_bik_excl": n_bik,
        "recall_all": pct(f_all, n_all, None),
        "recall_bik_excl": pct(f_bik, n_bik, None),
        "leak6_bik_excl": l_bik, "leak8_bik_excl": s_bik,
        "leak6_bik_excl_pct": pct(l_bik, n_bik, None),
        "leak8_bik_excl_pct": pct(s_bik, n_bik, None),
        "leak6_all": l_all, "leak8_all": s_all,
        "leaked_by_type": dict(leaked_types.most_common()),
        "masks_total": mc["total"]["n_masks"],
        "masks_on_gold": mc["total"]["n_scored"],
        "masks_on_nothing": prec["nothing_total"],
        "masks_cross": prec["cross_total"],
        "fp_on_negatives": agg["fp_on_neg_total"], "fp_by_type": fp_by_type,
        "masking_a": a, "masking_b": b, "masking_c": c,
        "under_n": bnd["total"]["shorter"], "under_ch": bnd["total"]["under_chars"],
        "over_n": bnd["total"]["longer"], "over_ch": bnd["total"]["over_chars"],
        "not_found_n": bnd["total"]["not_found"],
        "not_found_ch": bnd["total"]["not_found_chars"],
    }


# --------------------------------------------------------------------------- #
#             4-5. ПО СУЩНОСТЯМ И ПО ЛЮДЯМ (пересчёт §2.3 STATE)               #
# --------------------------------------------------------------------------- #
def by_entity_block(results):
    """Тот же счёт, что печатает tests/corpus/by_entity.py, но в структуре —
    считается ЗАНОВО этим прогоном (числа §2.3 STATE перенесены с DEFAULT-GATE)."""
    sm = BE.summarize(results)
    per = sm["per_type"]

    def roll(types):
        s = BE._roll(per, types)
        return {
            "occ": s["occ"], "ent": s["ent"], "multi": s["multi"],
            "occ_found_pct": pct(s["occ_found"], s["occ"], None),
            "ent_found_pct": pct(s["ent_masked"], s["ent"], None),
            "occ_prot_pct": pct(s["occ_prot"], s["occ"], None),
            "ent_prot_pct": pct(s["ent_prot"], s["ent"], None),
        }

    allt = sorted(per)
    out = {"direct": roll(BE.DIRECT), "indirect": roll(BE.INDIRECT),
           "total": roll(allt), "per_type": {}}
    for t in allt:
        s = per[t]
        if not s["ent"]:
            continue
        out["per_type"][t] = {
            "occ": s["occ"], "ent": s["ent"], "multi": s["multi"],
            "occ_found_pct": pct(s["occ_found"], s["occ"], None),
            "ent_found_pct": pct(s["ent_masked"], s["ent"], None),
            "occ_prot_pct": pct(s["occ_prot"], s["occ"], None),
            "ent_prot_pct": pct(s["ent_prot"], s["ent"], None),
        }
    for k in ("direct", "indirect", "total"):
        d = out[k]
        d["delta_found_pp"] = (d["ent_found_pct"] - d["occ_found_pct"])
        d["delta_prot_pp"] = (d["ent_prot_pct"] - d["occ_prot_pct"])
    return out


def persons_block(results):
    """ЛЮДИ. Строгий критерий = все вхождения закрыты маской СВОЕГО типа И
    ничего не утекло (`by_entity.protected`). Продуктовый = не утекло ничего
    (`leak_any` == False) — то, что реально уходит в LLM."""
    def key_fn(etype, text):
        return BE.person_key(text) if etype == "PER" else BE.value_key(etype, text)

    surface = [g for g in BE.group_entities(results) if g["type"] == "PER"]
    by_surname = [g for g in BE.group_entities(results, key_fn) if g["type"] == "PER"]

    def stat(groups):
        n = len(groups)
        strict = sum(1 for g in groups if g["protected"])
        product = sum(1 for g in groups if not g["leak_any"])
        masked_all = sum(1 for g in groups if g["masked_all"])
        return {"n": n, "strict": strict, "product": product,
                "masked_all": masked_all,
                "strict_pct": pct(strict, n, None),
                "product_pct": pct(product, n, None),
                "masked_all_pct": pct(masked_all, n, None),
                "open_any": n - strict,
                "leaking": n - product,
                "multi": sum(1 for g in groups if g["n_occ"] > 1)}

    return {"surface": stat(surface), "by_surname": stat(by_surname),
            "occurrences": sum(g["n_occ"] for g in surface)}


# --------------------------------------------------------------------------- #
#                       6. ЛИНИИ ГЕЙТА ПОИМЁННО                                #
# --------------------------------------------------------------------------- #
#: Что меряет каждая линия, каким допуском и какого уровня. Источник —
#: докстринг tests/corpus/gate.py и tests/corpus/gate_config.py; уровень
#: («жёсткая» = роняет прогон, «мягкая» = печатается предупреждением) взят
#: оттуда же и проверяется ниже по факту попадания строки в regressions/warnings.
LINES = [
    ("а", "PRECISION по каждому типу: TP на эталоне своего типа против FP на "
          "ОБЪЯВЛЕННОМ негативе", "PRECISION_TOLERANCE_PP", "жёсткая"),
    ("б", "LEAK_V2 — доля эталонных вхождений, от которых в анонимном тексте "
          "дожил фрагмент; по каждому типу и по агрегату BIK-excl, оба порога",
     "LEAK_V2_TOLERANCE", "жёсткая"),
    ("в", "MASKING A — round-trip: замаскированное значение восстанавливается "
          "байт-в-байт. Плюс АБСОЛЮТНОЕ требование 100 %",
     "MASKING_A_TOLERANCE_PP", "жёсткая"),
    ("г", "MASKING B — эталон закрыт масками ЦЕЛИКОМ (границы). C (тип маски) — "
          "мягкий уровень решением владельца",
     "MASKING_B_TOLERANCE_PP", "жёсткая (C — мягкая)"),
    ("д", "OVER-MASK ПРОЗЫ — маска, не легшая ни на эталон, ни на объявленный "
          "негатив: закрыт обычный текст", "OVERMASK_NOTHING_TOLERANCE", "жёсткая"),
    ("е", "ГРАНИЦЫ ПО НАПРАВЛЕНИЮ: недобор (часть значения открыта — УТЕЧКА) и "
          "перебор (закрыт лишний текст — ПОРЧА), раздельно",
     "BOUNDARY_UNDER_TOLERANCE / BOUNDARY_OVER_TOLERANCE", "жёсткая"),
    ("ж", "ЗЕРКАЛО ПОДАВЛЕНИЯ — эталонных сущностей, погашенных отрицательным "
          "классом (CLAUSE_REF/ROLE_TERM/COLLECTIVE)", "0 БЕЗУСЛОВНО", "жёсткая"),
    ("з", "НАБОР ПО УМОЛЧАНИЮ — эталон закрыт на МАКСИМУМЕ и открыт у "
          "пользователя, который ничего не настраивал",
     "0 + поимённый реестр known_default_leaks.json", "жёсткая"),
    ("и", "МАСКА ЧУЖОГО ТИПА — значение спрятано, но под именем другого типа; "
          "число мягкое, поимённая сверка реестра жёсткая",
     "число — мягкий; состав known_single_guard.json — 0", "двойная"),
]

EXTRA_CONDITIONS = [
    ("1", "КРЕШИ — outcome != processed с исключением", "0", "жёсткая"),
    ("3", "FP ВСЕГО на объявленных негативах корпуса", "FP_TOLERANCE", "жёсткая"),
    ("4", "MANIFEST.sha256 — корпус не изменился до и после прогона", "OK", "жёсткая"),
    ("5", "ИЗВЕСТНЫЙ ДОЛГ — ADDRESS found=False & leak (реестр "
          "docs/known_leaks_stage_c.json)", "MISSED_LEAK_TOLERANCE", "мягкая по числу, "
     "состав — предупреждение"),
]

#: ПРИЧИНА красной строки — не измерение, а ССЫЛКА на уже записанный разбор
#: (`docs/FINDINGS.md`, `docs/STATE.md`). Ключ — подстрока сообщения гейта.
#: Строка без записи остаётся БЕЗ причины: выдумывать её здесь нельзя.
CAUSES = [
    ("masking_correctness[B границы",
     "`SEAMJOIN-MASKB-PAIR-SEAM` / `CHARNORM-MASKB-B3-SEPARATOR` — дефект ПРИБОРА: "
     "значение, разорванное вёрсткой, закрыто ПАРОЙ масок B3, а разделитель "
     "между половинами ('\\n' сборки PT-1) не принадлежит ни одному сегменту "
     "системы, и `_covered_by_union` бракует обе маски пары"),
    ("(3) FP по негативам",
     "столкновение определений на ЗАМОРОЖЕННОМ корпусе: `DATE` (корпус v1 "
     "размечался при выключенном детекторе даты) и `CONTRACT_NO` (v1 размечен "
     "под обратное определение). `CONTRACT_NO` внесён в `scope_exclusions.py`, "
     "`DATE` — нет: внесение типа требует санкции владельца (`docs/STATE.md` §0.10)"),
    ("over-mask прозы[PER]",
     "`SEAMJOIN-PER-LABEL-3` — залеченное имя, стоящее последним в строке, "
     "дотягивается до метки следующей («Тел», «Дата»)"),
    ("over-mask прозы[ORG]",
     "разбор этапа S3: банки-контрагенты, которых корпус v1 не размечает ни "
     "сущностью, ни негативом (названо в `gate_config.OVERMASK_NOTHING_TOLERANCE`)"),
    ("over-mask прозы[ADDRESS]",
     "`DATE-HEADER-CITY-EXPOSED` — спан адреса, перелезавший на дату, укоротился, "
     "и метрика переклассифицировала маску из «частично на эталоне» в «мимо всего»"),
    ("over-mask прозы[PASSPORT_ISSUER]", "запись причины не найдена"),
    ("over-mask прозы[ВСЕГО]",
     "сумма строк выше плюс `DATE-NORMREF-OVERMASK` — маски на датах законов, "
     "приказов и первичных документов (размен утечки на читаемость, "
     "решение владельца не принято, `docs/STATE.md` §0.9)"),
    ("границы[EMAIL", "запись причины не найдена"),
]


#: Разбор красных тестов ЭТОГО прогона. Как и CAUSES, это не измерение, а
#: результат чтения кода и прямой пробы; ключ — подстрока имени теста.
TEST_CAUSES = [
    ("tests/test_u1_packaging.py",
     "ОС: модуль `procutil` работает через `ctypes.windll` (Windows API), а "
     "поиск браузера ищет Edge/Chrome по путям Windows. На Linux падает "
     "`AttributeError: module 'ctypes' has no attribute 'windll'`. Не находка "
     "о продукте"),
    ("tests/test_corpus_v2_reproducible.py::test_rebuild_matches_the_committed_corpus",
     "ОС сборки корпуса: все 223 расхождения разобраны прямой пробой и ни одно "
     "не про содержание — 139 `_model/*.json` отличаются ТОЛЬКО переводом "
     "строки (CRLF в репозитории против LF здесь), 84 `.docx` — полем zip "
     "«version made by» (0x0014 DOS против 0x0314 UNIX) при побайтно "
     "СОВПАДАЮЩИХ членах архива и их метаданных. Заведено долгом "
     "`METRICS-CORPUS-REBUILD-OSLOCK`"),
    ("tests/component2/test_g_regression.py",
     "производный: тест гоняет весь набор вложенным процессом и краснеет "
     "оттого, что там красны те же восемь тестов выше"),
]


def test_cause_for(name):
    for key, why in TEST_CAUSES:
        if key in name:
            return why
    return "разбора нет — красный тест этого прогона не объяснён"


def cause_for(msg):
    for key, why in CAUSES:
        if key in msg:
            return why
    return None


_MARK = re.compile(r"^\((.)\)")


def gate_block(baseline, current):
    kl_ids, kl_count = G._known_leaks()
    v = G.evaluate(baseline, current, kl_ids, check_ledger=True,
                   default_leaks_ids=G._known_default_leaks(),
                   single_guard_ids=G._known_single_guard())

    def bucket(msgs):
        out = defaultdict(list)
        for m in msgs:
            mk = _MARK.match(m)
            out[mk.group(1) if mk else "?"].append(m)
        return out

    reds = bucket(v["regressions"])
    warns = bucket(v["warnings"])
    imps = bucket(v["improvements"])

    tol = {
        "PRECISION_TOLERANCE_PP": GC.PRECISION_TOLERANCE_PP,
        "LEAK_V2_TOLERANCE": GC.LEAK_V2_TOLERANCE,
        "MASKING_A_TOLERANCE_PP": GC.MASKING_A_TOLERANCE_PP,
        "MASKING_B_TOLERANCE_PP": GC.MASKING_B_TOLERANCE_PP,
        "MASKING_C_WARN_PP": GC.MASKING_C_WARN_PP,
        "OVERMASK_NOTHING_TOLERANCE": GC.OVERMASK_NOTHING_TOLERANCE,
        "BOUNDARY_UNDER_TOLERANCE": GC.BOUNDARY_UNDER_TOLERANCE,
        "BOUNDARY_OVER_TOLERANCE": GC.BOUNDARY_OVER_TOLERANCE,
        "FP_TOLERANCE": GC.FP_TOLERANCE,
        "MISSED_LEAK_TOLERANCE": GC.MISSED_LEAK_TOLERANCE,
    }
    lines = []
    for key, what, tolname, level in LINES + EXTRA_CONDITIONS:
        lines.append({
            "line": key, "what": what, "tolerance": tolname, "level": level,
            "red": len(reds.get(key, [])), "warn": len(warns.get(key, [])),
            "improved": len(imps.get(key, [])),
            "red_msgs": reds.get(key, []), "warn_msgs": warns.get(key, []),
        })
    known = {k for k, *_ in LINES + EXTRA_CONDITIONS}
    other = {k: msgs for k, msgs in reds.items() if k not in known}
    return {
        "lines": lines, "tolerances": tol,
        "n_red": len(v["regressions"]), "n_warn": len(v["warnings"]),
        "n_improved": len(v["improvements"]),
        "red_all": v["regressions"], "warn_all": v["warnings"],
        "unclassified_red": other,
        "debt": {"count": v["composition"]["cur_count"],
                 "base_count": v["composition"]["base_count"],
                 "registry": kl_count,
                 "not_in_registry": len(v["composition"]["cur_not_in_registry"]),
                 "registry_not_leaking": len(v["composition"]["registry_not_leaking"]),
                 "left": len(v["composition"]["left"]),
                 "joined": len(v["composition"]["joined"])},
        "line_z": {"n": v["cur_def"]["n"], "per_type": v["cur_def"]["per_type"],
                   "cases": [list(c) for c in v["cur_def"]["cases"]]},
        "line_i": {"masks": v["cur_x"]["n"],
                   "pairs": {"%s->%s" % k: n for k, n in
                             sorted(v["cur_x"]["pairs"].items(), key=lambda x: -x[1])},
                   "docs": {"%s->%s" % k: n for k, n in v["cur_x"]["docs"].items()},
                   "values": v["cur_sg"]["n"],
                   "values_per_type": v["cur_sg"]["per_type"],
                   "value_pairs": {"%s->%s" % k: n for k, n in
                                   sorted(v["cur_sg"]["pairs"].items(), key=lambda x: -x[1])},
                   "class_total": len(v["cur_sg"]["state"]),
                   "open_default": v["cur_sg"]["n_open_default"],
                   "open_max": v["cur_sg"]["n_open_max"],
                   "registry_opened": sum(
                       1 for k in G._known_single_guard()
                       if (v["cur_sg"]["state"].get(k) or {}).get("uncovered_max")
                       or (v["cur_sg"]["state"].get(k) or {}).get("uncovered_default"))},
        "suppressed_gold": len(v["suppressed_gold_all"]),
    }


# --------------------------------------------------------------------------- #
#                        7. ПО НАБОРАМ ТИПОВ                                   #
# --------------------------------------------------------------------------- #
def profiles_block(prof):
    """Свод дампа run_profiles.py: по каждому из четырёх наборов — маски,
    полнота, утечка и что осталось ОТКРЫТЫМ (ни одного закрытого символа)."""
    import type_policy as TP
    names = prof["profiles"]
    out = {"elapsed_sec": prof.get("elapsed_sec"), "sets": {}}
    for p in names:
        # ИМЕНА ТИПОВ РАЗНЫЕ в конфиге и в разметке (PERSON->PER,
        # BANK_ACCOUNT->ACCOUNT, INN_PERSON->INN_PER): без перевода «тип входит
        # в набор» считалось бы по чужому словарю и молча врало.
        raw = TP._PROFILE_TYPES[p]
        in_set = (None if raw is None
                  else {ML.TYPE_MAP.get(t, t) for t in raw})
        tot = Counter()
        per_type = defaultdict(Counter)
        open_types = Counter()
        docs_with_open = 0
        for d in prof["docs"]:
            v = d["profiles"][p]
            tot["masks"] += v["n_masks"]
            tot["masks_located"] += v["n_masks_located"]
            if v["open_entities"]:
                docs_with_open += 1
            for c in v["open_entities"]:
                open_types[c["type"]] += 1
            for t, s in v["per_type"].items():
                for k, n in s.items():
                    per_type[t][k] += n
        def inset(t):
            return in_set is None or t in in_set
        n = sum(per_type[t]["n"] for t in per_type)
        found = sum(per_type[t]["found"] for t in per_type)
        leak6 = sum(per_type[t]["leak6"] for t in per_type)
        leak8 = sum(per_type[t]["leak8"] for t in per_type)
        openf = sum(per_type[t]["open_full"] for t in per_type)
        # РАЗДЕЛЬНО: тип ВХОДИТ в набор (пользователь ждёт маску, открытое
        # значение — дыра) и тип ВНЕ набора (открытое значение — работа по
        # правилам, а не дефект). Один общий счёт смешал бы их и читался бы
        # как «набор по умолчанию течёт тысячами».
        n_in = sum(per_type[t]["n"] for t in per_type if inset(t))
        found_in = sum(per_type[t]["found"] for t in per_type if inset(t))
        leak_in = sum(per_type[t]["leak6"] for t in per_type if inset(t))
        open_in = sum(per_type[t]["open_full"] for t in per_type if inset(t))
        open_out = openf - open_in
        open_types_in = {t: n2 for t, n2 in open_types.items() if inset(t)}
        open_types_out = {t: n2 for t, n2 in open_types.items() if not inset(t)}
        nbik = sum(per_type[t]["n"] for t in per_type if t != "BIK")
        fbik = sum(per_type[t]["found"] for t in per_type if t != "BIK")
        l6bik = sum(per_type[t]["leak6"] for t in per_type if t != "BIK")
        types = TP._PROFILE_TYPES[p]
        out["sets"][p] = {
            "label": TP.PROFILE_LABELS[p],
            "n_types": (len(types) if types is not None
                        else len([t for t in TP.known_types(
                            os.path.join(ROOT, "entity_types.yaml"))])),
            "types": sorted(types) if types is not None else None,
            "masks": tot["masks"], "masks_located": tot["masks_located"],
            "gold": n, "found": found,
            "recall": pct(found, n, None),
            "recall_bik_excl": pct(fbik, nbik, None),
            "leak6": leak6, "leak6_pct": pct(leak6, n, None),
            "leak6_bik_excl": l6bik, "leak6_bik_excl_pct": pct(l6bik, nbik, None),
            "leak8": leak8, "leak8_pct": pct(leak8, n, None),
            "open_entities": openf, "open_by_type": dict(open_types.most_common()),
            "docs_with_open": docs_with_open,
            "gold_in_set": n_in, "found_in_set": found_in,
            "recall_in_set": pct(found_in, n_in, None),
            "leak_in_set": leak_in, "leak_in_set_pct": pct(leak_in, n_in, None),
            "open_in_set": open_in, "open_out_of_set": open_out,
            "open_by_type_in_set": dict(sorted(open_types_in.items(),
                                               key=lambda x: -x[1])),
            "open_by_type_out_of_set": dict(sorted(open_types_out.items(),
                                                   key=lambda x: -x[1])),
            "per_type": {t: dict(s) for t, s in sorted(per_type.items())},
        }
    # ЦЕНА НАБОРА. Полнота (маска СВОЕГО типа) от набора не зависит вовсе:
    # фильтр стоит ПОСЛЕ разрешения пересечений, и выключение чужого типа не
    # снимает маску своего. А УТЕЧКА зависит: значение, которое на «Максимуме»
    # случайно накрыла маска ЧУЖОГО типа, в узком наборе остаётся открытым.
    # Здесь ровно эта разница, на ОДНИХ И ТЕХ ЖЕ типах — типах узкого набора.
    mx = out["sets"]["maximum"]["per_type"]
    for p, s in out["sets"].items():
        raw = TP._PROFILE_TYPES[p]
        in_set = (None if raw is None else {ML.TYPE_MAP.get(t, t) for t in raw})
        keys = [t for t in s["per_type"] if in_set is None or t in in_set]
        leak_here = sum(s["per_type"][t]["leak6"] for t in keys)
        leak_max = sum(mx.get(t, {}).get("leak6", 0) for t in keys)
        open_here = sum(s["per_type"][t]["open_full"] for t in keys)
        open_max = sum(mx.get(t, {}).get("open_full", 0) for t in keys)
        s["price_leak_here"] = leak_here
        s["price_leak_at_maximum"] = leak_max
        s["price_leak_delta"] = leak_here - leak_max
        s["price_open_here"] = open_here
        s["price_open_at_maximum"] = open_max
        s["price_open_delta"] = open_here - open_max
    return out


# --------------------------------------------------------------------------- #
#                8. РАЗЛОЖЕНИЕ УТЕЧКИ ПО МЕХАНИЗМАМ                            #
# --------------------------------------------------------------------------- #
#: Класс порчи ДОКУМЕНТА берётся из `source` эталона («base» = чистый текст,
#: «mutated:<сид>:<класс>»), а не из метки trick сущности: сид и класс —
#: свойство документа, а trick — свойство ОДНОГО значения внутри него.
def doc_mutation_class(src):
    if not src or src == "base":
        return "чистый текст"
    parts = src.split(":")
    return parts[-1] if len(parts) >= 3 else src


FOCUS_TYPES = ("PER", "ORG", "ADDRESS", "PASSPORT")


def mechanisms_block(results):
    by_class = defaultdict(Counter)
    by_class_type = defaultdict(lambda: defaultdict(Counter))
    by_trick = defaultdict(Counter)
    status = Counter()
    frag_len = Counter()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        cls = doc_mutation_class(r.get("source"))
        for e in r["entities"]:
            t = e["type"]
            v2 = e["leak_v2"]
            hit6, hit8 = ML._leak_v2_hits(t, v2)
            for bag, key in ((by_class, cls), (by_trick, e.get("trick") or "(без метки)")):
                bag[key]["n"] += 1
                bag[key]["leak6"] += int(hit6)
                bag[key]["leak8"] += int(hit8)
                bag[key]["not_found"] += int(not e["found"])
                if hit6:
                    bag[key]["full" if v2.get("status") == "full" else "partial"] += 1
            s = by_class_type[cls][t]
            s["n"] += 1
            s["leak6"] += int(hit6)
            s["not_found"] += int(not e["found"])
            if hit6:
                status[v2.get("status", "?")] += 1
                # ДЛИНА УТЁКШЕГО ФРАГМЕНТА — самый длинный выживший кусок
                # значения. `window_len` есть только у числовой ветви, поэтому
                # берём длину строки фрагмента: она определена у всех веток.
                frags = v2.get("fragments") or []
                longest = max((len(str(f)) for f in frags), default=None)
                frag_len["неклассифицировано" if longest is None else longest] += 1
    def pack(bag):
        out = {}
        for k, s in sorted(bag.items(), key=lambda x: -x[1]["n"]):
            out[k] = {"n": s["n"], "leak6": s["leak6"], "leak8": s["leak8"],
                      "leak6_pct": pct(s["leak6"], s["n"], None),
                      "not_found": s["not_found"],
                      "full": s["full"], "partial": s["partial"]}
        return out
    focus = {}
    for cls, per in by_class_type.items():
        focus[cls] = {t: {"n": per[t]["n"], "leak6": per[t]["leak6"],
                          "leak6_pct": pct(per[t]["leak6"], per[t]["n"], None),
                          "not_found": per[t]["not_found"]}
                      for t in FOCUS_TYPES if per.get(t)}
    return {"by_doc_class": pack(by_class), "by_trick": pack(by_trick),
            "by_class_focus": focus,
            "leak_status": dict(status),
            "fragment_len": {str(k): n for k, n in sorted(
                frag_len.items(), key=lambda x: (isinstance(x[0], str), x[0]))}}


# --------------------------------------------------------------------------- #
#                     9. РАЗРЕЗЫ ПО ДОКУМЕНТАМ                                 #
# --------------------------------------------------------------------------- #
def documents_block(results):
    by_kind = defaultdict(Counter)
    by_fmt = defaultdict(Counter)
    per_doc = []
    for r in results:
        if r.get("outcome") != "processed":
            continue
        leaked = 0
        found = 0
        types = Counter()
        for e in r["entities"]:
            hit6, _ = ML._leak_v2_hits(e["type"], e["leak_v2"])
            leaked += int(hit6)
            found += int(e["found"])
            if hit6:
                types[e["type"]] += 1
        n = len(r["entities"])
        masks = r["n_detected"]
        for bag, key in ((by_kind, r.get("contract_type", "")), (by_fmt, r["format"])):
            bag[key]["docs"] += 1
            bag[key]["gold"] += n
            bag[key]["found"] += found
            bag[key]["leak6"] += leaked
            bag[key]["masks"] += masks
        per_doc.append({"doc_id": r["doc_id"], "format": r["format"],
                        "contract_type": r.get("contract_type", ""),
                        "source": r.get("source", ""), "gold": n,
                        "leak6": leaked, "masks": masks,
                        "types": dict(types.most_common())})

    def pack(bag):
        return {k: {"docs": s["docs"], "gold": s["gold"], "masks": s["masks"],
                    "recall": pct(s["found"], s["gold"], None),
                    "leak6": s["leak6"], "leak6_pct": pct(s["leak6"], s["gold"], None),
                    "masks_per_doc": s["masks"] / s["docs"] if s["docs"] else None}
                for k, s in sorted(bag.items())}
    top = sorted(per_doc, key=lambda d: (-d["leak6"], d["doc_id"]))[:10]
    return {"by_contract_kind": pack(by_kind), "by_format": pack(by_fmt),
            "top_leaking": top}


# --------------------------------------------------------------------------- #
#                   10. ОБРАТИМОСТЬ ПО ФОРМАТАМ                                #
# --------------------------------------------------------------------------- #
def roundtrip_block(v1, v2):
    def per_format(results):
        out = defaultdict(Counter)
        for r in results:
            if r.get("outcome") != "processed":
                continue
            f = r["format"]
            out[f]["docs"] += 1
            out[f]["rt_ok"] += int(r["roundtrip_ok"])
            out[f]["unresolved"] += r.get("unresolved", 0) or 0
            for m in r.get("masks", ()):
                out[f]["masks"] += 1
                out[f]["a_ok"] += int(m["a_ok"])
                out[f]["a_channel_ok"] += int(m["a_channel_ok"])
        return {f: {"docs": s["docs"], "masks": s["masks"],
                    "masking_a": pct(s["a_ok"], s["masks"], None),
                    "a_channel": pct(s["a_channel_ok"], s["masks"], None),
                    "roundtrip_byte_exact_docs": s["rt_ok"],
                    "unresolved": s["unresolved"]}
                for f, s in sorted(out.items())}
    return {"v1": per_format(v1), "v2": per_format(v2)}


# --------------------------------------------------------------------------- #
#                   11. РЕЕСТРЫ И ДОЛГИ ЧИСЛОМ                                 #
# --------------------------------------------------------------------------- #
def registries_block(v1, gate):
    out = {}
    # known_single_guard.json — линия «и», поимённая сверка
    sg_path = os.path.join(CORPUS1, "known_single_guard.json")
    sg_reg = json.load(open(sg_path, encoding="utf-8"))
    sg_ids = G._known_single_guard()
    cur_sg = ML.single_guard_summary(v1)
    cur_ids = set(cur_sg["covered"])
    out["known_single_guard.json"] = {
        "records": len(sg_reg.get("cases", [])),
        "matched": len(cur_ids & sg_ids),
        "only_in_dump": len(cur_ids - sg_ids),
        "only_in_registry": len(sg_ids - cur_ids),
        "meaning": "значение, спрятанное маской ЧУЖОГО типа (одиночная защита)",
    }
    # known_default_leaks.json — линия «з»
    dl_path = os.path.join(CORPUS1, "known_default_leaks.json")
    dl_reg = json.load(open(dl_path, encoding="utf-8"))
    dl_ids = G._known_default_leaks()
    cur_def = set(ML.default_profile_summary(v1)["cases"])
    out["known_default_leaks.json"] = {
        "records": len(dl_reg.get("cases", [])),
        "matched": len(cur_def & dl_ids),
        "only_in_dump": len(cur_def - dl_ids),
        "only_in_registry": len(dl_ids - cur_def),
        "meaning": "закрыто на МАКСИМУМЕ, открыто в наборе ПО УМОЛЧАНИЮ",
    }
    # overmask_ledger.json — журнал движений планки линии «д»
    ol = json.load(open(os.path.join(CORPUS1, "overmask_ledger.json"), encoding="utf-8"))
    entries = ol.get("entries", ol if isinstance(ol, list) else [])
    out["overmask_ledger.json"] = {
        "records": len(entries),
        "matched": None, "only_in_dump": None, "only_in_registry": None,
        "meaning": "журнал движений точки отсчёта линии «д» (автор + причина); "
                   "не сверяется с прогоном — это история решений, а не список случаев",
    }
    # known_leaks_stage_c.json — условие 5
    out["docs/known_leaks_stage_c.json"] = {
        "records": gate["debt"]["registry"],
        "matched": gate["debt"]["count"] - gate["debt"]["not_in_registry"],
        "only_in_dump": gate["debt"]["not_in_registry"],
        "only_in_registry": gate["debt"]["registry_not_leaking"],
        "meaning": "ADDRESS found=False и утечка (известный долг этапа C)",
    }
    return out


def findings_block():
    """СЧЁТ строк реестра долгов FINDINGS.md по разделам + счёт решений
    владельца, которых ждёт работа (docs/STATE.md §0). Считаются строки, а не
    смысл: чтобы число долгов в отчёте не приходилось переписывать руками."""
    text = open(os.path.join(ROOT, "docs", "FINDINGS.md"), encoding="utf-8").read()
    sections, cur = {}, None
    order = []
    for line in text.splitlines():
        m = re.match(r"^### (.+)$", line)
        if m:
            cur = m.group(1).strip()
            if cur not in sections:
                sections[cur] = 0
                order.append(cur)
            continue
        if cur and re.match(r"^\|\s*`", line):
            sections[cur] += 1
    open_sections = [s for s in order if re.match(r"^\d\.", s)]
    closed = [s for s in order if s.startswith("Закрыто")]
    state = open(os.path.join(ROOT, "docs", "STATE.md"), encoding="utf-8").read()
    m = re.search(r"## 0\. Решения владельца.*?\n(.*?)\n## 1\.", state, re.S)
    decisions = len(re.findall(r"^\d+\. \*\*", m.group(1), re.M)) if m else None
    return {
        "by_section": {s: sections[s] for s in open_sections},
        "open_total": sum(sections[s] for s in open_sections),
        "closed_this_stage": sum(sections[s] for s in closed),
        "owner_decisions_pending": decisions,
    }


# --------------------------------------------------------------------------- #
#                  12. РАЗМЕР И СОСТАВ СИСТЕМЫ                                 #
# --------------------------------------------------------------------------- #
def system_block(runs):
    import yaml
    cfg = yaml.safe_load(open(os.path.join(ROOT, "entity_types.yaml"), encoding="utf-8"))
    ent = cfg.get("entity_types", {})
    maskable = [t for t, s in ent.items() if isinstance(s, dict) and s.get("token_prefix")]
    barriers = [t for t in ent if t not in maskable]
    src_files, src_lines = 0, 0
    for name in sorted(os.listdir(os.path.join(ROOT, "src"))):
        if not name.endswith(".py"):
            continue
        src_files += 1
        with open(os.path.join(ROOT, "src", name), encoding="utf-8") as fh:
            src_lines += sum(1 for _ in fh)
    detectors = [n for n in sorted(os.listdir(os.path.join(ROOT, "src")))
                 if n.endswith(".py") and (
                     "detector" in n or n in ("multispan.py", "syntax_compound.py",
                                              "layout_repair.py", "anchor_registry.py",
                                              "normalizer.py"))]
    # Число тестов и исходы — из ХВОСТА лога набора (итоговая строка pytest).
    pytest_log = os.path.join(HERE, "run_pytest.log")
    tests = None
    if os.path.exists(pytest_log):
        txt = open(pytest_log, encoding="utf-8", errors="replace").read()
        tail = txt[-4000:]
        tests = {k.rstrip("s") if k.startswith("error") else k: int(v)
                 for v, k in re.findall(
                     r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)", tail)}
        tests["collected"] = sum(v for k, v in tests.items() if k != "collected")
        # ИМЕНА упавших тестов — иначе «2 failed» в отчёте нечитаемо и легко
        # принимается за шум. Строка pytest вида "FAILED path::test - reason".
        tests["failed_names"] = sorted(set(
            re.findall(r"^FAILED (\S+)", txt, re.M)))[:40]
    return {
        "entity_types_total": len(ent),
        "entity_types_maskable": len(maskable),
        "entity_types_maskable_list": sorted(maskable),
        "barrier_classes": sorted(barriers),
        "src_py_files": src_files, "src_py_lines": src_lines,
        "detector_modules": detectors,
        "arbitration_order": cfg.get("arbitration_order"),
        "tests": tests,
        "timings_sec": {k: v["elapsed_sec"] for k, v in runs.items()},
    }


# --------------------------------------------------------------------------- #
#                    13. ЧЕГО МЫ НЕ ЗНАЕМ (считаемая часть)                    #
# --------------------------------------------------------------------------- #
def unknowns_block(t1, t2, gate, sysb):
    """Считаемая часть раздела «чего мы не знаем»: какие типы в каком корпусе
    БЕЗ эталона, какие линии мягкие, что перенесено. Текстовая часть — в
    шаблоне отчёта; здесь только то, что можно посчитать из дампов."""
    maskable = set(sysb["entity_types_maskable_list"])
    # имена типов в замере отличаются от имён конфига (PERSON->PER и т.д.)
    alias = dict(getattr(ML, "TYPE_MAP", {}))
    measured = {alias.get(t, t) for t in maskable}
    have1 = {t for t, r in t1["rows"].items() if r["n_gold"]}
    have2 = {t for t, r in t2["rows"].items() if r["n_gold"]}
    soft = [l for l in gate["lines"] if "мягк" in l["level"]]
    soft_details = [
        ("masking C (тип маски), внутри линии «г»",
         "`gate_config.MASKING_C_WARN_PP` — падение печатается ПРЕДУПРЕЖДЕНИЕМ, "
         "решение владельца (`docs/STATE.md` §6)"),
        ("линия «и», ЧИСЛО значений под чужой маской",
         "мягкий: сегодня значение закрыто, ронять гейт нечем. ЖЁСТКАЯ половина "
         "той же линии — поимённая сверка реестра `known_single_guard.json`"),
        ("условие 5, известный долг ADDRESS",
         "`gate_config.MISSED_LEAK_TOLERANCE` — мягкий порог по решению владельца; "
         "компенсатор — диагностика СОСТАВА (подмена «N на другой N» даёт "
         "предупреждение)"),
    ]
    return {
        "types_maskable_measured_names": sorted(measured),
        "no_gold_v1": sorted(measured - have1),
        "no_gold_v2": sorted(measured - have2),
        "no_gold_anywhere": sorted(measured - have1 - have2),
        "soft_lines": [{"line": l["line"], "level": l["level"]} for l in soft],
        "soft_details": [{"what": a, "why": b} for a, b in soft_details],
        "carried": CARRIED,
        # ТИП БЕЗ СТРОКИ НЕ ОХРАНЯЕТСЯ: считаем типы, у которых на корпусе есть
        # маски/FP, но которых нет в ML.ALL_ENTITY_TYPES — значит ни одна линия
        # гейта не печатает по ним ОТДЕЛЬНОЙ строки, их вклад виден только в
        # агрегате.
        "types_without_gate_row": sorted(
            {t for blk in (t1, t2) for t, r in blk["rows"].items()
             if (r["n_masks"] or r["fp_neg"] or r["nothing"])
             and t not in ML.ALL_ENTITY_TYPES}),
    }


# --------------------------------------------------------------------------- #
#                              РЕНДЕР MARKDOWN                                 #
# --------------------------------------------------------------------------- #
def n_or_dash(x):
    return "—" if x is None else ("%d" % x if isinstance(x, int) else x)


def p_or_dash(x):
    return "—" if x is None or isinstance(x, str) else "%.2f" % x


def render(d):
    L = []
    W = L.append
    h = d["head"]
    W("# METRICS-FULL — полный срез метрик SHIFRATOR одним прогоном")
    W("")
    W("**Этот файл СОБРАН СКРИПТОМ** (`experiments/metrics_full/collect_metrics.py`) "
      "из дампов прогона `experiments/metrics_full/measure_all.py`. Руками в нём "
      "не написано ни одного числа. Числа, которые пересчитать было нечем, "
      "собраны в разделе 13 и помечены датой снимка.")
    W("")
    W("Зона этапа — ЗАМЕР. `src/`, `entity_types.yaml`, корпуса, эталон и планка "
      "не тронуты; `promote_baseline.py` не запускался.")
    W("")

    # ---------------------------------------------------------------- 1
    W("## 1. Шапка прогона")
    W("")
    W("| | |")
    W("|---|---|")
    W("| коммит | `%s` (ветка `%s`%s) |" % (h["commit"], h["branch"],
      ", рабочее дерево ГРЯЗНОЕ" if h["dirty"] else ", рабочее дерево чистое"))
    W("| дата сборки отчёта (UTC) | %s |" % h["date_utc"])
    W("| python | %s |" % h["python"])
    W("| машина | %s, %s |" % (h["platform"], h["machine"]))
    W("")
    W("**Хронометраж прогонов.** Время — этой машины и этого прогона; на машине "
      "владельца оно другое (раздел 13).")
    W("")
    W("| замер | команда | старт (UTC) | секунд | код возврата |")
    W("|---|---|---|---:|---:|")
    for name, r in h["runs"].items():
        W("| %s | `%s` | %s | %.1f | %d |"
          % (name, " ".join(os.path.basename(c) for c in r["cmd"]),
             r["started_utc"], r["elapsed_sec"], r["exit_code"]))
    W("")
    W("**Корпуса.**")
    W("")
    W("| | корпус v1 | корпус v2 |")
    W("|---|---:|---:|")
    for label, k in (("документов", "docs"), ("эталонных вхождений", "gold_entities"),
                     ("объявленных негативов", "negatives"),
                     ("файлов в MANIFEST", "manifest_files"),
                     ("обработано (processed)", "processed")):
        W("| %s | %s | %s |" % (label, h["v1"][k], h["v2"][k]))
    W("| MANIFEST.sha256 | %s | %s |"
      % ("OK" if h["v1"]["manifest_ok"] else "РАСХОЖДЕНИЙ %d" % h["v1"]["manifest_bad"],
         "OK" if h["v2"]["manifest_ok"] else "РАСХОЖДЕНИЙ %d" % h["v2"]["manifest_bad"]))
    W("| крешей | %d | %d |" % (len(h["v1"]["crashed"]), len(h["v2"]["crashed"])))
    W("| отказов (refused) | %d | %d |" % (len(h["v1"]["refused"]), len(h["v2"]["refused"])))
    W("| документов, восстановленных ПОБАЙТНО | %d из %d | %d из %d |"
      % (h["v1"]["roundtrip_ok"], h["v1"]["processed"],
         h["v2"]["roundtrip_ok"], h["v2"]["processed"]))
    W("| нераспознанных токенов при восстановлении | %d | %d |"
      % (h["v1"]["unresolved"], h["v2"]["unresolved"]))
    W("")
    W("«Восстановлен побайтно» — весь текст документа целиком; это СТРОЖЕ, чем "
      "инвариант обратимости (`masking A` ниже), который требует байт-в-байт "
      "ровно замаскированные ЗНАЧЕНИЯ. Расхождение даёт схлопывание `\\n` → ` ` "
      "в ячейке таблицы при сборке plain, само значение при этом цело.")
    W("")

    # ---------------------------------------------------------------- 2
    W("## 2. Агрегаты по продукту — оба корпуса")
    W("")
    W("Снято: v1 — полный гейт %d док. этого прогона; v2 — `measure_v2.py` %d док. "
      "этого прогона." % (h["v1"]["docs"], h["v2"]["docs"]))
    W("")
    a, b = d["product_v1"], d["product_v2"]
    W("**Утечка на двух корпусах меряется РАЗНЫМИ приборами**, и колонки нельзя "
      "класть рядом как одну величину: на v1 это оконная метрика `leak_v2` с "
      "порогами ≥6 и ≥8 (её судит линия «б» гейта), на v2 — позиционная "
      "`leak_pos` (цифры эталонного спана, не закрытые ни одной маской), у "
      "которой порогов нет вовсе, а «строгий» счёт — это доля `full`. "
      "Оконную метрику на v2 применять НЕЛЬЗЯ: у фабричных типов её диспетчер "
      "не имеет ветви и консервативно объявляет утёкшим КАЖДОЕ вхождение.")
    W("")
    W("| | корпус v1 | корпус v2 |")
    W("|---|---:|---:|")
    rows = [
        ("эталонных вхождений (все типы)", "gold_occurrences_all", i),
        ("эталонных вхождений (BIK-excl)", "gold_occurrences_bik_excl", i),
        ("полнота, все типы, %", "recall_all", p_or_dash),
        ("полнота, агрегат BIK-excl, %", "recall_bik_excl", p_or_dash),
        ("утечка (v1: ≥6 / v2: позиц.), BIK-excl, сущностей", "leak6_bik_excl", i),
        ("утечка (v1: ≥6 / v2: позиц.), BIK-excl, %", "leak6_bik_excl_pct", p_or_dash),
        ("утечка строгая (v1: ≥8 / v2: full), BIK-excl, сущностей", "leak8_bik_excl", i),
        ("утечка строгая (v1: ≥8 / v2: full), BIK-excl, %", "leak8_bik_excl_pct", p_or_dash),
        ("утечка, все типы, сущностей", "leak6_all", i),
        ("масок всего", "masks_total", i),
        ("масок, легших на эталон", "masks_on_gold", i),
        ("масок «мимо всего» (over-mask прозы)", "masks_on_nothing", i),
        ("масок на эталоне ЧУЖОГО типа", "masks_cross", i),
        ("ложных на ОБЪЯВЛЕННОМ негативе", "fp_on_negatives", i),
        ("masking A (обратимость), %", "masking_a", p_or_dash),
        ("masking B (границы), %", "masking_b", p_or_dash),
        ("masking C (тип маски), %", "masking_c", p_or_dash),
        ("недобор границ, сущностей", "under_n", i),
        ("недобор границ, символов", "under_ch", i),
        ("перебор границ, сущностей", "over_n", i),
        ("перебор границ, символов", "over_ch", i),
        ("без маски своего типа, сущностей (контекст)", "not_found_n", i),
        ("без маски своего типа, открыто символов", "not_found_ch", i),
    ]
    for label, key, fmt in rows:
        W("| %s | %s | %s |" % (label, fmt(a[key]), fmt(b[key])))
    W("")
    W("**FP на объявленных негативах — по типам-виновникам.**")
    W("")
    W("| тип | v1 | v2 |")
    W("|---|---:|---:|")
    for t in sorted(set(a["fp_by_type"]) | set(b["fp_by_type"])):
        W("| %s | %d | %d |" % (t, a["fp_by_type"].get(t, 0), b["fp_by_type"].get(t, 0)))
    W("| **ВСЕГО** | **%d** | **%d** |" % (a["fp_on_negatives"], b["fp_on_negatives"]))
    W("")
    W("**Утёкшие сущности по типам** (v1 — порог ≥6, v2 — позиционная метрика).")
    W("")
    W("| тип | v1 | v2 |")
    W("|---|---:|---:|")
    for t in sorted(set(a["leaked_by_type"]) | set(b["leaked_by_type"]),
                    key=lambda x: -(a["leaked_by_type"].get(x, 0)
                                    + b["leaked_by_type"].get(x, 0))):
        W("| %s | %d | %d |" % (t, a["leaked_by_type"].get(t, 0),
                                b["leaked_by_type"].get(t, 0)))
    W("")
    return L


def render_types(L, d):
    W = L.append
    W("## 3. По типам — полная таблица, оба корпуса")
    W("")
    W("Прочерк — эталона этого типа в корпусе нет ни одного вхождения; тип при "
      "этом может иметь маски (они видны в колонках «мимо всего» и «fp на "
      "негативах»). Строка есть у КАЖДОГО типа, включая те, у которых нет "
      "эталона нигде: тип без строки не охраняется.")
    W("")
    for corpus, key, docs in (("v1", "types_v1", d["head"]["v1"]["docs"]),
                              ("v2", "types_v2", d["head"]["v2"]["docs"])):
        rows = d[key]["rows"]
        W("### 3.%s. Корпус %s (%d док., прогон этого этапа)"
          % ("1" if corpus == "v1" else "2", corpus, docs))
        W("")
        lab6, lab8, note = leak_labels(corpus)
        W("Утечка здесь — %s." % note)
        W("")
        W("| тип | эталон | найдено | полнота %% | %s | %s, %% | %s | %s, %% | "
          "точность %% | fp на негат. | масок | мимо всего | masking B %% | masking C %% | "
          "недобор шт | недобор симв | перебор шт | перебор симв |"
          % (lab6, lab6, lab8, lab8))
        W("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for t in sorted(rows):
            r = rows[t]
            if not (r["n_gold"] or r["n_masks"] or r["fp_neg"] or r["nothing"]):
                continue
            dash = lambda v, f=i: ("—" if not r["n_gold"] else f(v))
            W("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %d | %d | %d | %s | %s | %s | %s | %s | %s |"
              % (t,
                 dash(r["n_gold"]), dash(r["found"]), dash(r["recall"], p_or_dash),
                 dash(r["leak6"]), dash(r["leak6_pct"], p_or_dash),
                 dash(r["leak8"]), dash(r["leak8_pct"], p_or_dash),
                 p_or_dash(r["precision"]), r["fp_neg"], r["n_masks"], r["nothing"],
                 p_or_dash(r["masking_b"]), p_or_dash(r["masking_c"]),
                 dash(r["under_n"]), dash(r["under_ch"]),
                 dash(r["over_n"]), dash(r["over_ch"])))
        W("")
        W("**Разбор причины промаха** (сущности, у которых маски своего типа нет): "
          "`nothing` — не нашли ничем; `wrong_type:X` — на этом месте стоит маска "
          "типа X.")
        W("")
        W("| тип | не найдено всего | nothing | wrong_type — чей |")
        W("|---|---:|---:|---|")
        any_miss = False
        for t in sorted(rows):
            m = rows[t]["miss"]
            if not m:
                continue
            any_miss = True
            wrong = {k.split(":", 1)[1]: v for k, v in m.items() if k.startswith("wrong_type")}
            W("| %s | %d | %d | %s |"
              % (t, sum(m.values()), m.get("nothing", 0),
                 ", ".join("%s ×%d" % (k, v) for k, v in
                           sorted(wrong.items(), key=lambda x: -x[1])) or "—"))
        if not any_miss:
            W("| — | 0 | 0 | — |")
        W("")


def render_rest(L, d):
    W = L.append

    # ---------------------------------------------------------------- 4
    be = d["by_entity"]
    W("## 4. По СУЩНОСТЯМ, а не по вхождениям (корпус v1, пересчитано этим прогоном)")
    W("")
    W("Сущность — группа эталонных вхождений ОДНОГО документа с одним типом и "
      "одним нормализованным значением. «Защищено целиком» — все вхождения "
      "закрыты маской СВОЕГО типа И ни один фрагмент не дожил до анонимного "
      "текста. Прибор — `tests/corpus/by_entity.py`.")
    W("")
    W("| | по вхождениям, % | по сущностям, % | разница, пп |")
    W("|---|---:|---:|---:|")
    for label, k in (("ПРЯМЫЕ идентификаторы — найдено", "direct"),
                     ("КОСВЕННЫЕ — найдено", "indirect"),
                     ("ВСЕГО — найдено", "total")):
        s = be[k]
        W("| %s | %s | %s | %+.2f |" % (label, p_or_dash(s["occ_found_pct"]),
                                        p_or_dash(s["ent_found_pct"]), s["delta_found_pp"]))
    for label, k in (("ПРЯМЫЕ — защищено целиком", "direct"),
                     ("КОСВЕННЫЕ — защищено целиком", "indirect"),
                     ("ВСЕГО — защищено целиком", "total")):
        s = be[k]
        W("| %s | %s | %s | %+.2f |" % (label, p_or_dash(s["occ_prot_pct"]),
                                        p_or_dash(s["ent_prot_pct"]), s["delta_prot_pp"]))
    W("")
    W("Вхождений %d, сущностей %d; более одного вхождения у %d сущностей — "
      "только на них две метрики и могут разойтись."
      % (be["total"]["occ"], be["total"]["ent"], be["total"]["multi"]))
    W("")
    W("| тип | вхожд. | сущн. | найдено по вхожд., % | найдено по сущн., % | "
      "защищено по вхожд., % | защищено по сущн., % |")
    W("|---|---:|---:|---:|---:|---:|---:|")
    for t, s in be["per_type"].items():
        W("| %s | %d | %d | %s | %s | %s | %s |"
          % (t, s["occ"], s["ent"], p_or_dash(s["occ_found_pct"]),
             p_or_dash(s["ent_found_pct"]), p_or_dash(s["occ_prot_pct"]),
             p_or_dash(s["ent_prot_pct"])))
    W("")
    W("### 4.1. Дельта к §2.3 `docs/STATE.md`")
    W("")
    W("Колонка «было» — ЕДИНСТВЕННОЕ место этого раздела, где стоят "
      "ПЕРЕНЕСЁННЫЕ числа: снимок этапа DEFAULT-GATE, в самом STATE помеченный "
      "«`by_entity.py` этим этапом НЕ гонялся». Прежний дамп не сохранён, "
      "пересчитать «как было» нечем — можно только назвать разницу.")
    W("")
    ov = CARRIED["§2.3 STATE — счёт по сущностям (для НАЗВАНИЯ ДЕЛЬТЫ, "
                "не для таблиц)"]["values"]
    W("| строка §2.3 | было (DEFAULT-GATE) | стало (этот прогон) | дельта, пп |")
    W("|---|---:|---:|---:|")
    pairs = [
        ("ПРЯМЫЕ — найдено, по вхождениям", "direct_found_occ",
         be["direct"]["occ_found_pct"]),
        ("ПРЯМЫЕ — найдено, по сущностям", "direct_found_ent",
         be["direct"]["ent_found_pct"]),
        ("ПРЯМЫЕ — защищено, по вхождениям", "direct_prot_occ",
         be["direct"]["occ_prot_pct"]),
        ("ПРЯМЫЕ — защищено, по сущностям", "direct_prot_ent",
         be["direct"]["ent_prot_pct"]),
        ("КОСВЕННЫЕ — найдено, по вхождениям", "indirect_found_occ",
         be["indirect"]["occ_found_pct"]),
        ("КОСВЕННЫЕ — найдено, по сущностям", "indirect_found_ent",
         be["indirect"]["ent_found_pct"]),
        ("КОСВЕННЫЕ — защищено, по вхождениям", "indirect_prot_occ",
         be["indirect"]["occ_prot_pct"]),
        ("КОСВЕННЫЕ — защищено, по сущностям", "indirect_prot_ent",
         be["indirect"]["ent_prot_pct"]),
        ("ВСЕГО — найдено, по вхождениям", "total_found_occ",
         be["total"]["occ_found_pct"]),
        ("ВСЕГО — найдено, по сущностям", "total_found_ent",
         be["total"]["ent_found_pct"]),
        ("ВСЕГО — защищено, по вхождениям", "total_prot_occ",
         be["total"]["occ_prot_pct"]),
        ("ВСЕГО — защищено, по сущностям", "total_prot_ent",
         be["total"]["ent_prot_pct"]),
    ]
    for label, k, now in pairs:
        W("| %s | %.2f | %.2f | %+.2f |" % (label, ov[k], now, now - ov[k]))
    pb0 = d["persons"]
    W("| ЛЮДИ — сущностей `PER` по поверхности, шт | %d | %d | %+d |"
      % (ov["per_surface"], pb0["surface"]["n"],
         pb0["surface"]["n"] - ov["per_surface"]))
    W("| ЛЮДИ — сведено по фамилии, шт | %d | %d | %+d |"
      % (ov["per_by_surname"], pb0["by_surname"]["n"],
         pb0["by_surname"]["n"] - ov["per_by_surname"]))
    W("| ЛЮДИ — защищено СТРОГО, %% | %.2f | %.2f | %+.2f |"
      % (ov["per_strict_pct"], pb0["by_surname"]["strict_pct"],
         pb0["by_surname"]["strict_pct"] - ov["per_strict_pct"]))
    W("| ЛЮДИ — защищено ПРОДУКТОВО, %% | %.2f | %.2f | %+.2f |"
      % (ov["per_product_pct"], pb0["by_surname"]["product_pct"],
         pb0["by_surname"]["product_pct"] - ov["per_product_pct"]))
    W("| ЛЮДИ — с открытым упоминанием, чел | %d | %d | %+d |"
      % (ov["per_open_any"], pb0["by_surname"]["open_any"],
         pb0["by_surname"]["open_any"] - ov["per_open_any"]))
    W("")
    W("**Читать дельту со знаком осторожно.** Между снимком DEFAULT-GATE и этим "
      "прогоном прошли этапы SINGLE-GUARD, PER-SPREAD, DATE-ON, CHAR-NORM и "
      "SEAM-JOIN; какая часть движения чья — по двум точкам не восстановить, и "
      "этот отчёт такого утверждения не делает.")
    W("")

    # ---------------------------------------------------------------- 5
    pb = d["persons"]
    W("## 5. По ЛЮДЯМ — главная цифра для человека (корпус v1)")
    W("")
    W("| | по поверхности значения | сведённые по фамилии |")
    W("|---|---:|---:|")
    W("| сущностей `PER` | %d | %d |" % (pb["surface"]["n"], pb["by_surname"]["n"]))
    W("| из них с более чем одним вхождением | %d | %d |"
      % (pb["surface"]["multi"], pb["by_surname"]["multi"]))
    W("| все вхождения закрыты маской своего типа, %% | %s | %s |"
      % (p_or_dash(pb["surface"]["masked_all_pct"]),
         p_or_dash(pb["by_surname"]["masked_all_pct"])))
    W("| защищено целиком, СТРОГИЙ критерий, %% | %s | **%s** |"
      % (p_or_dash(pb["surface"]["strict_pct"]), p_or_dash(pb["by_surname"]["strict_pct"])))
    W("| защищено, ПРОДУКТОВЫЙ критерий (не утекло ничего), %% | %s | **%s** |"
      % (p_or_dash(pb["surface"]["product_pct"]), p_or_dash(pb["by_surname"]["product_pct"])))
    W("| имеют хотя бы одно ОТКРЫТОЕ упоминание (строгий), шт | %d | **%d** |"
      % (pb["surface"]["open_any"], pb["by_surname"]["open_any"]))
    W("| у кого хоть что-то УТЕКЛО (продуктовый), шт | %d | **%d** |"
      % (pb["surface"]["leaking"], pb["by_surname"]["leaking"]))
    W("")
    W("Вхождений `PER` в эталоне — %d. Строгий критерий требует ещё и маску "
      "СВОЕГО типа; продуктовый — только чтобы ничего не утекло. Разрыв между "
      "ними и есть класс «значение спрятано под именем чужого типа» (линия «и», "
      "раздел 6)." % pb["occurrences"])
    W("")
    W("Счёт по фамилии ЗАНИЖЕН по строгости в обе стороны и это названо в самом "
      "приборе: однофамильцы в одном договоре сливаются в одного человека, а "
      "разные формы одного имени, у которых самый длинный токен разный, "
      "остаются разными людьми.")
    W("")

    # ---------------------------------------------------------------- 6
    g = d["gate"]
    W("## 6. Все линии гейта поимённо (корпус v1, прогон этого этапа)")
    W("")
    W("Гейт: **красных строк %d**, предупреждений %d, улучшений к точке отсчёта %d. "
      "Точка отсчёта — `tests/corpus/results_baseline.json`; этим этапом она НЕ "
      "двигалась." % (g["n_red"], g["n_warn"], g["n_improved"]))
    W("")
    W("| линия | что меряет | допуск | уровень | красных строк | предупреждений |")
    W("|---|---|---|---|---:|---:|")
    for l in g["lines"]:
        tol = l["tolerance"]
        val = g["tolerances"].get(tol)
        tol_s = "`%s` = %s" % (tol, val) if val is not None else tol
        W("| **%s** | %s | %s | %s | %d | %d |"
          % (l["line"], l["what"], tol_s, l["level"], l["red"], l["warn"]))
    W("")
    if g["unclassified_red"]:
        W("Красные строки, не отнесённые ни к одной линии: %s"
          % json.dumps(g["unclassified_red"], ensure_ascii=False))
        W("")
    W("### 6.1. Каждая красная строка — числом и причиной")
    W("")
    W("Числа в строке — «точка отсчёта → этот прогон». Колонка «причина» — "
      "ССЫЛКА на уже записанный разбор (`docs/FINDINGS.md`, `docs/STATE.md`), а "
      "не вывод этого этапа; где записи нет, там стоит «запись причины не "
      "найдена», и выдумывать её отчёт не имеет права.")
    W("")
    W("| # | линия | строка гейта (число → число) | причина, если она записана |")
    W("|---:|---|---|---|")
    for k, m in enumerate(g["red_all"], 1):
        mk = _MARK.match(m)
        body = m[mk.end():].strip() if mk else m
        W("| %d | %s | %s | %s |"
          % (k, mk.group(1) if mk else "?", body.replace("|", "\\|"),
             cause_for(m) or "запись причины не найдена"))
    W("")
    W("### 6.2. Предупреждения (гейт не роняют)")
    W("")
    for m in g["warn_all"]:
        W("* %s" % m)
    W("")
    W("### 6.3. Линия «з» — набор ПО УМОЛЧАНИЮ, состав поимённо")
    W("")
    W("Случаев: **%d**, по типам: %s." % (g["line_z"]["n"],
      ", ".join("%s %d" % kv for kv in sorted(g["line_z"]["per_type"].items())) or "—"))
    W("")
    W("| документ | тип | спан |")
    W("|---|---|---|")
    for doc_id, t, s, e in g["line_z"]["cases"]:
        W("| `%s` | %s | [%d:%d] |" % (doc_id, t, s, e))
    W("")
    W("### 6.4. Линия «и» — маска ЧУЖОГО типа, состав поимённо")
    W("")
    li = g["line_i"]
    W("Две единицы счёта, и разница осмысленна: **%d масок** неверного типа "
      "(эталон в наборе по умолчанию, маска — вне набора) и **%d значений** под "
      "одиночной защитой — то есть закрытых маской ЧУЖОГО типа."
      % (li["masks"], li["values"]))
    W("")
    W("Сам класс, который дамп кладёт в поле `single_guard`, ШИРЕ нарочно: в "
      "него идут все эталонные сущности набора по умолчанию БЕЗ маски своего "
      "типа — и «прикрыта чужой маской», и «не прикрыта ничем». Всего таких "
      "**%d**, из них **%d** не закрыты ничем даже на «Максимуме» (это промахи "
      "полноты, их меряют линии «б» и «е», а не «и») и **%d** — под чужой "
      "маской. **ЖЁСТКАЯ половина линии: реестровых случаев, переставших быть "
      "прикрытыми, — %d** (допуск 0)."
      % (li["class_total"], li["open_max"], li["values"], li["registry_opened"]))
    W("")
    W("| эталон → маска | масок | документов |")
    W("|---|---:|---:|")
    for k, n in li["pairs"].items():
        W("| %s | %d | %d |" % (k, n, li["docs"].get(k, 0)))
    W("")
    W("| эталон → маска | ЗНАЧЕНИЙ |")
    W("|---|---:|")
    for k, n in li["value_pairs"].items():
        W("| %s | %d |" % (k, n))
    W("")
    W("Значения под одиночной защитой по типам эталона: %s."
      % ", ".join("%s %d" % kv for kv in sorted(li["values_per_type"].items())))
    W("")
    W("### 6.5. Прочие условия прогона")
    W("")
    W("| условие | факт |")
    W("|---|---|")
    W("| (1) креши | %d |" % len(d["head"]["v1"]["crashed"]))
    W("| (4) MANIFEST до и после | %s |"
      % ("OK" if d["head"]["v1"]["manifest_ok"] else "РАСХОЖДЕНИЕ"))
    W("| (ж) эталонных сущностей погашено отрицательным классом | %d |"
      % g["suppressed_gold"])
    W("| (5) известный долг ADDRESS (реестр %d) | %d; не документировано реестром %d; "
      "из реестра больше не течёт %d |"
      % (g["debt"]["registry"], g["debt"]["count"], g["debt"]["not_in_registry"],
         g["debt"]["registry_not_leaking"]))
    W("")

    # ---------------------------------------------------------------- 7
    pf = d["profiles"]
    W("## 7. По НАБОРАМ типов — то, что видит пользователь (корпус v1)")
    W("")
    W("Замер `experiments/metrics_full/run_profiles.py`: одна детекция и одно "
      "разрешение пересечений на документ, поверх — четыре раскладки масок. "
      "Гейт меряет ТОЛЬКО «Максимум», поэтому по наборам продукт систематически "
      "не измерялся ни разу.")
    W("")
    W("**Считать надо ДВУМЯ счётами, и смешивать их нельзя.** Эталонная сущность "
      "типа, ВХОДЯЩЕГО в набор, оставшаяся открытой, — это дыра: пользователь "
      "ждал маску и не получил её. Сущность типа ВНЕ набора, оставшаяся "
      "открытой, — работа по правилам: он сам выбрал её не прятать. Один общий "
      "счёт читался бы как «набор по умолчанию течёт тысячами», что неправда.")
    W("")
    W("| набор | типов | масок | эталон типов НАБОРА | полнота НАБОРА, % | "
      "утечка ≥6 в наборе, шт | утечка в наборе, % | ОТКРЫТО целиком: типов "
      "набора | ОТКРЫТО целиком: типов вне набора |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for p, s in pf["sets"].items():
        W("| %s (`%s`) | %d | %d | %d | %s | %d | %s | **%d** | %d |"
          % (s["label"], p, s["n_types"], s["masks"], s["gold_in_set"],
             p_or_dash(s["recall_in_set"]), s["leak_in_set"],
             p_or_dash(s["leak_in_set_pct"]), s["open_in_set"], s["open_out_of_set"]))
    W("")
    W("Для справки — те же наборы, но по ВСЕМУ эталону корпуса (включая типы, "
      "которые набор намеренно не прячет):")
    W("")
    W("| набор | эталон всего | полнота, % | полнота BIK-excl, % | утечка ≥6, шт | "
      "утечка ≥6, % | утечка ≥8, шт | открыто целиком всего |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|")
    for p, s in pf["sets"].items():
        W("| %s | %d | %s | %s | %d | %s | %d | %d |"
          % (p, s["gold"], p_or_dash(s["recall"]), p_or_dash(s["recall_bik_excl"]),
             s["leak6"], p_or_dash(s["leak6_pct"]), s["leak8"], s["open_entities"]))
    W("")
    W("«Открыто целиком» — эталонная сущность, у которой в этом наборе не закрыт "
      "НИ ОДИН символ ни одной маской (маска чужого типа тоже считается защитой: "
      "значение в LLM не уходит).")
    W("")
    for p, s in pf["sets"].items():
        W("**%s (`%s`)** — открытых сущностей %d в %d документах."
          % (s["label"], p, s["open_entities"], s["docs_with_open"]))
        W("")
        W("  * типов НАБОРА (дыра): %s"
          % (", ".join("%s %d" % kv for kv in s["open_by_type_in_set"].items())
             or "нет ни одной"))
        W("  * типов ВНЕ набора (по правилам): %s"
          % (", ".join("%s %d" % kv for kv in s["open_by_type_out_of_set"].items())
             or "нет ни одной"))
        W("")
    W("### 7.1. Цена набора — на ОДНИХ И ТЕХ ЖЕ типах")
    W("")
    W("Полнота от набора не зависит НИ НА ЕДИНИЦУ: фильтр типов стоит ПОСЛЕ "
      "разрешения пересечений, и выключение чужого типа не снимает маску "
      "своего. Зависит УТЕЧКА — значение, которое на «Максимуме» случайно "
      "накрыла маска ЧУЖОГО типа, в узком наборе остаётся открытым. Таблица "
      "меряет ровно эту разницу: одни и те же эталонные сущности (типов "
      "набора), два раскладывания масок.")
    W("")
    W("| набор | эталон типов набора | утечка ≥6 в этом наборе | утечка ≥6 при "
      "«Максимуме» | цена набора, сущностей | открыто целиком здесь | открыто "
      "целиком при «Максимуме» | цена, сущностей |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|")
    for p, s in pf["sets"].items():
        W("| %s | %d | %d | %d | **%+d** | %d | %d | **%+d** |"
          % (p, s["gold_in_set"], s["price_leak_here"], s["price_leak_at_maximum"],
             s["price_leak_delta"], s["price_open_here"],
             s["price_open_at_maximum"], s["price_open_delta"]))
    W("")
    W("Гейт видит из этой цены ТОЛЬКО линию «з» (раздел 6.3): она считает "
      "сущности, закрытые на максимуме и открытые по умолчанию, СИМВОЛЬНЫМ "
      "покрытием, а не метрикой утечки, и потому даёт другое, меньшее число. "
      "Обе цифры честные и меряют разное: линия «з» — «маски не стало вовсе», "
      "колонка выше — «от значения дожил читаемый фрагмент».")
    W("")
    W("**Наборы «Всё, включая деньги» и «Максимум» СОВПАДАЮТ по составу** — оба "
      "%d типов из %d маскируемых, и все числа по ним равны. Четвёртый набор "
      "сегодня не добавляет к третьему ничего: каждый маскируемый тип конфига "
      "объявляет `with_money` в своём ключе `sets:`."
      % (pf["sets"]["with_money"]["n_types"], pf["sets"]["maximum"]["n_types"]))
    W("")
    W("**Состав наборов** (из собранного `entity_types.yaml`, ключ `sets:`):")
    W("")
    for p, s in pf["sets"].items():
        W("* `%s` — %d типов: %s"
          % (p, s["n_types"],
             ", ".join(s["types"]) if s["types"] else
             "сентинел `None` — фильтр не применяется вовсе"))
    W("")

    # ---------------------------------------------------------------- 8
    mb = d["mechanisms"]
    W("## 8. Разложение утечки по механизмам (корпус v1)")
    W("")
    W("Класс порчи берётся из поля `source` эталона: `base` — чистый текст, "
      "`mutated:<сид>:<класс>` — документ, испорченный целиком одним классом "
      "(`combo` — несколько классов сразу). Это свойство ДОКУМЕНТА; метка "
      "`trick` отдельного значения разобрана ниже отдельной таблицей.")
    W("")
    W("| класс порчи документа | эталонных вхождений | утечка ≥6 | доля, % | "
      "утечка ≥8 | не найдено | full | partial |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, s in mb["by_doc_class"].items():
        W("| %s | %d | %d | %s | %d | %d | %d | %d |"
          % (k, s["n"], s["leak6"], p_or_dash(s["leak6_pct"]), s["leak8"],
             s["not_found"], s["full"], s["partial"]))
    W("")
    W("**По типам внутри класса** — `PER`, `ORG`, `ADDRESS`, `PASSPORT`.")
    W("")
    W("| класс порчи | тип | эталон | утечка ≥6 | доля, % | не найдено |")
    W("|---|---|---:|---:|---:|---:|")
    for cls in mb["by_doc_class"]:
        for t, s in mb["by_class_focus"].get(cls, {}).items():
            W("| %s | %s | %d | %d | %s | %d |"
              % (cls, t, s["n"], s["leak6"], p_or_dash(s["leak6_pct"]), s["not_found"]))
    W("")
    W("**По одиночным меткам `trick`** — свойство отдельного значения, а не "
      "документа (значение может нести метку в документе любого класса).")
    W("")
    W("| метка | эталонных вхождений | утечка ≥6 | доля, % | не найдено |")
    W("|---|---:|---:|---:|---:|")
    for k, s in mb["by_trick"].items():
        W("| %s | %d | %d | %s | %d |" % (k, s["n"], s["leak6"],
                                          p_or_dash(s["leak6_pct"]), s["not_found"]))
    W("")
    W("**Тяжесть утечки: full против partial.** %s."
      % ", ".join("%s — %d" % kv for kv in sorted(mb["leak_status"].items())))
    W("")
    W("**Распределение длины утёкшего фрагмента** (самый длинный выживший кусок "
      "значения, в символах):")
    W("")
    W("| длина | случаев |")
    W("|---|---:|")
    for k, n in mb["fragment_len"].items():
        W("| %s | %d |" % (k, n))
    W("")

    # ---------------------------------------------------------------- 9
    db = d["documents"]
    W("## 9. Разрезы по документам (корпус v1)")
    W("")
    W("| вид договора | док. | эталон | полнота, % | утечка ≥6 | утечка, % | "
      "масок | масок на документ |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, s in db["by_contract_kind"].items():
        W("| %s | %d | %d | %s | %d | %s | %d | %.1f |"
          % (k or "(не указан)", s["docs"], s["gold"], p_or_dash(s["recall"]),
             s["leak6"], p_or_dash(s["leak6_pct"]), s["masks"], s["masks_per_doc"]))
    W("")
    W("| формат | док. | эталон | полнота, % | утечка ≥6 | утечка, % | масок | "
      "масок на документ |")
    W("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, s in db["by_format"].items():
        W("| .%s | %d | %d | %s | %d | %s | %d | %.1f |"
          % (k, s["docs"], s["gold"], p_or_dash(s["recall"]), s["leak6"],
             p_or_dash(s["leak6_pct"]), s["masks"], s["masks_per_doc"]))
    W("")
    W("**Десять документов с наибольшей утечкой.**")
    W("")
    W("| документ | формат | вид | эталон | утечка ≥6 | типы утёкшего |")
    W("|---|---|---|---:|---:|---|")
    for r in db["top_leaking"]:
        W("| `%s` | .%s | %s | %d | %d | %s |"
          % (r["doc_id"], r["format"], r["contract_type"], r["gold"], r["leak6"],
             ", ".join("%s ×%d" % kv for kv in r["types"].items()) or "—"))
    W("")

    # ---------------------------------------------------------------- 10
    rb = d["roundtrip"]
    W("## 10. Обратимость по форматам")
    W("")
    W("`masking A` — инвариант проекта: замаскированное значение восстанавливается "
      "байт-в-байт. Считается ПО МАСКАМ, не по документам.")
    W("")
    W("| корпус | формат | док. | масок | masking A, % | A-канал, % | "
      "док. восстановлено побайтно целиком | нераспознанных токенов |")
    W("|---|---|---:|---:|---:|---:|---:|---:|")
    for corpus in ("v1", "v2"):
        for fmt, s in rb[corpus].items():
            W("| %s | .%s | %d | %d | %s | %s | %d | %d |"
              % (corpus, fmt, s["docs"], s["masks"], p_or_dash(s["masking_a"]),
                 p_or_dash(s["a_channel"]), s["roundtrip_byte_exact_docs"],
                 s["unresolved"]))
    W("")
    W("**Компонент 2 (`.xlsx`, `.pptx`) — замера НЕТ, и это дыра, а не ноль.** "
      "Ни в одном корпусе нет ни одного `.xlsx`/`.pptx` документа: оба корпуса "
      "порождают только `.txt` и `.docx`. Обратимость этих двух форматов "
      "покрыта ТОЛЬКО юнит-тестами `tests/component2/` (единичные рукодельные "
      "файлы), то есть числа «masking A на .xlsx» не существует. Чтобы оно "
      "появилось, нужен корпус с этими форматами и разметкой к нему — сегодня "
      "его нет ни одного документа.")
    W("")

    # ---------------------------------------------------------------- 11
    W("## 11. Реестры и долги числом")
    W("")
    W("| реестр | записей | совпало с прогоном | только в прогоне | только в реестре |")
    W("|---|---:|---:|---:|---:|")
    for name, s in d["registries"].items():
        W("| `%s` | %s | %s | %s | %s |"
          % (name, n_or_dash(s["records"]), n_or_dash(s["matched"]),
             n_or_dash(s["only_in_dump"]), n_or_dash(s["only_in_registry"])))
    W("")
    for name, s in d["registries"].items():
        W("* `%s` — %s" % (name, s["meaning"]))
    W("")
    fb = d["findings"]
    W("**Открытые долги `docs/FINDINGS.md` — по разделам** (счёт строк реестра, "
      "не пересказ):")
    W("")
    W("| раздел | записей |")
    W("|---|---:|")
    for s, n in fb["by_section"].items():
        W("| %s | %d |" % (s, n))
    W("| **ВСЕГО открытых** | **%d** |" % fb["open_total"])
    W("")
    W("Закрыто и перенесено в архив последним этапом: %d. "
      "**Решений владельца, которых ждёт работа (`docs/STATE.md` §0): %s.**"
      % (fb["closed_this_stage"], n_or_dash(fb["owner_decisions_pending"])))
    W("")

    # ---------------------------------------------------------------- 12
    sb = d["system"]
    W("## 12. Размер и состав системы")
    W("")
    W("| | |")
    W("|---|---:|")
    W("| типов в `entity_types.yaml` всего | %d |" % sb["entity_types_total"])
    W("| из них МАСКИРУЕМЫХ (есть `token_prefix`) | %d |" % sb["entity_types_maskable"])
    W("| отрицательных классов-барьеров (без `token_prefix`) | %d |"
      % len(sb["barrier_classes"]))
    W("| модулей `.py` в `src/` | %d |" % sb["src_py_files"])
    W("| строк кода в `src/` (со всеми комментариями) | %d |" % sb["src_py_lines"])
    W("| модулей детекции и нормализации | %d |" % len(sb["detector_modules"]))
    t = sb["tests"] or {}
    W("| тестов в наборе (passed) | %s |" % n_or_dash(t.get("passed")))
    W("| из них failed / skipped / xfailed | %s / %s / %s |"
      % (n_or_dash(t.get("failed", 0)), n_or_dash(t.get("skipped", 0)),
         n_or_dash(t.get("xfailed", 0))))
    for label, k in (("время полного набора тестов, с", "pytest"),
                     ("время полного гейта v1, с", "gate_v1"),
                     ("время замера v2, с", "v2"),
                     ("время замера по наборам, с", "profiles"),
                     ("время быстрого среза, с", "subsample")):
        W("| %s | %s |" % (label, n_or_dash(sb["timings_sec"].get(k))))
    W("")
    if t.get("failed_names"):
        W("")
        W("**Красные тесты этого прогона — поимённо и с разбором.** Набор гонялся "
          "на Linux (раздел 13.6); тест, падающий по свойству ОС, — не находка о "
          "продукте, но и молчать о нём нельзя. **Набор НЕ зелёный, и объявлять "
          "его зелёным этот отчёт не имеет права.**")
        W("")
        W("| тест | почему красный |")
        W("|---|---|")
        for name in t["failed_names"]:
            W("| `%s` | %s |" % (name, test_cause_for(name)))
    W("")
    W("Маскируемые типы: %s." % ", ".join(sb["entity_types_maskable_list"]))
    W("")
    W("Барьерные (не маскируемые) классы: %s." % ", ".join(sb["barrier_classes"]))
    W("")
    W("Модули детекции и нормализации: %s." % ", ".join(sb["detector_modules"]))
    W("")

    # ---------------------------------------------------------------- 13
    ub = d["unknowns"]
    W("## 13. Чего мы не знаем")
    W("")
    W("**Этот раздел важнее остальных.** Отчёт без него будет прочитан как "
      "обещание, а он им не является.")
    W("")
    W("### 13.1. Размеченных реальных договоров нет ни одного")
    W("")
    W("Оба корпуса ПОРОЖДЕНЫ генератором, который писал И текст, И разметку. "
      "Конструкция, которую генератор не предусмотрел, не появится ни в "
      "документе, ни в эталоне — и её отсутствие будет НЕВИДИМЫМ. Значит "
      "каждое число выше — ВЕРХНЯЯ ГРАНИЦА, а не измерение работы на живом "
      "договоре. Частоту любого класса дефектов в реальных договорах измерить "
      "сегодня нечем.")
    W("")
    W("### 13.2. Какие типы в каком корпусе не размечены вовсе")
    W("")
    W("Маскируемых типов — %d. Прочерк в таблице раздела 3 значит ровно это: "
      "полноту и утечку типа на этом корпусе не измеряет ничто."
      % len(ub["types_maskable_measured_names"]))
    W("")
    W("| | типы |")
    W("|---|---|")
    W("| нет эталона на v1 | %s |" % (", ".join(ub["no_gold_v1"]) or "—"))
    W("| нет эталона на v2 | %s |" % (", ".join(ub["no_gold_v2"]) or "—"))
    W("| **нет эталона НИГДЕ** | **%s** |" % (", ".join(ub["no_gold_anywhere"]) or "—"))
    W("")
    W("### 13.3. Какие линии мягкие и потому гейт не роняют")
    W("")
    W("| что меряется мягко | чем это задано |")
    W("|---|---|")
    for sd in ub["soft_details"]:
        W("| %s | %s |" % (sd["what"], sd["why"]))
    W("")
    W("Мягкая линия печатает падение и пропускает его дальше. Ноль красных "
      "строк по мягкой линии НЕ означает, что по ней всё в порядке.")
    W("")
    W("### 13.3а. Типы, у которых в гейте НЕТ отдельной строки")
    W("")
    if ub["types_without_gate_row"]:
        W("Список типов замера (`measure_lib.ALL_ENTITY_TYPES`) задан вручную. "
          "Типы ниже дают маски и/или ложные срабатывания на корпусе, но в этом "
          "списке их НЕТ — значит ни одна линия гейта не печатает по ним "
          "отдельной строки, и их вклад виден только в агрегате «ВСЕГО». "
          "**Тип без строки не охраняется** — это собственное правило гейта "
          "(комментарий у `ALL_ENTITY_TYPES`), и здесь оно нарушено: %s."
          % ", ".join("`%s`" % t for t in ub["types_without_gate_row"]))
    else:
        W("Таких типов нет: каждый тип, дающий маски на корпусе, имеет "
          "собственную строку в линиях гейта.")
    W("")
    W("### 13.4. Какие числа перенесены и с каких дат")
    W("")
    W("| что | дата снимка | почему пересчитать нечем | источник |")
    W("|---|---|---|---|")
    for k, c in ub["carried"].items():
        W("| %s | %s | %s | %s |" % (k, c["date"], c["why"], c["source"]))
    W("")
    W("**В таблицы разделов 2, 3, 7, 8, 9, 10 перенесённые числа НЕ вошли ни "
      "одним значением** — там только этот прогон. Колонка «реальный документ» "
      "из `docs/STATE.md` §2.1 сюда не переносилась вовсе: она измерена другим "
      "прибором (счёт масок без разметки) и в одной таблице с корпусными "
      "долями читалась бы как сравнимая величина, каковой не является.")
    W("")
    W("### 13.5. Чего в этом отчёте нет, потому что мерить нечем")
    W("")
    W("* **Обратимость `.xlsx`/`.pptx`** — ни одного документа этих форматов "
      "нет ни в одном корпусе (раздел 10).")
    W("* **Круг на собранной программе** — Windows, PyInstaller и настоящий "
      "браузер; в этом рабочем дереве не воспроизводится (раздел 13.4).")
    W("* **Реальный договор владельца** — на машине его нет по запрету 7, и "
      "разметки у него нет вовсе.")
    W("* **Цена ошибки в деньгах или в риске** — не измеряется ничем: у "
      "проекта нет ни одного числа о последствиях одной утёкшей сущности.")
    W("")
    W("### 13.6. Оговорка про машину, на которой снят этот срез")
    W("")
    W("Продукт — Windows-инструмент, а этот прогон снят на **%s**, python %s, в "
      "заново собранном окружении по `packaging/requirements-build.lock.txt` "
      "(без Windows-only пакетов: `pyinstaller`, `pefile`, `pywin32-ctypes`, "
      "`altgraph`, `colorama`). Все ЧИСЛА замера воспроизвелись; но времена "
      "прогонов в разделе 12 — этой машины и к машине владельца не относятся, а "
      "сборка exe и круг через настоящий браузер здесь не выполнимы вовсе."
      % (d["head"]["platform"], d["head"]["python"]))
    W("")
    W("")
    return L


# --------------------------------------------------------------------------- #
#                                   MAIN                                       #
# --------------------------------------------------------------------------- #
def main():
    v1 = load(DUMP_V1)
    base1 = load(BASELINE_V1)
    v2 = load(DUMP_V2)
    prof = load(DUMP_PROF)
    runs = load(RUNS) if os.path.exists(RUNS) else {}
    gold1 = load(os.path.join(CORPUS1, "gold.json"))
    gold2 = load(os.path.join(CORPUS2, "gold_v2.json"))

    t1 = per_type_block(v1, "v1")
    t2 = per_type_block(v2, "v2")
    gate = gate_block(base1, v1)
    sysb = system_block(runs)

    d = {
        "head": head_block(v1, v2, gold1, gold2, runs),
        "product_v1": product_block(v1, t1),
        "product_v2": product_block(v2, t2),
        "types_v1": t1, "types_v2": t2,
        "by_entity": by_entity_block(v1),
        "persons": persons_block(v1),
        "gate": gate,
        "profiles": profiles_block(prof),
        "mechanisms": mechanisms_block(v1),
        "documents": documents_block(v1),
        "roundtrip": roundtrip_block(v1, v2),
        "registries": registries_block(v1, gate),
        "findings": findings_block(),
        "system": sysb,
    }
    d["unknowns"] = unknowns_block(t1, t2, gate, sysb)
    # в json не кладём сырые агрегаты measure_lib — они дублируют таблицы
    for k in ("types_v1", "types_v2"):
        d[k] = {"rows": d[k]["rows"]}

    L = render(d)
    render_types(L, d)
    render_rest(L, d)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    print("отчёт  -> %s (%d строк)" % (OUT_MD, len(L)))
    print("машине -> %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
