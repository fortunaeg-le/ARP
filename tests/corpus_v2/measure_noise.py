# -*- coding: utf-8 -*-
"""measure_noise.py — ЗАМЕР «ЧИСТЫЙ ПРОТИВ ГРЯЗНОГО».

Прогоняет парный шумовой корпус (`generate_noise.py`) продуктовой детекцией и
показывает, сколько мы теряем на тексте, испорченном распознаванием, — в
разрезе по КЛАССАМ ИСКАЖЕНИЯ и по ВИДАМ ДАННЫХ.

ПРИБОР НЕ СВОЙ. Детекция и все метрики берутся у `measure_v2` (а тот — у
`tests/corpus/measure_lib.py` и `pipeline.run_detection`): второй копии
измерителя в проекте быть не должно. Здесь только подмена каталога документов
и сведение пар.

СРАВНЕНИЕ ПОШТУЧНОЕ, НЕ ПОСРЕДНЕЕ. Каждая величина грязного документа
сопоставляется с ТОЙ ЖЕ величиной чистого двойника по ключу `pair`. Поэтому
«потеряно 12» означает ровно двенадцать вхождений, которые находились до
искажения и перестали находиться после, а не разницу двух процентов.

КРАСНАЯ ЛИНИЯ (README корпуса v2): цифры на порождённом корпусе — ВЕРХНЯЯ
ГРАНИЦА, а не измерение. Класс искажения, который генератор не предусмотрел,
здесь не появится, и его отсутствие будет невидимым.

Запуск:
    venv/Scripts/python.exe tests/corpus_v2/measure_noise.py
    venv/Scripts/python.exe tests/corpus_v2/measure_noise.py --results <файл>
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_v2 as M2                                        # noqa: E402
import noise_lib as NL                                         # noqa: E402

NOISE_ROOT = os.path.join(HERE, "noise")
GOLD_NOISE = os.path.join(NOISE_ROOT, "gold_noise.json")
OUT = os.path.join(NOISE_ROOT, "results_noise.json")

CEILING = ("  цифры на порождённом корпусе — верхняя граница, не измерение "
           "(tests/corpus_v2/README.md, «КРАСНАЯ ЛИНИЯ»)")


def load_gold():
    g = json.load(open(GOLD_NOISE, encoding="utf-8"))
    g.sort(key=lambda d: d["doc_id"])
    return g


def run(gold):
    # Подмена каталога документов — единственная правка чужого измерителя.
    M2.DOCS = os.path.join(NOISE_ROOT, "docs")
    results = []
    t0 = time.time()
    for i, d in enumerate(gold):
        try:
            rec = M2.process_doc(d)
        except Exception as exc:                               # noqa: BLE001
            import traceback
            traceback.print_exc()
            rec = {"outcome": "crashed", "doc_id": d["doc_id"], "error": repr(exc)}
        # Разметку носит эталон, а не запись прогона: переносим поля оси
        # искажений в запись, чтобы отчёт считался по одному файлу.
        if rec.get("outcome") == "processed":
            meta = [{"noise": e.get("noise"), "clean": e.get("clean"),
                     "pair": e.get("pair")} for e in d["entities"]]
            for r, m in zip(rec["entities"], meta):
                r.update(m)
            rec["dirty"] = d["doc_id"].startswith("nzd_")
        results.append(rec)
        if (i + 1) % 10 == 0 or i == 0:
            print("[%d/%d] %s  %.0fs" % (i + 1, len(gold), d["doc_id"],
                                         time.time() - t0), flush=True)
    return results


# --------------------------------------------------------------------------- #
#                                   ОТЧЁТ                                      #
# --------------------------------------------------------------------------- #
def pair_index(results):
    """{pair: {'clean': запись, 'dirty': запись}} — сведение двойников."""
    idx = {}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        side = "dirty" if r.get("dirty") else "clean"
        for e in r["entities"]:
            if not e.get("pair"):
                continue
            idx.setdefault(e["pair"], {})[side] = e
    return idx


def rows(idx, key):
    """{ключ: счётчики} — свод по классу искажения или по виду данных."""
    out = {}
    for p in idx.values():
        c, d = p.get("clean"), p.get("dirty")
        if not c or not d:
            continue                       # непарное вхождение в свод не идёт
        k = key(c, d)
        if k is None:
            continue
        s = out.setdefault(k, {"n": 0, "cl_found": 0, "d_found": 0, "lost": 0,
                               "gained": 0, "cl_leak": 0, "d_leak": 0,
                               "cl_exact": 0, "d_exact": 0})
        s["n"] += 1
        s["cl_found"] += int(c["found"])
        s["d_found"] += int(d["found"])
        s["cl_exact"] += int(c["exact"])
        s["d_exact"] += int(d["exact"])
        s["cl_leak"] += int(c["leak_pos"]["status"] != "none")
        s["d_leak"] += int(d["leak_pos"]["status"] != "none")
        if c["found"] and not d["found"]:
            s["lost"] += 1
        if d["found"] and not c["found"]:
            s["gained"] += 1
    return out


def print_table(title, tab, order=None, extra=None):
    print("\n=== %s ===" % title)
    print(CEILING)
    head = ("  %-17s %5s %9s %9s %7s %7s %8s %8s" %
            ("", "n", "чист.", "грязн.", "потер.", "верн.", "теч.чист", "теч.гряз"))
    print(head)
    print("  " + "-" * (len(head) - 2))
    keys = [k for k in (order or sorted(tab)) if k in tab]
    keys += [k for k in sorted(tab) if k not in keys]
    for k in keys:
        s = tab[k]
        note = (" %s" % extra[k]) if extra and k in extra else ""
        print("  %-17s %5d %8.1f%% %8.1f%% %7d %7d %7.1f%% %7.1f%%%s"
              % (k, s["n"],
                 100.0 * s["cl_found"] / s["n"], 100.0 * s["d_found"] / s["n"],
                 s["lost"], s["gained"],
                 100.0 * s["cl_leak"] / s["n"], 100.0 * s["d_leak"] / s["n"], note))
    tot = {kk: sum(s[kk] for s in tab.values())
           for kk in ("n", "cl_found", "d_found", "lost", "gained", "cl_leak", "d_leak")}
    if tot["n"]:
        print("  %-17s %5d %8.1f%% %8.1f%% %7d %7d %7.1f%% %7.1f%%"
              % ("ВСЕГО", tot["n"],
                 100.0 * tot["cl_found"] / tot["n"], 100.0 * tot["d_found"] / tot["n"],
                 tot["lost"], tot["gained"],
                 100.0 * tot["cl_leak"] / tot["n"], 100.0 * tot["d_leak"] / tot["n"]))
    print("  чист./грязн. = полнота (найдена маска своего вида) до и после искажения;")
    print("  потер. = находилось и перестало; верн. = не находилось и нашлось;")
    print("  теч. = утечка: цифры значения, не закрытые НИ ОДНОЙ маской.")


def report(results):
    idx = pair_index(results)
    classes = NL.load_noise_classes()
    order = [c["id"] for c in classes]
    ev = {c["id"]: c["evidence"] for c in classes}
    by_class = rows(idx, lambda c, d: d.get("noise") or "(не искажено)")
    print_table("ПОТЕРИ ПО КЛАССАМ ИСКАЖЕНИЯ", by_class, order + ["(не искажено)"],
                extra=ev)
    by_type = rows(idx, lambda c, d: d["type"] if d.get("noise") else None)
    print_table("ПОТЕРИ ПО ВИДАМ ДАННЫХ (только искажённые вхождения)", by_type)

    # Ложные срабатывания: искажение не только теряет величину, но и создаёт
    # маску на пустом месте (склейка соседей даёт «двадцатизначный счёт»).
    fp = {"clean": 0, "dirty": 0}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        fp["dirty" if r.get("dirty") else "clean"] += len(r["false_positives"])
    print("\n=== ЛОЖНЫЕ СРАБАТЫВАНИЯ (маска не на эталоне) ===")
    print("  чистые документы: %d | грязные: %d | прирост: %+d"
          % (fp["clean"], fp["dirty"], fp["dirty"] - fp["clean"]))
    lonely = sum(1 for p in idx.values() if len(p) != 2)
    if lonely:
        print("\n  НЕПАРНЫХ ВХОЖДЕНИЙ (в свод не вошли): %d" % lonely)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=None, help="отчёт по готовому дампу")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    if args.results:
        results = json.load(open(args.results, encoding="utf-8"))
    else:
        gold = load_gold()
        print("Прогон шумового корпуса: %d документов…" % len(gold))
        t0 = time.time()
        results = run(gold)
        json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
        print("DONE %d docs in %.0fs -> %s" % (len(results), time.time() - t0, args.out))
    report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
