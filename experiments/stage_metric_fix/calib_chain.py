# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 1 — КАЛИБРОВКА правила «восстановимость».

Первый заход (calib_fragments.py) считал СУММУ открытых кусков и показал, почему
голая сумма не годится: с полом куска 2 «утечек» становится +753, и это шум —
трёхзначный run где угодно в документе совпадает с каким-нибудь окном
20-значного счёта по чистой случайности.

Здесь проверяется правило, которое ближе к сути: значение считается открытым,
если его куски дожили ПОДРЯД — соседними цифровыми группами анонимного текста,
в порядке ядра, разделёнными только швом вёрстки. Это ровно то, что делает
читатель: видит «704\\n-068» и собирает глазами.

Скрипт ничего не чинит: пишет chain_calib.json (длины и цифровые куски, без
текстов ПДн) для выбора двух чисел — предела зазора между кусками и пола длины.
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
OUT = os.path.join(HERE, "chain_calib.json")

GAP_PROBE = 40  # предел зазора ПРИ СБОРЕ данных, заведомо щедрый


def runs_with_pos(text):
    """[(цифровой run, start, end)] в координатах ИСХОДНОГО текста.

    Свод «буква-на-месте-цифры» и снятие невидимых — те же, что у v2_digit_runs,
    но здесь важны позиции, поэтому нормализация посимвольная и длину не меняет.
    Пробел/дефис МЕЖДУ цифрами склеиваются внутрь run-а — как в v2_digit_runs.
    """
    out = []
    cur = []
    start = None
    pend = None  # конец последней цифры
    for i, ch in enumerate(text):
        if ord(ch) in ML._V2_INVIS:
            continue
        c = ML._V2_LETTER2DIGIT.get(ch, ch)
        if c.isdigit():
            if start is None:
                start = i
            cur.append(c)
            pend = i + 1
        elif cur and ch in (" ", "-"):
            continue  # разделитель внутри числа
        else:
            if cur:
                out.append(("".join(cur), start, pend))
                cur, start, pend = [], None, None
    if cur:
        out.append(("".join(cur), start, pend))
    return out


def best_chain_floor(core, runs, gap_max, floor):
    """То же, что best_chain, но КАЖДЫЙ кусок цепочки не короче `floor`.

    Пол обязателен: без него цепочка собирается из одиночных нулей, разбросанных
    по документу («0»,«0»,«0»…), — это не остаток значения, а совпадение."""
    best = (0, [], [])
    idx = [k for k, r in enumerate(runs) if len(r[0]) >= floor and r[0] in core]
    for i in idx:
        acc = runs[i][0]
        gaps, parts = [], [runs[i][0]]
        if len(acc) > best[0]:
            best = (len(acc), list(parts), list(gaps))
        j = i + 1
        while j < len(runs):
            if len(runs[j][0]) < floor:
                break
            gap = runs[j][1] - runs[j - 1][2]
            if gap > gap_max:
                break
            nxt = acc + runs[j][0]
            if nxt not in core:
                break
            acc = nxt
            gaps.append(gap)
            parts.append(runs[j][0])
            if len(acc) > best[0]:
                best = (len(acc), list(parts), list(gaps))
            j += 1
    return best


def best_chain(core, runs, gap_max):
    """Самая длинная цепочка соседних run-ов, складывающаяся в кусок ядра.

    Каждый run цепочки обязан быть подстрокой ядра, конкатенация цепочки — тоже
    подстрокой ядра (то есть куски идут в порядке значения и стыкуются без
    пропусков), зазор между соседями в тексте <= gap_max символов.
    Возвращает (длина собранного, [куски], [зазоры])."""
    best = (0, [], [])
    n = len(runs)
    for i in range(n):
        if runs[i][0] not in core:
            continue
        acc = runs[i][0]
        gaps = []
        parts = [runs[i][0]]
        if len(acc) > best[0]:
            best = (len(acc), list(parts), list(gaps))
        j = i + 1
        while j < n:
            gap = runs[j][1] - runs[j - 1][2]
            if gap > gap_max:
                break
            nxt = acc + runs[j][0]
            if nxt not in core:
                break
            acc = nxt
            gaps.append(gap)
            parts.append(runs[j][0])
            if len(acc) > best[0]:
                best = (len(acc), list(parts), list(gaps))
            j += 1
    return best


def worker(d):
    path = os.path.join(DOCS, d["doc_id"] + "." + d["format"])
    doc = extract(path)
    entities = run_detection(doc, CONFIG)
    anon, kept = tokenize(doc, entities, CONFIG, enabled_types=type_policy.MAXIMUM)
    field = ML.v2_digit_runs(anon)
    runs = runs_with_pos(anon)
    out = []
    for e in d["entities"]:
        t = e["type"]
        if t not in RM.V2_NUMERIC_TYPES:
            continue
        core = ML.v2_core_digits(e["text"])
        if not core:
            continue
        old = ML.longest_surviving_window(core, field)
        total, parts, gaps = best_chain(core, runs, GAP_PROBE)
        grid = {}
        for floor in (2, 3, 4):
            for gap in (1, 2, 3, 5, 10, 20):
                tt, pp, gg = best_chain_floor(core, runs, gap, floor)
                grid[f"{floor}|{gap}"] = [tt, pp, gg]
        out.append({
            "doc_id": d["doc_id"], "type": t, "core_len": len(core),
            "old_window": len(old),
            "chain_total": total, "parts": parts, "gaps": gaps,
            "grid": grid,
        })
    return out


def main():
    gold = RM.load_gold()
    t0 = time.time()
    import multiprocessing
    recs = []
    with multiprocessing.Pool(RM._default_workers()) as pool:
        for i, part in enumerate(pool.imap(worker, gold)):
            recs.extend(part)
            if (i + 1) % 100 == 0:
                print(f"[{i+1}/{len(gold)}] {time.time()-t0:.0f}s", flush=True)
    json.dump(recs, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {len(recs)} -> {OUT}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
