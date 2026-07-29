# -*- coding: utf-8 -*-
"""
addr_report.py — сводка по дампу addr_probe.py: ДВЕ СТОРОНЫ ОТДЕЛЬНО.

    venv/Scripts/python.exe experiments/stage_addr_b/addr_report.py before.json [after.json]

Печатает: распределение направлений ошибки (точно / короче / длиннее / обе /
не найдено) по всему набору и по категориям корпуса, суммарные символы недобора
и перебора, и (при двух файлах) таблицу «до -> после».
"""
import json
import os
import sys
from collections import Counter, defaultdict

ORDER = ["exact", "longer", "shorter", "both", "not_found"]
RUS = {"exact": "точно", "longer": "длиннее", "shorter": "КОРОЧЕ",
       "both": "КОРОЧЕ+длиннее", "not_found": "не найдено"}

# ЗНАЧАЩИЙ открытый кусок — тот, где осталась буква или цифра. Открытая
# хвостовая точка аббревиатуры («обл» вместо «обл.») формально делает маску
# короче эталона и роняет masking B, но ПДн не раскрывает: цену этих двух
# случаев нельзя складывать в одно число, иначе «утечка» раздувается
# пунктуацией. Поэтому обе стороны считаются и в «символах», и в «значащих».
def _significant(pieces):
    return [p for p in pieces if any(ch.isalnum() for ch in p)]
CATS = ["canonical", "ugly", "adversarial"]
CAT_RUS = {"canonical": "обычные", "ugly": "некрасивые", "adversarial": "состязательные"}


def load(p):
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), p)
    return json.load(open(p, encoding="utf-8"))


def stats(dump):
    ents = [e for d in dump for e in d["entities"]]
    st = {"n": len(ents), "dir": Counter(), "under": 0, "over": 0,
          "over_on_gold": 0, "by_cat": defaultdict(Counter),
          "under_by_cat": Counter(), "over_by_cat": Counter(),
          "fp_masks": sum(d["n_fp_masks"] for d in dump),
          "n_by_cat": Counter(),
          "sig_short": 0, "sig_short_by_cat": Counter(),
          "punct_short": 0, "sig_long": 0, "sig_long_by_cat": Counter()}
    for e in ents:
        st["dir"][e["direction"]] += 1
        st["under"] += e["under"]
        st["over"] += e["over"]
        st["over_on_gold"] += e["over_on_other_gold"]
        c = e["category"]
        st["by_cat"][c][e["direction"]] += 1
        st["n_by_cat"][c] += 1
        st["under_by_cat"][c] += e["under"]
        st["over_by_cat"][c] += e["over"]
        if e["direction"] in ("shorter", "both"):
            if _significant(e["open_pieces"]):
                st["sig_short"] += 1
                st["sig_short_by_cat"][c] += 1
            else:
                st["punct_short"] += 1
        if e["direction"] in ("longer", "both") and _significant(e["extra_pieces"]):
            st["sig_long"] += 1
            st["sig_long_by_cat"][c] += 1
    return st


def pct(x, n):
    return (100.0 * x / n) if n else 0.0


