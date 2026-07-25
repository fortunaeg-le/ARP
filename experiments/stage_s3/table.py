# -*- coding: utf-8 -*-
"""Сводная таблица этапа S3 по одному или двум дампам прогона корпуса.

    venv/Scripts/python.exe experiments/stage_s3/table.py <after.json> [before.json]

Печатает то же, что просит приёмка S3: leak_v2 по типам, masking A/B ПО ТИПАМ
МАСКИ (не агрегат), precision по типам, FP на негативах, over-mask прозы
(nothing) по типам, известный долг ADDRESS. Ничего не меняет.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, CORPUS)

import measure_lib as ML   # noqa: E402
import gate as G           # noqa: E402


def load(p):
    return json.load(open(p, encoding="utf-8"))


def nothing_by_type(results):
    d = collections.Counter()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for f in r.get("false_positives", []):
            if f.get("on_negative") is None and f.get("cross_type") is None:
                d[f["gtype"]] += 1
    return d


def nothing_texts(results, gtype):
    d = collections.Counter()
    where = collections.defaultdict(list)
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for f in r.get("false_positives", []):
            if (f.get("on_negative") is None and f.get("cross_type") is None
                    and f["gtype"] == gtype):
                key = f["text"]
                d[key] += 1
                where[key].append(r["doc_id"])
    return d, where


def recall_by_type(results):
    n = collections.Counter()
    found = collections.Counter()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            n[e["type"]] += 1
            found[e["type"]] += int(bool(e["found"]))
    return n, found


def report(after, before=None):
    A = load(after)
    B = load(before) if before else None
    agg_a = ML.aggregate_results(A)
    agg_b = ML.aggregate_results(B) if B else None
    pr_a = ML.precision_by_type(A)
    pr_b = ML.precision_by_type(B) if B else None
    nA, fA = recall_by_type(A)
    nB, fB = recall_by_type(B) if B else ({}, {})
    mcA = agg_a["masking_correctness"]
    mcB = agg_b["masking_correctness"] if agg_b else None
    nothA, nothB = nothing_by_type(A), (nothing_by_type(B) if B else {})

    print("документов: %d   крешей: %d" % (len(A), len(agg_a["crashed"])))
    hdr = "%-10s %7s %7s | %7s %7s | %8s %8s | %7s %7s | %6s" % (
        "тип", "leak≥6", "(было)", "maskB%", "(было)", "prec%", "(было)",
        "recall%", "(было)", "noth")
    print(hdr)
    print("-" * len(hdr))
    for t in ML.ALL_ENTITY_TYPES:
        ca = agg_a["per_type"].get(t, dict(G.EMPTY_STATS))
        cb = agg_b["per_type"].get(t, dict(G.EMPTY_STATS)) if agg_b else None
        sa = mcA["per_type"].get(t)
        sb = mcB["per_type"].get(t) if mcB else None
        ba = ML.mc_rates(sa)[1] if sa and sa["n_scored"] else float("nan")
        bb = ML.mc_rates(sb)[1] if sb and sb["n_scored"] else float("nan")
        pa = pr_a["per_type"].get(t, {}).get("precision")
        pb = pr_b["per_type"].get(t, {}).get("precision") if pr_b else None
        ra = 100.0 * fA[t] / nA[t] if nA.get(t) else float("nan")
        rb = 100.0 * fB[t] / nB[t] if nB.get(t) else float("nan")
        print("%-10s %7d %7s | %7.2f %7s | %8s %8s | %7.2f %7s | %6d" % (
            t, ca["leak_v2_6"], (cb["leak_v2_6"] if cb is not None else "—"),
            ba, ("%.2f" % bb if bb == bb else "—"),
            ("%.2f" % pa if pa is not None else "—"),
            ("%.2f" % pb if pb is not None else "—"),
            ra, ("%.2f" % rb if rb == rb else "—"),
            nothA.get(t, 0)))
    ta, tb = agg_a["total_bik_excl"], (agg_b["total_bik_excl"] if agg_b else None)
    print("-" * len(hdr))
    print("TOTAL(BIK-excl) leak_v2>=6: %d (%.1f%%)%s" % (
        ta["leak_v2_6"], 100.0 * ta["leak_v2_6"] / ta["n"],
        ("   было %d (%.1f%%)" % (tb["leak_v2_6"], 100.0 * tb["leak_v2_6"] / tb["n"])) if tb else ""))
    print("TOTAL(BIK-excl) leak_v2>=8: %d%s" % (
        ta["leak_v2_8"], ("   было %d" % tb["leak_v2_8"]) if tb else ""))
    a_, b_, c_ = ML.mc_rates(mcA["total"])
    print("masking A/B/C (агрегат): %.2f / %.2f / %.2f%s" % (
        a_, b_, c_,
        ("   было %.2f / %.2f / %.2f" % ML.mc_rates(mcB["total"])) if mcB else ""))
    print("масок всего: %d%s   легло на эталон: %d" % (
        mcA["total"]["n_masks"],
        ("  (было %d)" % mcB["total"]["n_masks"]) if mcB else "",
        mcA["total"]["n_scored"]))
    print("не закрыли эталон целиком (B fail): %d%s" % (
        mcA["total"]["n_scored"] - mcA["total"]["b_ok"],
        ("  (было %d)" % (mcB["total"]["n_scored"] - mcB["total"]["b_ok"])) if mcB else ""))
    print("FP на негативах: %d%s   over-mask прозы: %d%s   cross-type: %d" % (
        pr_a["fp_neg_total"], ("  (было %d)" % pr_b["fp_neg_total"]) if pr_b else "",
        pr_a["nothing_total"], ("  (было %d)" % pr_b["nothing_total"]) if pr_b else "",
        pr_a["cross_total"]))
    debt_a = G.missed_leak_ids(A)
    print("утечка-долг ADDRESS (found=False & leak): %d%s" % (
        len(debt_a), ("  (было %d)" % len(G.missed_leak_ids(B))) if B else ""))
    for t in ("ADDRESS", "ORG"):
        txt, where = nothing_texts(A, t)
        print("--- over-mask прозы %s: %d масок, %d уникальных ---"
              % (t, sum(txt.values()), len(txt)))
        for k, c in txt.most_common(40):
            print("   %3d  %-60r %s" % (c, k[:60], where[k][:2]))


if __name__ == "__main__":
    report(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
