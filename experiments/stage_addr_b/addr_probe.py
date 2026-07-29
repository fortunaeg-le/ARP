# -*- coding: utf-8 -*-
"""
addr_probe.py — измеритель ГРАНИЦ адресных масок по НАПРАВЛЕНИЮ ошибки (этап ADDR-B).

ЗАЧЕМ ОТДЕЛЬНЫЙ ИЗМЕРИТЕЛЬ. Штатные метрики замера дают по границам ОДНО число
на сторону и обе стороны путают:
  * masking B («эталон закрыт масками целиком») видит ТОЛЬКО недобор — маска шире
    эталона по B не нарушение (см. measure_lib.mc_check_bc, docstring);
  * поле `exact` в results_*.json — булево «ровно», из него не видно, в какую
    сторону промах;
  * поля left_trim/right_trim считаются по ХАЛЛУ масок СВОЕГО типа и молчат о
    случае «слева уехал внутрь, справа вылез наружу».
Правка границ легко улучшает одну сторону за счёт другой, поэтому этап меряет две
цены раздельно, всегда:
  НЕДОБОР (under)  — символы эталонного адреса, НЕ закрытые НИ ОДНОЙ маской
                     (любого типа: соседний PER/ORG-токен адрес тоже прячет).
                     Это утечка ПДн — дорогая ошибка.
  ПЕРЕБОР (over)   — символы ADDRESS-масок, ЛЕЖАЩИЕ ВНЕ своего эталона. Документ
                     хуже читается, данные защищены — дешёвая ошибка.

СОПОСТАВЛЕНИЕ МАСКА->ЭТАЛОН — то же правило, что у measure_lib.mc_check_bc:
маска приписывается эталонной сущности с НАИБОЛЬШИМ пересечением. Маска, не
пересёкшая ни одного эталонного адреса, — ложное срабатывание, в границы не идёт
(её меряет precision, линия «а» гейта).

Запуск (полный корпус, ~10 мин):
    venv/Scripts/python.exe experiments/stage_addr_b/addr_probe.py --out before.json
Быстрый набор (25 док. tests/corpus/subset_iter.json, для итераций внутри этапа):
    venv/Scripts/python.exe experiments/stage_addr_b/addr_probe.py --subset --out x.json

Корпус НЕ трогает, в ~/.shifrator не пишет (хранилище — временная директория
run_measurement.STORAGE).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, CORPUS)

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import type_policy  # noqa: E402

TYPE = "ADDRESS"


def _ov(a0, a1, b0, b1):
    return max(a0, b0) < min(a1, b1)


def _union_len(ivs, lo, hi):
    """Длина объединения интервалов, пересечённого с [lo,hi)."""
    cut = sorted((max(s, lo), min(e, hi)) for s, e in ivs if _ov(s, e, lo, hi))
    total, cur = 0, lo - 1
    end = lo - 1
    for s, e in cut:
        if s > end:
            total += end - cur if end > cur else 0
            cur, end = s, e
        else:
            end = max(end, e)
    total += end - cur if end > cur else 0
    return total


def _uncovered(gs, ge, ivs):
    """Куски [gs,ge), НЕ покрытые ни одним интервалом ivs -> список (s,e)."""
    out = []
    cur = gs
    for s, e in sorted((s, e) for s, e in ivs if _ov(s, e, gs, ge)):
        if s > cur:
            out.append((cur, min(s, ge)))
        cur = max(cur, e)
        if cur >= ge:
            break
    if cur < ge:
        out.append((cur, ge))
    return [(s, e) for s, e in out if e > s]


def _outside(ms, me, gs, ge):
    """Куски маски [ms,me), лежащие ВНЕ эталона [gs,ge)."""
    out = []
    if ms < gs:
        out.append((ms, min(me, gs)))
    if me > ge:
        out.append((max(ms, ge), me))
    return [(s, e) for s, e in out if e > s]


def probe_doc(d):
    doc_id = d["doc_id"]
    path = os.path.join(RM.DOCS, doc_id + "." + d["format"])
    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)

    doc = extract(path)
    entities = run_detection(doc, RM.CONFIG)
    _, kept = tokenize(doc, entities, RM.CONFIG, enabled_types=type_policy.MAXIMUM)

    offs, _ = ML.build_segment_offsets(doc, G, body_start)
    det = [x for x in ML.map_entities_to_pt1(kept, offs, G) if x["start"] is not None]
    all_masks = [(x["start"], x["end"]) for x in det]
    addr_masks = [x for x in det if x["gtype"] == TYPE]

    golds = [e for e in d["entities"] if e["type"] == TYPE]

    # маска -> эталон с наибольшим пересечением (правило measure_lib.mc_check_bc)
    assigned = {i: [] for i in range(len(golds))}
    n_fp_masks = 0
    for m in addr_masks:
        ms, me = m["start"], m["end"]
        cand = [(i, g) for i, g in enumerate(golds) if _ov(ms, me, g["start"], g["end"])]
        if not cand:
            n_fp_masks += 1
            continue
        i, g = max(cand, key=lambda p: min(me, p[1]["end"]) - max(ms, p[1]["start"]))
        assigned[i].append((ms, me))

    recs = []
    for i, g in enumerate(golds):
        gs, ge = g["start"], g["end"]
        mine = assigned[i]
        found = bool(mine)
        holes = _uncovered(gs, ge, all_masks)          # НЕ закрыто НИ ОДНОЙ маской
        under = sum(e - s for s, e in holes)
        extra = []
        for ms, me in mine:
            extra += _outside(ms, me, gs, ge)
        over = sum(e - s for s, e in extra)
        if not found:
            direction = "not_found"
        elif under == 0 and over == 0:
            direction = "exact"
        elif under == 0:
            direction = "longer"
        elif over == 0:
            direction = "shorter"
        else:
            direction = "both"
        # на что лёг перебор: другая эталонная сущность или свободный текст
        other_gold = [(e["start"], e["end"]) for e in d["entities"]
                      if e["type"] != TYPE]
        over_on_gold = sum(_union_len(other_gold, s, e) for s, e in extra)
        recs.append({
            "doc_id": doc_id, "source": d["source"], "category": g.get("category", ""),
            "trick": g.get("trick"), "gold_start": gs, "gold_end": ge,
            "gold_text": g["text"], "direction": direction,
            "under": under, "over": over,
            "over_on_other_gold": over_on_gold,
            "n_masks": len(mine),
            "mask_texts": [G[s:e] for s, e in sorted(mine)],
            "mask_spans": sorted(mine),
            "open_pieces": [G[s:e] for s, e in holes],
            "extra_pieces": [G[s:e] for s, e in extra],
            "left_ctx": G[max(0, gs - 60):gs],
            "right_ctx": G[ge:ge + 60],
        })
    return {"doc_id": doc_id, "n_fp_masks": n_fp_masks, "entities": recs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--subset", action="store_true",
                    help="только 25 документов tests/corpus/subset_iter.json")
    a = ap.parse_args()

    gold = RM.load_gold()
    if a.subset:
        import subset_lib
        ids = set(subset_lib.load_subset_ids())
        gold = [d for d in gold if d["doc_id"] in ids]

    out, t0 = [], time.time()
    for i, d in enumerate(gold):
        out.append(probe_doc(d))
        if (i + 1) % 25 == 0 or i + 1 == len(gold):
            print(f"[{i+1}/{len(gold)}] {time.time()-t0:.0f}s", flush=True)
    path = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print("->", path)


if __name__ == "__main__":
    main()
