# -*- coding: utf-8 -*-
"""multilabel.py — тот же разбор, но БЕЗ приоритета механизмов.

Почему отдельным файлом. `decompose.py` приписывает вхождению ОДИН механизм по
порядку проверок, и первый же проверяемый («подменённые символы») собирает на
себя все комбинированные случаи корпуса (`mut:combo` — 230 вхождений, где
мутация применена НЕСКОЛЬКИМИ сразу). Ответ на вопрос «сколько людей закроет
один механизм» на таком счёте завышен по построению: у комбинированного
вхождения после починки омоглифов останутся перенос и регистр.

Здесь у вхождения НАБОР меток (сколько признаков в нём есть, столько и меток),
и человек считается закрытым механизмом M, только если у КАЖДОГО его
провалившегося вхождения M — единственная метка.
"""
import io
import json
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML          # noqa: E402
import by_entity as BE            # noqa: E402
from decompose import (shape, case_class, has_homoglyph, is_oblique,  # noqa: E402
                       INVIS, failed)


def labels(e):
    """ВСЕ признаки вхождения, а не первый попавшийся."""
    out = set()
    text = e["text"]
    tr = e.get("trick") or ""
    if has_homoglyph(text):
        out.add("подменённые буквы")
    if INVIS.search(text):
        out.add("невидимые символы")
    if "\n" in text or "linebreak" in tr or "cell_split" in tr \
            or "split_runs" in tr or "cell_boundary" in tr:
        out.add("разрыв вёрсткой")
    cc = case_class(text)
    if cc != "обычный" and cc != "нет букв":
        out.add("регистр: " + cc)
    mr = e.get("miss_reason") or ""
    if mr.startswith("wrong_type:"):
        out.add("чужой тип: " + mr.split(":", 1)[1])
    if is_oblique(text):
        out.add("косвенный падеж")
    sh = shape(text)
    if sh in ("фамилия одна", "одни инициалы"):
        out.add("облик: " + sh)
    if sh == "фамилия+инициалы":
        out.add("облик: фамилия+инициалы")
    if not out:
        out.add("НИ ОДНОГО ПРИЗНАКА (чистое значение, обычный регистр)")
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "tests", "corpus", "results_gate_current.json")
    results = json.load(open(path, encoding="utf-8"))

    groups = collections.OrderedDict()
    for d in results:
        if d.get("outcome") != "processed":
            continue
        for e in d["entities"]:
            if e["type"] != "PER":
                continue
            k = (d["doc_id"], BE.person_key(e["text"]))
            g = groups.setdefault(k, {"doc": d["doc_id"], "key": k[1],
                                      "occ": [], "fail": []})
            g["occ"].append(e)
            if failed(e):
                g["fail"].append(e)
    total = len(groups)
    bad = [g for g in groups.values() if g["fail"]]

    out = io.open(os.path.join(HERE, "multilabel.txt"), "w", encoding="utf-8")
    w = out.write
    w("людей: %d, незащищённых: %d\n\n" % (total, len(bad)))

    occ_lab = collections.Counter()      # меток на вхождениях
    ppl_lab = collections.Counter()      # людей, у кого метка ВСТРЕЧАЕТСЯ
    solo_occ = collections.Counter()     # вхождений, где метка ЕДИНСТВЕННАЯ
    close = collections.Counter()        # людей, кого метка закрывает ЦЕЛИКОМ
    nlab = collections.Counter()
    for g in bad:
        seen = set()
        ok_solo = None
        allsolo = True
        for e in g["fail"]:
            L = labels(e)
            nlab[len(L)] += 1
            seen |= L
            for m in L:
                occ_lab[m] += 1
            if len(L) == 1:
                m = next(iter(L))
                solo_occ[m] += 1
                if ok_solo is None:
                    ok_solo = m
                elif ok_solo != m:
                    allsolo = False
            else:
                allsolo = False
        for m in seen:
            ppl_lab[m] += 1
        if allsolo and ok_solo:
            close[ok_solo] += 1
        g["_labels"] = sorted(seen)

    w("=== МЕТКИ (вхождение может иметь несколько) ===\n")
    w("%-38s %7s %7s %7s %7s\n"
      % ("метка", "вхожд", "из них", "людей", "ЗАКР."))
    w("%-38s %7s %7s %7s %7s\n" % ("", "всего", "одна", "затр.", "людей"))
    for m, n in occ_lab.most_common():
        w("%-38s %7d %7d %7d %7d\n" % (m, n, solo_occ[m], ppl_lab[m], close[m]))
    w("\nчисло меток на провалившемся вхождении: %s\n"
      % dict(sorted(nlab.items())))
    w("людей, закрываемых ОДНОЙ меткой: %d из %d незащищённых (%.2f%% от %d)\n"
      % (sum(close.values()), len(bad),
         100.0 * sum(close.values()) / total, total))
    w("\n=== СОЧЕТАНИЯ МЕТОК У ЧЕЛОВЕКА (топ-20) ===\n")
    combos = collections.Counter(tuple(g["_labels"]) for g in bad)
    for c, n in combos.most_common(20):
        w("%4d  %s\n" % (n, " + ".join(c)))
    w("\n=== ПОТОЛОК: доля защиты, если закрыть N крупнейших меток ===\n")
    w("(человек закрыт, если ВСЕ метки всех его провалов входят в набор)\n")
    order = [m for m, _ in occ_lab.most_common()]
    cum = set()
    for m in order:
        cum.add(m)
        fixed = sum(1 for g in bad if set(g["_labels"]) <= cum)
        w("%-38s -> защита %6.2f%% (%d/%d)\n"
          % ("+ " + m, 100.0 * (total - len(bad) + fixed) / total,
             total - len(bad) + fixed, total))
    out.close()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
