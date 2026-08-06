# -*- coding: utf-8 -*-
"""Сколько отрицательных примеров каждого типа ошибочно маскируется.

Первое измерение ПЕРЕУСЕРДСТВОВАНИЯ в проекте. До этапа A3 меряли только
«нашли ли нужное»; «не нахватали ли лишнего» измерять было нечем — у 13 из 18
маскируемых типов не существовало ни одного отрицательного примера
(A2-NO-NEGATIVES-13-TYPES).

Считается по дампу прогона `tests/corpus_v2/results_v2_current.json`:
  * ЗНАМЕНАТЕЛЬ — сколько двойников (`lookalike`) этого типа в эталоне;
  * ЧИСЛИТЕЛЬ — на скольких из них легла ХОТЬ КАКАЯ-ТО маска.

Маска СВОЕГО типа и маска ЧУЖОГО разделены намеренно: «двойник ОГРН накрыт
маской OGRN» — промах именно детектора ОГРН, а «двойник ОГРН накрыт маской
PHONE» — промах телефона, и лечится он в другом месте.

Запуск:
    venv/Scripts/python.exe experiments/stage_a3/count_lookalikes.py
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GOLD = os.path.join(ROOT, "tests", "corpus_v2", "gold_v2.json")
RESULTS = os.path.join(ROOT, "tests", "corpus_v2", "results_v2_current.json")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def main():
    gold = {d["doc_id"]: d for d in json.load(open(GOLD, encoding="utf-8"))}
    results = json.load(open(RESULTS, encoding="utf-8"))
    if isinstance(results, dict):
        results = results.get("results", results.get("documents"))

    total = collections.Counter()          # двойников в эталоне, по типу
    hit_own = collections.Counter()        # накрыт маской СВОЕГО типа
    hit_other = collections.Counter()      # накрыт маской ЧУЖОГО типа
    by_mask = collections.defaultdict(collections.Counter)
    sample = {}

    for r in results:
        if r.get("outcome") != "processed":
            continue
        g = gold.get(r["doc_id"])
        if not g:
            continue
        looks = [n for n in g.get("negatives", []) if n.get("lookalike")]
        masks = r.get("masks") or []
        spans = [(m["start"], m["end"], m.get("gtype") or m.get("type")) for m in masks
                 if m.get("start") is not None]
        for n in looks:
            t = n["type"]
            total[t] += 1
            covering = [gt for s, e, gt in spans if s < n["end"] and e > n["start"]]
            if not covering:
                continue
            if t in covering:
                hit_own[t] += 1
            else:
                hit_other[t] += 1
            for gt in set(covering):
                by_mask[t][gt] += 1
            sample.setdefault((t, tuple(sorted(set(covering)))),
                              (r["doc_id"], n["text"][:60], n["why"][:80]))

    print("=== ОШИБОЧНЫЕ МАСКИ НА ОТРИЦАТЕЛЬНЫХ ПРИМЕРАХ (двойниках) ===")
    print("  цифры на порождённом корпусе — верхняя граница, не измерение")
    print("  (tests/corpus_v2/README.md, «КРАСНАЯ ЛИНИЯ»)\n")
    print("  %-9s %6s %8s %8s %7s   %s"
          % ("тип", "всего", "св.тип", "чуж.тип", "доля", "какие маски легли"))
    for t in sorted(total, key=lambda k: -(hit_own[k] + hit_other[k])):
        n = total[t]
        bad = hit_own[t] + hit_other[t]
        which = ", ".join("%s=%d" % (k, v)
                          for k, v in by_mask[t].most_common()) or "—"
        print("  %-9s %6d %8d %8d %6.1f%%   %s"
              % (t, n, hit_own[t], hit_other[t], 100.0 * bad / n if n else 0.0, which))

    print("\n=== ПРИМЕРЫ (по одному на сочетание «тип двойника -> маски») ===")
    for (t, combo), (doc, text, why) in sorted(sample.items()):
        print("  %-9s <- %-24s %s | %r" % (t, ",".join(combo), doc, text))

    # У типа бывает НЕСКОЛЬКО отрицательных примеров разного класса (у INN —
    # безъякорный ОКПО и якорный регистрационный номер), и общий процент их
    # смешивает. Разрез по причине показывает, какой именно класс сработал.
    print("\n=== РАЗРЕЗ ПО КОНКРЕТНОМУ ОТРИЦАТЕЛЬНОМУ ПРИМЕРУ ===")
    per_neg_total = collections.Counter()
    per_neg_bad = collections.Counter()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        g = gold.get(r["doc_id"])
        if not g:
            continue
        spans = [(m["start"], m["end"]) for m in (r.get("masks") or [])
                 if m.get("start") is not None]
        for n in g.get("negatives", []):
            if not n.get("lookalike"):
                continue
            key = (n["type"], n["why"][:52])
            per_neg_total[key] += 1
            if any(s < n["end"] and e > n["start"] for s, e in spans):
                per_neg_bad[key] += 1
    for key in sorted(per_neg_total, key=lambda k: (-per_neg_bad[k], k)):
        t, why = key
        print("  %-9s %4d/%-4d  %s" % (t, per_neg_bad[key], per_neg_total[key], why))


if __name__ == "__main__":
    main()
