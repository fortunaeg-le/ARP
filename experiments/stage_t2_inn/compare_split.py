# -*- coding: utf-8 -*-
"""
compare_split.py — ЭТАП T2-INN: доказательство, что разделение типа ИНН —
БУХГАЛТЕРИЯ, а не смена поведения детекции.

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ. Гейт сравнивает ПО ТИПАМ, и после переразметки
эталона он обязан покраснеть по построению: у типа `INN_PER` в старом baseline
вообще нет строки (n=0, утечка 0), а в новом прогоне у него 675 сущностей — любая
их утечка выглядит «ростом с нуля». Это не регресс и не улучшение, это появление
колонки. Чтобы отличить одно от другого, нужно сравнить СУММУ двух половин ИНН в
новом прогоне с ОДНОЙ строкой ИНН в старом — что и делает этот скрипт.

ЧТО СРАВНИВАЕТСЯ (всё — поимённо по документам, не по агрегату):
  1. МАСКИ. Множество (документ, значение маски) должно совпасть побайтно; тип
     маски сравнивается ПОСЛЕ склейки INN+INN_PER в одну корзину. Расхождение
     здесь означало бы, что разделение сдвинуло границы или потеряло маску.
  2. СУЩНОСТИ ЭТАЛОНА. found/leak_v1/leak_v2 по каждой записи — по (документ,
     значение); тип снова склеен. Расхождение = изменилась детекция.
  3. ЛОЖНЫЕ СРАБАТЫВАНИЯ. Те же корзины, тот же принцип.
  4. АГРЕГАТ. INN(было) против INN+INN_PER(стало) и все прочие типы — байт-в-байт.

Запуск:
    venv/Scripts/python.exe experiments/stage_t2_inn/compare_split.py \
        --before tests/corpus/results_baseline.json \
        --after  tests/corpus/results_t2inn.json
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, CORPUS)

import measure_lib as ML  # noqa: E402

#: Две половины одного прежнего типа. Для сравнения «до/после» они склеиваются:
#: до этапа обе лежали под именем INN.
INN_BUCKET = {"INN", "INN_PER"}


def bucket(t):
    return "INN(оба)" if t in INN_BUCKET else t


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def by_doc(results):
    return {r["doc_id"]: r for r in results}


def cmp_masks(a, b):
    """Маски: (тип-корзина, значение, токен-префикс-без-номера НЕ сравниваем)."""
    problems = []
    da, db = by_doc(a), by_doc(b)
    if set(da) != set(db):
        problems.append("разный состав документов")
        return problems
    for doc_id in sorted(da):
        ma = collections.Counter(
            (bucket(m["gtype"]), m["text"], m["a_ok"], m["b_ok"], m["c_ok"], m["scored"])
            for m in da[doc_id].get("masks", []))
        mb = collections.Counter(
            (bucket(m["gtype"]), m["text"], m["a_ok"], m["b_ok"], m["c_ok"], m["scored"])
            for m in db[doc_id].get("masks", []))
        if ma != mb:
            only_a = list((ma - mb).items())[:3]
            only_b = list((mb - ma).items())[:3]
            problems.append("%s: маски разошлись; было %r, стало %r"
                            % (doc_id, only_a, only_b))
    return problems


def cmp_entities(a, b):
    problems = []
    da, db = by_doc(a), by_doc(b)
    for doc_id in sorted(da):
        ea = collections.Counter(
            (bucket(e["type"]), e["text"], e["found"], e["exact"], e["leak_v1"],
             e["leak_v2"]["status"])
            for e in da[doc_id].get("entities", []))
        eb = collections.Counter(
            (bucket(e["type"]), e["text"], e["found"], e["exact"], e["leak_v1"],
             e["leak_v2"]["status"])
            for e in db[doc_id].get("entities", []))
        if ea != eb:
            problems.append("%s: сущности эталона разошлись; было %r, стало %r"
                            % (doc_id, list((ea - eb).items())[:3],
                               list((eb - ea).items())[:3]))
    return problems


def cmp_fp(a, b):
    problems = []
    da, db = by_doc(a), by_doc(b)
    for doc_id in sorted(da):
        fa = collections.Counter(
            (bucket(f["gtype"]), f["text"], f["on_negative"] is not None,
             f["cross_type"] is not None)
            for f in da[doc_id].get("false_positives", []))
        fb = collections.Counter(
            (bucket(f["gtype"]), f["text"], f["on_negative"] is not None,
             f["cross_type"] is not None)
            for f in db[doc_id].get("false_positives", []))
        if fa != fb:
            problems.append("%s: ложные срабатывания разошлись; было %r, стало %r"
                            % (doc_id, list((fa - fb).items())[:3],
                               list((fb - fa).items())[:3]))
    return problems


def agg_table(a, b):
    """Печатает сводку по типам с ИНН-склейкой и возвращает список расхождений."""
    aa, ab = ML.aggregate_results(a), ML.aggregate_results(b)

    def fold(agg):
        out = {}
        for t, d in agg["per_type"].items():
            k = bucket(t)
            cur = out.setdefault(k, {"n": 0, "found": 0, "leak_v1": 0,
                                     "leak_v2_6": 0, "leak_v2_8": 0, "fp": 0})
            for f in ("n", "found", "leak_v1", "leak_v2_6", "leak_v2_8"):
                cur[f] += d[f]
            cur["fp"] += agg["fp_on_neg"].get(t, 0)
        return out

    fa, fb = fold(aa), fold(ab)
    print("%-12s %6s %6s | %8s %8s | %8s %8s | %6s %6s" % (
        "тип", "n(б)", "n(т)", "found(б)", "found(т)",
        "leak6(б)", "leak6(т)", "fp(б)", "fp(т)"))
    diffs = []
    for t in sorted(set(fa) | set(fb)):
        x = fa.get(t, {"n": 0, "found": 0, "leak_v2_6": 0, "fp": 0})
        y = fb.get(t, {"n": 0, "found": 0, "leak_v2_6": 0, "fp": 0})
        mark = "" if x == y else "   <-- РАСХОЖДЕНИЕ"
        print("%-12s %6d %6d | %8d %8d | %8d %8d | %6d %6d%s" % (
            t, x["n"], y["n"], x["found"], y["found"],
            x["leak_v2_6"], y["leak_v2_6"], x["fp"], y["fp"], mark))
        if x != y:
            diffs.append((t, x, y))
    print("\nFP на негативах ВСЕГО: %d -> %d" % (aa["fp_on_neg_total"], ab["fp_on_neg_total"]))
    mb_ = aa.get("masking_correctness", {}).get("total")
    mc_ = ab.get("masking_correctness", {}).get("total")
    if mb_ and mc_:
        print("masking A/B/C: %.2f/%.2f/%.2f -> %.2f/%.2f/%.2f"
              % (ML.mc_rates(mb_) + ML.mc_rates(mc_)))
    print("креши: %d -> %d" % (len(aa["crashed"]), len(ab["crashed"])))
    return diffs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    args = ap.parse_args(argv)

    a, b = load(args.before), load(args.after)
    print("ДО : %s (%d документов)" % (args.before, len(a)))
    print("ПОСЛЕ: %s (%d документов)\n" % (args.after, len(b)))

    diffs = agg_table(a, b)

    print("\nПОИМЁННАЯ СВЕРКА (ИНН склеен в одну корзину):")
    problems = cmp_masks(a, b) + cmp_entities(a, b) + cmp_fp(a, b)
    if not problems and not diffs:
        print("  расхождений НЕТ: маски, сущности эталона и ложные срабатывания")
        print("  совпали по всем документам — разделение типа не изменило детекцию.")
        return 0
    for p in (problems or [])[:40]:
        print("  -", p)
    if diffs:
        print("  агрегатных расхождений: %d (см. таблицу выше)" % len(diffs))
    return 1


if __name__ == "__main__":
    sys.exit(main())
