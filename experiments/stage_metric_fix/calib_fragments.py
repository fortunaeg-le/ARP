# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 1 — КАЛИБРОВКА порога куска (AUD1-LEAK-SUBTHRESHOLD).

Прибор считает утечку числового реквизита по САМОМУ ДЛИННОМУ выжившему окну.
Значение, раздробленное вёрсткой на куски короче мягкого порога (6), получает
вердикт «утечки нет», хотя открыто целиком: «704\\n-068» — читатель собирает
паспорт глазами.

Скрипт НИЧЕГО не чинит и ничего не решает. Он один раз гоняет корпус и
записывает для КАЖДОЙ числовой эталонной сущности:
  * ядро (длину), самое длинное выжившее окно (как сейчас меряет прибор),
  * ВСЕ выжившие куски, собранные без наложения (жадно, длинные вперёд),
    с полом длины куска 2 — чтобы порог пола можно было выбрать ПОСЛЕ, по
    данным, а не назначить заранее.
Дамп: frag_calib.json.  Выбор порога — отдельным разбором (report_calib.py).

Текстов ПДн в дамп не кладём (публичный репозиторий): только длины, тип,
идентификатор документа и сами цифровые куски — они и есть предмет спора,
но это цифры СИНТЕТИЧЕСКОГО корпуса, не реального документа.
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
OUT = os.path.join(HERE, "frag_calib.json")

FLOOR = 2  # пол длины куска при СБОРЕ данных; выбор рабочего пола — после


def _placed_fragments(core, field, floor):
    """Куски ядра, дожившие в цифровом поле, разложенные по ядру без наложения.

    Правило приёма куска — ТО ЖЕ, что у действующей метрики
    (`ML.longest_surviving_window`): кусок засчитывается, только если run
    анонимного текста, в котором он найден, сам является остатком значения
    (покрытие >= RUN_COVERAGE_MIN) либо ядро дожило целиком. Иначе короткий
    кусок ловил бы случайные совпадения в чужих числах."""
    cands = []
    for run in field.split():
        w = ML._lcs_with_run(core, run)
        if not w or len(w) < floor:
            continue
        if not (w == core or len(w) >= ML.RUN_COVERAGE_MIN * len(run)):
            continue
        cands.append(w)
    cands.sort(key=lambda w: (-len(w), w))
    covered = [False] * len(core)
    placed = []
    for w in cands:
        L = len(w)
        for i in range(len(core) - L + 1):
            if core[i:i + L] == w and not any(covered[i:i + L]):
                covered[i:i + L] = [True] * L
                placed.append(w)
                break
    return placed


def worker(d):
    path = os.path.join(DOCS, d["doc_id"] + "." + d["format"])
    doc = extract(path)
    entities = run_detection(doc, CONFIG)
    anon, kept = tokenize(doc, entities, CONFIG, enabled_types=type_policy.MAXIMUM)
    field = ML.v2_digit_runs(anon)
    date_field = ML.v2_date_field(anon)
    out = []
    for e in d["entities"]:
        t = e["type"]
        if t not in RM.V2_NUMERIC_TYPES:
            continue
        core = ML.v2_core_digits(e["text"])
        if not core:
            continue
        placed = _placed_fragments(core, field, FLOOR)
        longest = max((len(w) for w in placed), default=0)
        out.append({
            "doc_id": d["doc_id"], "type": t, "core_len": len(core),
            "longest": longest, "placed": sorted(placed, key=lambda w: -len(w)),
            "open_total": sum(len(w) for w in placed),
            # контроль: видит ли дату отдельное поле (для сверки, не для порога)
            "in_date_field": core in date_field,
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
            if (i + 1) % 50 == 0:
                print(f"[{i+1}/{len(gold)}] {time.time()-t0:.0f}s", flush=True)
    json.dump(recs, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {len(recs)} zapisey -> {OUT}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