def show(st, title):
    print(f"\n=== {title}: n={st['n']} эталонных адресов ===")
    for k in ORDER:
        print("  %-16s %5d  %5.1f%%" % (RUS[k], st["dir"][k], pct(st["dir"][k], st["n"])))
    n_short = st["dir"]["shorter"] + st["dir"]["both"]
    n_long = st["dir"]["longer"] + st["dir"]["both"]
    print("  --")
    print("  масок КОРОЧЕ эталона (утечка):     %5d  %5.1f%%   символов открыто: %d"
          % (n_short, pct(n_short, st["n"]), st["under"]))
    print("     в т.ч. ЗНАЧАЩИЙ остаток (буква/цифра): %d (%.1f%%);  "
          "только пунктуация (хвостовая точка «обл.»): %d"
          % (st["sig_short"], pct(st["sig_short"], st["n"]), st["punct_short"]))
    print("  масок ДЛИННЕЕ эталона (перебор):   %5d  %5.1f%%   символов лишних:  %d "
          "(из них на др. эталонных сущностях: %d)"
          % (n_long, pct(n_long, st["n"]), st["over"], st["over_on_gold"]))
    print("     в т.ч. ЗНАЧАЩИЙ захват: %d (%.1f%%)"
          % (st["sig_long"], pct(st["sig_long"], st["n"])))
    print("  ADDRESS-масок мимо всех адресов (ложные): %d" % st["fp_masks"])
    print("  по категориям:")
    for c in CATS:
        cc, n = st["by_cat"][c], st["n_by_cat"][c]
        if not n:
            continue
        s = cc["shorter"] + cc["both"]
        l = cc["longer"] + cc["both"]
        print("    %-14s n=%4d | точно %4d (%5.1f%%) | КОРОЧЕ %4d (%5.1f%%, симв %5d) "
              "| длиннее %3d (%4.1f%%, симв %5d) | не найдено %3d (%4.1f%%)"
              % (CAT_RUS[c], n, cc["exact"], pct(cc["exact"], n), s, pct(s, n),
                 st["under_by_cat"][c], l, pct(l, n), st["over_by_cat"][c],
                 cc["not_found"], pct(cc["not_found"], n)))


def diff(a, b):
    print("\n=== ДО -> ПОСЛЕ (обе стороны раздельно) ===")
    for k in ORDER:
        print("  %-16s %5d -> %-5d  (%+d)" % (RUS[k], a["dir"][k], b["dir"][k],
                                              b["dir"][k] - a["dir"][k]))
    sa, sb = a["dir"]["shorter"] + a["dir"]["both"], b["dir"]["shorter"] + b["dir"]["both"]
    la, lb = a["dir"]["longer"] + a["dir"]["both"], b["dir"]["longer"] + b["dir"]["both"]
    print("  --")
    print("  КОРОЧЕ (утечка):  %5d -> %-5d (%+d)   символов: %d -> %d (%+d)"
          % (sa, sb, sb - sa, a["under"], b["under"], b["under"] - a["under"]))
    print("     значащий остаток: %d -> %d (%+d);  только пунктуация: %d -> %d (%+d)"
          % (a["sig_short"], b["sig_short"], b["sig_short"] - a["sig_short"],
             a["punct_short"], b["punct_short"], b["punct_short"] - a["punct_short"]))
    print("  ДЛИННЕЕ (перебор):%5d -> %-5d (%+d)   символов: %d -> %d (%+d)"
          % (la, lb, lb - la, a["over"], b["over"], b["over"] - a["over"]))
    print("     значащий захват: %d -> %d (%+d)"
          % (a["sig_long"], b["sig_long"], b["sig_long"] - a["sig_long"]))
    print("  ложных ADDRESS-масок: %d -> %d (%+d)"
          % (a["fp_masks"], b["fp_masks"], b["fp_masks"] - a["fp_masks"]))
    print("\n  по категориям:")
    for c in CATS:
        ca, cb = a["by_cat"][c], b["by_cat"][c]
        if not a["n_by_cat"][c]:
            continue
        f = lambda cc: (cc["shorter"] + cc["both"], cc["longer"] + cc["both"], cc["exact"],
                        cc["not_found"])
        s1, l1, e1, nf1 = f(ca)
        s2, l2, e2, nf2 = f(cb)
        print("    %-14s точно %4d->%-4d | КОРОЧЕ %4d->%-4d | длиннее %3d->%-3d | "
              "не найдено %3d->%-3d" % (CAT_RUS[c], e1, e2, s1, s2, l1, l2, nf1, nf2))


if __name__ == "__main__":
    a = stats(load(sys.argv[1]))
    show(a, sys.argv[1])
    if len(sys.argv) > 2:
        b = stats(load(sys.argv[2]))
        show(b, sys.argv[2])
        diff(a, b)
