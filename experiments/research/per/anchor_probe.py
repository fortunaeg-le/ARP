# -*- coding: utf-8 -*-
"""anchor_probe.py — где именно ломается якорь PER. РАЗВЕДКА, `src/` не трогает.

Якорь человека (`anchor_registry.PerAnchorDetector`) строится по совокупности:
само-якорь по форме ФИО (фамилия + имя/отчество ИЛИ фамилия + инициалы), либо
голая фамилия при внешнем маркере. Всё начинается с одного условия: слово
должно быть опознано как ФАМИЛИЯ (`_nameparts` -> 'surn'), а это словарный
разбор. Если фамилии в словаре нет — никакой контекст не поможет, и потолок
здесь не «правило не написали», а «слово не опознано».

Здесь по каждому провалившемуся вхождению спрашивается ровно это:
  * опознаётся ли фамилия словарём в ИСХОДНОМ виде (как в документе);
  * опознаётся ли она после свода омоглифов и приведения регистра;
  * набирается ли само-якорь (есть ли имя/отчество или инициалы рядом).
Разница между первым и вторым — цена символьных механизмов на самом узком
месте; провал во всех трёх — тот самый словарный потолок.
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
sys.path.insert(0, os.path.join(ROOT, "src"))

import measure_lib as ML                       # noqa: E402
import by_entity as BE                         # noqa: E402
import anchor_registry as AR                   # noqa: E402
from decompose import failed                   # noqa: E402
from causal import fix_homo, fix_case          # noqa: E402

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def parts_of(text):
    """Разбор значения по словарю: множество ролей каждого слова."""
    return [(w.group(0), AR._nameparts(w.group(0))) for w in WORD.finditer(text)]


def verdict(text):
    p = parts_of(text)
    surn = any("surn" in r for _, r in p)
    given = any(("name" in r or "patr" in r) for _, r in p)
    init = any(AR._is_initial(w) for w, _ in p)
    return surn, given, init


def main():
    path = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")
    results = json.load(open(path, encoding="utf-8"))
    groups = collections.OrderedDict()
    for d in results:
        if d.get("outcome") != "processed":
            continue
        for e in d["entities"]:
            if e["type"] != "PER":
                continue
            k = (d["doc_id"], BE.person_key(e["text"]))
            g = groups.setdefault(k, {"occ": [], "fail": []})
            g["occ"].append(e)
            if failed(e):
                g["fail"].append(e)
    bad = [g for g in groups.values() if g["fail"]]
    good = [g for g in groups.values() if not g["fail"]]

    out = io.open(os.path.join(HERE, "anchor.txt"), "w", encoding="utf-8")
    w = out.write
    w("=== СЛОВАРНЫЙ РАЗБОР ЗНАЧЕНИЯ (что видит якорь) ===\n")
    w("«фамилия» — слово опознано как surn; «само-якорь» — есть имя/отчество\n"
      "или инициалы рядом (тогда маркер контекста не нужен).\n\n")

    def tally(entries, title):
        c = collections.Counter()
        for text in entries:
            s0, g0, i0 = verdict(text)
            s1, g1, i1 = verdict(fix_case(fix_homo(text)))
            self0 = s0 and (g0 or i0)
            self1 = s1 and (g1 or i1)
            if self0:
                c["само-якорь набран как есть"] += 1
            elif self1:
                c["само-якорь появляется ПОСЛЕ свода символов/регистра"] += 1
            elif s1:
                c["фамилия опознана, но само-якоря нет (нужен маркер)"] += 1
            else:
                c["ФАМИЛИЯ НЕ ОПОЗНАНА СЛОВАРЁМ даже после свода"] += 1
        n = sum(c.values())
        w("[%s] всего %d\n" % (title, n))
        for k, v in c.most_common():
            w("   %-52s %5d  %5.1f%%\n" % (k, v, 100.0 * v / n if n else 0))
        w("\n")
        return c

    fail_texts = [e["text"] for g in bad for e in g["fail"]]
    ok_texts = [e["text"] for g in good for e in g["occ"]]
    cf = tally(fail_texts, "ПРОВАЛИВШИЕСЯ вхождения")
    tally(ok_texts, "вхождения ЗАЩИЩЁННЫХ людей (контроль)")

    # то же самое, но по ЛЮДЯМ: человек упирается в словарь, если НИ ОДНО его
    # провалившееся вхождение не даёт фамилию даже после свода.
    w("=== ПО ЛЮДЯМ ===\n")
    cp = collections.Counter()
    for g in bad:
        vs = []
        for e in g["fail"]:
            s1, g1, i1 = verdict(fix_case(fix_homo(e["text"])))
            vs.append((s1, s1 and (g1 or i1)))
        if all(not s for s, _ in vs):
            cp["все провалы — фамилия не опознана словарём"] += 1
        elif all(sa for _, sa in vs):
            cp["все провалы — само-якорь есть (промах НЕ словарный)"] += 1
        else:
            cp["смешанно / нужен внешний маркер"] += 1
    for k, v in cp.most_common():
        w("   %-52s %5d  %5.1f%% от 688\n" % (k, v, 100.0 * v / len(bad)))
    w("\nвсего незащищённых людей: %d из %d\n" % (len(bad), len(groups)))

    # примеры словарного провала — БЕЗ текста значения, только облик
    w("\n=== ОБЛИК ТЕХ, У КОГО ФАМИЛИЯ НЕ ОПОЗНАНА (реальных строк нет) ===\n")
    sh = collections.Counter()
    for g in bad:
        for e in g["fail"]:
            s1, _, _ = verdict(fix_case(fix_homo(e["text"])))
            if not s1:
                p = parts_of(fix_case(fix_homo(e["text"])))
                sh[tuple(sorted({r for _, rs in p for r in rs}) or ("<роли нет>",))] += 1
    for k, v in sh.most_common(12):
        w("   %-40s %5d\n" % ("+".join(k), v))

    # Уточнение потолка: «фамилия не опознана» — ещё НЕ приговор. Если имя и
    # отчество опознаны, а третье слово словарю неизвестно, то якорь даёт не
    # словарь, а ОБЛИК записи («Имя Отчество Хслово» = ФИО). Это правило, а не
    # словарь, — значит класс делится надвое, и делить его обязательно.
    w("\n=== ДЕЛЕНИЕ СЛОВАРНОГО ПОТОЛКА ===\n")
    c2 = collections.Counter()
    p2 = collections.Counter()
    for g in bad:
        marks = set()
        for e in g["fail"]:
            t = fix_case(fix_homo(e["text"]))
            s1, g1, i1 = verdict(t)
            if s1:
                marks.add("не словарный")
                continue
            p = parts_of(t)
            has_name = any("name" in r for _, r in p)
            has_patr = any("patr" in r for _, r in p)
            n_words = len(p)
            if (has_name and has_patr and n_words >= 3) or (has_name and i1):
                k = "спасает ПРАВИЛО ОБЛИКА (имя+отчество опознаны, 3-е слово нет)"
            elif has_name or has_patr:
                k = "опознана только часть имени — правило слабее, риск ЛС"
            else:
                k = "НИ ОДНО слово не опознано — настоящий словарный потолок"
            c2[k] += 1
            marks.add(k)
        if "не словарный" not in marks and marks:
            # человек целиком в словарном классе — куда он попадает
            p2[sorted(marks)[0] if len(marks) == 1 else "смешанный словарный"] += 1
    w("по ВХОЖДЕНИЯМ (из 363 «фамилия не опознана»):\n")
    for k, v in c2.most_common():
        w("   %-58s %5d\n" % (k, v))
    w("по ЛЮДЯМ (из 179 целиком словарных):\n")
    for k, v in p2.most_common():
        w("   %-58s %5d\n" % (k, v))
    out.close()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
