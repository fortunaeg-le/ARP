# -*- coding: utf-8 -*-
"""PER-SPREAD — таблица ДО/ПОСЛЕ по двум дампам полного корпуса.

    venv/Scripts/python.exe experiments/per_spread/compare.py <до.json> <после.json>

Считает то, что требует правило техдиректора: сперва УТЕЧКА, потом полнота;
в ВХОЖДЕНИЯХ и в СУЩНОСТЯХ; долю людей, защищённых ЦЕЛИКОМ (сведение по
фамилии); ЛОЖНЫЕ срабатывания отдельной строкой; и общую картину по типам.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import by_entity as BE          # noqa: E402
import measure_lib as ML        # noqa: E402


def load(p):
    return json.load(open(p, encoding="utf-8"))


def occ_stats(dump, only_clean=False):
    """по ВХОЖДЕНИЯМ: всего / утечка (leak_v2 != none) / не найдено."""
    st = collections.defaultdict(lambda: [0, 0, 0])
    for r in dump:
        if only_clean and "__m" in r["doc_id"]:
            continue
        for e in r["entities"]:
            s = st[e["type"]]
            s[0] += 1
            if e["leak_v2"]["status"] != "none":
                s[1] += 1
            if not e["found"]:
                s[2] += 1
    return st


def ent_stats(dump, only_clean=False):
    """по СУЩНОСТЯМ (документ + тип + нормализованное значение)."""
    tot = collections.Counter()
    leak = collections.Counter()
    miss = collections.Counter()
    for r in dump:
        if only_clean and "__m" in r["doc_id"]:
            continue
        groups = collections.defaultdict(list)
        for e in r["entities"]:
            groups[(e["type"], BE.value_key(e["type"], e["text"]))].append(e)
        for (t, _), es in groups.items():
            tot[t] += 1
            if any(e["leak_v2"]["status"] != "none" for e in es):
                leak[t] += 1
            if not all(e["found"] for e in es):
                miss[t] += 1
    return tot, leak, miss


def person_stats(dump, only_clean=False):
    """ЛЮДИ: сведение всех форм PER одного документа по КЛЮЧУ ФАМИЛИИ
    (by_entity.person_key). Человек защищён ЦЕЛИКОМ, если ни одна его форма
    не дала утечки."""
    tot = whole = 0
    open_people = []
    for r in dump:
        if only_clean and "__m" in r["doc_id"]:
            continue
        by_p = collections.defaultdict(list)
        for e in r["entities"]:
            if e["type"] != "PER":
                continue
            by_p[BE.person_key(e["text"])].append(e)
        for k, es in by_p.items():
            tot += 1
            if all(e["leak_v2"]["status"] == "none" for e in es):
                whole += 1
            else:
                open_people.append((r["doc_id"], k))
    return tot, whole, open_people


def fp_stats(dump, only_clean=False):
    """ЛОЖНЫЕ: маска легла на текст, который не эталон.
      on-nothing  — ни эталон, ни объявленный негатив (порча документа);
      on-negative — на объявленном негативе (система назвала ПДн то, что корпус
                    прямо объявил не-ПДн);
      cross-type  — эталон есть, но названный тип другой (не порча текста)."""
    st = collections.Counter()
    items = collections.Counter()
    for r in dump:
        if only_clean and "__m" in r["doc_id"]:
            continue
        for f in r["false_positives"]:
            if f.get("cross_type"):
                st[("cross-type", f["gtype"])] += 1
            elif f.get("on_negative"):
                st[("on-negative", f["gtype"])] += 1
                items[("on-negative", f["gtype"], f["text"][:60])] += 1
            else:
                st[("on-nothing", f["gtype"])] += 1
                items[("on-nothing", f["gtype"], f["text"][:60])] += 1
    return st, items


def pct(a, b):
    return "—" if not b else "%.2f%%" % (100.0 * a / b)


def main():
    before, after = load(sys.argv[1]), load(sys.argv[2])
    for label, only_clean in (("ВЕСЬ КОРПУС (324 док.)", False),
                              ("ЧИСТЫЕ ДОКУМЕНТЫ (108)", True)):
        print("=" * 78)
        print(label)
        print("=" * 78)

        ob, oa = occ_stats(before, only_clean), occ_stats(after, only_clean)
        print("\n-- ПО ВХОЖДЕНИЯМ: утечка (leak_v2 != none) / не найдено --")
        print("%-11s %7s | %-19s | %-19s" % ("тип", "всего", "утечка до -> после", "не найдено до -> после"))
        for t in sorted(set(ob) | set(oa), key=lambda t: -ob[t][1]):
            b, a = ob[t], oa[t]
            mark = "  <<<" if b[1] != a[1] else ""
            print("%-11s %7d | %5d -> %5d (%+d) | %5d -> %5d (%+d)%s"
                  % (t, b[0], b[1], a[1], a[1] - b[1], b[2], a[2], a[2] - b[2], mark))
        tb = sum(v[1] for v in ob.values())
        ta = sum(v[1] for v in oa.values())
        n = sum(v[0] for v in ob.values())
        print("%-11s %7d | %5d -> %5d (%+d)" % ("ИТОГО", n, tb, ta, ta - tb))

        print("\n-- ПО СУЩНОСТЯМ (док. + тип + значение) --")
        tb_, lb, mb = ent_stats(before, only_clean)
        ta_, la, ma = ent_stats(after, only_clean)
        print("%-11s %7s | %-19s | %-19s" % ("тип", "всего", "утечка до -> после", "не найдено до -> после"))
        for t in sorted(set(tb_) | set(ta_), key=lambda t: -lb[t]):
            print("%-11s %7d | %5d -> %5d (%+d) | %5d -> %5d (%+d)"
                  % (t, tb_[t], lb[t], la[t], la[t] - lb[t], mb[t], ma[t], ma[t] - mb[t]))

        print("\n-- ЛЮДИ ЦЕЛИКОМ (сведение форм PER по ключу фамилии) --")
        pb, wb, _ = person_stats(before, only_clean)
        pa, wa, open_a = person_stats(after, only_clean)
        print("  людей в корпусе: %d -> %d" % (pb, pa))
        print("  защищены ЦЕЛИКОМ (ни одна форма не утекла): %d (%s) -> %d (%s)"
              % (wb, pct(wb, pb), wa, pct(wa, pa)))

        print("\n-- ЛОЖНЫЕ СРАБАТЫВАНИЯ (цена правки) --")
        sb, ib = fp_stats(before, only_clean)
        sa, ia = fp_stats(after, only_clean)
        for kind in ("on-nothing", "on-negative", "cross-type"):
            b = sum(v for k, v in sb.items() if k[0] == kind)
            a = sum(v for k, v in sa.items() if k[0] == kind)
            print("  %-12s %5d -> %5d (%+d)" % (kind, b, a, a - b))
        print("  по типам, где изменилось:")
        for k in sorted(set(sb) | set(sa)):
            if sb[k] != sa[k]:
                print("     %-12s %-16s %5d -> %5d (%+d)"
                      % (k[0], k[1], sb[k], sa[k], sa[k] - sb[k]))
        if not only_clean:
            print("  ПОИМЁННО — новые маски on-nothing / on-negative:")
            for k in sorted(set(ia) | set(ib)):
                if ia[k] > ib[k]:
                    print("     +%-3d %-12s %-14s %r"
                          % (ia[k] - ib[k], k[0], k[1], k[2]))
            print("  ПОИМЁННО — ушедшие:")
            for k in sorted(set(ia) | set(ib)):
                if ia[k] < ib[k]:
                    print("     -%-3d %-12s %-14s %r"
                          % (ib[k] - ia[k], k[0], k[1], k[2]))
        print()


if __name__ == "__main__":
    main()
