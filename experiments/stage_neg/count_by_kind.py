# -*- coding: utf-8 -*-
"""Ложные маски на двойниках — РАЗРЕЗ ПО ВИДАМ СЛУЧАЯ (этап NEG).

Отличие от `experiments/stage_a3/count_lookalikes.py` одно, и оно всё:
там разрез делался по первым 52 знакам поля `why`, здесь — по слогу `kind`,
который двойник несёт в эталоне с этапа NEG. Разница не косметическая: до NEG
у типа был ОДИН вид случая, и разрез по причине совпадал с разрезом по типу;
теперь видов пять, причины у них длинные и начинаются похоже, и склейка двух
видов в одну строку прошла бы молча.

Считается по дампу прогона `tests/corpus_v2/results_v2_current.json`:
  * ЗНАМЕНАТЕЛЬ — сколько двойников этого вида случая в эталоне;
  * ЧИСЛИТЕЛЬ — на скольких из них легла ХОТЬ КАКАЯ-ТО маска (и какая именно).

Запуск:
    venv/Scripts/python.exe experiments/stage_neg/count_by_kind.py
    venv/Scripts/python.exe experiments/stage_neg/count_by_kind.py --json было.json
    venv/Scripts/python.exe experiments/stage_neg/count_by_kind.py --diff было.json
"""
import argparse
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


def collect(results_path=RESULTS):
    gold = {d["doc_id"]: d for d in json.load(open(GOLD, encoding="utf-8"))}
    results = json.load(open(results_path, encoding="utf-8"))
    if isinstance(results, dict):
        results = results.get("results", results.get("documents"))

    total = collections.Counter()
    bad = collections.Counter()
    by_mask = collections.defaultdict(collections.Counter)
    sample = {}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        g = gold.get(r["doc_id"])
        if not g:
            continue
        spans = [(m["start"], m["end"], m.get("gtype") or m.get("type"))
                 for m in (r.get("masks") or []) if m.get("start") is not None]
        for n in g.get("negatives", []):
            if not n.get("lookalike"):
                continue
            # Вид случая. У двойников A3/A7 слога нет — они попадают в строку
            # «<ТИП>/(a3)», и это честно: там вид случая ровно один.
            key = "%s/%s" % (n["type"], n.get("kind", "").split("/", 1)[-1] or "(a3)")
            if n.get("kind"):
                key = n["kind"]
            total[key] += 1
            covering = [gt for s, e, gt in spans if s < n["end"] and e > n["start"]]
            if not covering:
                continue
            bad[key] += 1
            for gt in set(covering):
                by_mask[key][gt] += 1
            sample.setdefault(key, (r["doc_id"], n["text"][:48]))
    return total, bad, by_mask, sample


def as_dict(total, bad):
    return {k: [bad[k], total[k]] for k in total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=RESULTS)
    ap.add_argument("--json", help="записать числа в файл (снимок «было»)")
    ap.add_argument("--diff", help="сравнить с ранее записанным снимком")
    a = ap.parse_args()

    total, bad, by_mask, sample = collect(a.results)
    print("=== ЛОЖНЫЕ МАСКИ НА ДВОЙНИКАХ, ПО ВИДАМ СЛУЧАЯ ===")
    print("  цифры на порождённом корпусе — верхняя граница, не измерение\n")
    print("  %-28s %10s  %-22s %s" % ("вид случая", "ложных", "какие маски", "пример"))
    prev = json.load(open(a.diff, encoding="utf-8")) if a.diff else None
    for key in sorted(total):
        which = ", ".join("%s=%d" % (k, v) for k, v in by_mask[key].most_common()) or "—"
        mark = ""
        if prev is not None:
            was = prev.get(key, [0, 0])[0]
            if was != bad[key]:
                mark = "   <-- было %d" % was
        doc, txt = sample.get(key, ("—", ""))
        print("  %-28s %5d/%-4d  %-22s %s%s"
              % (key, bad[key], total[key], which, txt, mark))
    print("\n  ИТОГО ложных: %d из %d двойников" % (sum(bad.values()), sum(total.values())))
    if a.json:
        json.dump(as_dict(total, bad), open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, sort_keys=True)
        print("  снимок записан:", a.json)


if __name__ == "__main__":
    main()
