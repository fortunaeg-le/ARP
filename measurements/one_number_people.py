# -*- coding: utf-8 -*-
"""ОДНА ЦИФРА ПРО ЛЮДЕЙ — все кандидаты рядом, на одном свежем прогоне."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
import measure_lib as ML  # noqa: E402
import by_entity as BE  # noqa: E402

out = open(os.path.join(HERE, "one_number_people.txt"), "w", encoding="utf-8")


def say(*a):
    out.write(" ".join(str(x) for x in a) + "\n")


def occ_stats(results):
    """PER по ВХОЖДЕНИЯМ: найдено / не утекло / защищено (и то, и другое)."""
    n = found = clean = prot = 0
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if e["type"] != "PER":
                continue
            n += 1
            f = bool(e["found"])
            hit6, _ = ML._leak_v2_hits("PER", e["leak_v2"])
            found += int(f)
            clean += int(not hit6)
            prot += int(f and not hit6)
    return n, found, clean, prot


def ent_stats(results, key_fn):
    ents = [g for g in BE.group_entities(results, key_fn=key_fn) if g["type"] == "PER"]
    n = len(ents)
    return (n,
            sum(1 for g in ents if g["masked_all"]),
            sum(1 for g in ents if not g["leak_any"]),
            sum(1 for g in ents if g["protected"]))


def report(tag, path):
    results = json.load(open(path, encoding="utf-8"))
    say("\n" + "=" * 100)
    say("ПРОГОН: %s   (%s)" % (tag, os.path.basename(path)))
    say("=" * 100)
    n, f, c, p = occ_stats(results)
    say("A. ПО ВХОЖДЕНИЯМ PER            знаменатель %5d вхождений" % n)
    say("     найдено (маска своего типа)   %6.2f%%  (%d)" % (100.0 * f / n, f))
    say("     не утекло (leak_v2>=6 нет)    %6.2f%%  (%d)" % (100.0 * c / n, c))
    say("     защищено (и то, и другое)     %6.2f%%  (%d)" % (100.0 * p / n, p))
    for lbl, key_fn in (("B. ПО СУЩНОСТЯМ (поверхность значения)", BE.value_key),
                        ("C. ПО ЛЮДЯМ (сведены по фамилии, person_key)",
                         lambda t, x: BE.person_key(x))):
        n2, m2, c2, p2 = ent_stats(results, key_fn)
        say("%-32s знаменатель %5d" % (lbl, n2))
        say("     все вхождения под маской свого типа %6.2f%%  (%d)" % (100.0 * m2 / n2, m2))
        say("     НИ ОДНО вхождение не утекло         %6.2f%%  (%d)" % (100.0 * c2 / n2, c2))
        say("     защищено (и то, и другое)           %6.2f%%  (%d)" % (100.0 * p2 / n2, p2))
    return results


say("ОДНА ЦИФРА ПРО ЛЮДЕЙ — сессия MEASURE-2, корпус v1 (324 документа)")
say("")
say("Три числа из задания меряют РАЗНОЕ и потому не сравнимы напрямую:")
say("  63.33 % — доля ВХОЖДЕНИЙ PER, которые закрыты и не утекли;")
say("  62.72 % — доля СУЩНОСТЕЙ PER (уникальная поверхность значения в документе);")
say("  62.28 % — доля ЛЮДЕЙ (вхождения сведены по фамилии, person_key).")
say("Ниже каждое пересчитано свежим прогоном и разложено на два критерия:")
say("«под маской своего типа» и «ничего не утекло». Это ключ ко всему разбору:")
say("критерии расходятся, и выбор основной цифры — выбор одного из них.")

report("МАКСИМУМ (все типы включены)", os.path.join(HERE, "results_measure2.json"))
report("ПО УМОЛЧАНИЮ (набор PERSONAL — то, что получает покупатель)",
       os.path.join(HERE, "results_default_profile.json"))
out.close()
print("ok")
