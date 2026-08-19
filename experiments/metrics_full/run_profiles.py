# -*- coding: utf-8 -*-
"""run_profiles.py — ЗАМЕР ПО ЧЕТЫРЁМ НАБОРАМ ТИПОВ (этап METRICS-FULL).

    venv/Scripts/python.exe experiments/metrics_full/run_profiles.py [--workers N] [--limit N]

ЗАЧЕМ ОТДЕЛЬНЫЙ ПРИБОР. Штатный замер (`tests/corpus/run_measurement.py`) кладёт
маски ДВАЖДЫ — набором по умолчанию и «Максимумом» — и из первой раскладки берёт
ровно одно число: линию «з» (что закрыто на максимуме и открыто по умолчанию).
Полнота, утечка и число масок печатаются ТОЛЬКО для «Максимума». Пользователь же
работает на одном из ЧЕТЫРЁХ наборов (`src/type_policy.py`), и по наборам продукт
не измерялся систематически ни разу — раздел 7 задания METRICS-FULL.

ЧТО СЧИТАЕТСЯ. Одна детекция и одно разрешение пересечений на документ (дорогая
часть), поверх — ЧЕТЫРЕ дешёвых `apply_masking`. По каждому набору: число масок,
полнота (эталонная сущность закрыта маской СВОЕГО типа), утечка (та же метрика
`leak_v2`, что у гейта, порог >= 6) и поимённо — какие эталонные сущности каких
типов остались ОТКРЫТЫМИ (ни один символ не закрыт ни одной маской набора).

ЧЕГО ЭТОТ ПРИБОР НЕ ДЕЛАЕТ. Он ничего не сравнивает с точкой отсчёта и ничем не
управляет: это замер, а не гейт. Границы масок (`masking B/C`), точность и
over-mask здесь не считаются — набор их не двигает (фильтр типов стоит ПОСЛЕ
разрешения пересечений, `src/type_policy.py`), а считать их четырежды значило бы
печатать одно и то же число четыре раза.

ПОРЯДОК НАБОРОВ ВАЖЕН: `apply_masking` проставляет `token` на самих объектах
`Entity`, поэтому «Максимум» идёт последним — ровно как в штатном замере.
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

import measure_lib as ML          # noqa: E402
import run_measurement as RM      # noqa: E402
import type_policy                # noqa: E402
from extractor import extract     # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import resolve_for_masking, apply_masking  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(CORPUS, "docs")
OUT = os.path.join(HERE, "results_profiles.json")

#: Порядок — от узкого к широкому, «Максимум» ПОСЛЕДНИМ (см. докстринг).
PROFILES = ("personal", "personal_requisites", "with_money", "maximum")


def _profile_types(name):
    return type_policy.profile_types(name) if hasattr(type_policy, "profile_types") \
        else type_policy._PROFILE_TYPES[name]


def process_doc(d):
    doc_id = d["doc_id"]
    path = os.path.join(DOCS, doc_id + "." + d["format"])
    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)

    doc = extract(path)
    entities = run_detection(doc, CONFIG)
    resolved = resolve_for_masking(doc, entities, CONFIG)
    offs, _ = ML.build_segment_offsets(doc, G, body_start,
                                       regions=ML.part_regions(path, G))

    out = {"doc_id": doc_id, "format": d["format"],
           "contract_type": d.get("contract_type", ""),
           "n_entities": len(d["entities"]), "profiles": {}}

    for prof in PROFILES:
        anon, kept = apply_masking(doc, resolved, CONFIG, _profile_types(prof))
        det = ML.map_entities_to_pt1(kept, offs, G)
        located = [x for x in det if x["start"] is not None]
        spans = [(x["start"], x["end"]) for x in located]

        anon_norm_v2 = ML.v2_norm_text(anon)
        anon_digits = ML.v2_digit_runs(anon)
        anon_dates = ML.v2_date_field(anon)
        anon_runs = ML.v2_digit_runs_pos(anon)

        per_type = {}
        open_entities = []
        for e in d["entities"]:
            t = e["type"]
            s = per_type.setdefault(t, {"n": 0, "found": 0, "leak6": 0, "leak8": 0,
                                        "open_full": 0})
            s["n"] += 1
            found = any(x["gtype"] == t and
                        max(e["start"], x["start"]) < min(e["end"], x["end"])
                        for x in located)
            s["found"] += int(found)
            v2 = RM.leak_v2(t, e["text"], anon_norm_v2, anon_digits, anon_dates,
                            anon_runs=anon_runs)
            hit6, hit8 = ML._leak_v2_hits(t, v2)
            s["leak6"] += int(hit6)
            s["leak8"] += int(hit8)
            # ОТКРЫТА = ни один символ эталонного спана не закрыт НИ ОДНОЙ
            # маской этого набора (маска чужого типа тоже считается защитой —
            # значение в LLM не уходит, см. §3аа STATE).
            unc = ML.uncovered_chars(e["start"], e["end"], spans)
            if unc == (e["end"] - e["start"]):
                s["open_full"] += 1
                open_entities.append({"type": t, "start": e["start"], "end": e["end"]})

        out["profiles"][prof] = {
            "n_masks": len(kept),
            "n_masks_located": len(located),
            "per_type": per_type,
            "open_entities": open_entities,
        }
    return out


def _safe(d):
    try:
        return process_doc(d)
    except Exception as exc:  # тот же перехват, что у штатного замера
        import traceback
        traceback.print_exc()
        return {"doc_id": d["doc_id"], "crashed": repr(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    gold = RM.load_gold()
    if args.limit:
        gold = gold[:args.limit]
    workers = args.workers if args.workers is not None else RM._default_workers()
    print("Замер по наборам: %d документов, %d набора(ов), workers=%d"
          % (len(gold), len(PROFILES), workers), flush=True)

    t0 = time.time()
    results = []
    if workers <= 1:
        for i, d in enumerate(gold):
            results.append(_safe(d))
            if (i + 1) % 20 == 0:
                print("[%d/%d] %.0fs" % (i + 1, len(gold), time.time() - t0), flush=True)
    else:
        import multiprocessing
        with multiprocessing.Pool(workers) as pool:
            for i, rec in enumerate(pool.imap(_safe, gold)):
                results.append(rec)
                if (i + 1) % 20 == 0:
                    print("[%d/%d] %.0fs" % (i + 1, len(gold), time.time() - t0), flush=True)

    elapsed = time.time() - t0
    json.dump({"profiles": list(PROFILES), "elapsed_sec": round(elapsed, 1),
               "docs": results},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
    print("DONE %d docs in %.0fs -> %s" % (len(results), elapsed, args.out), flush=True)


if __name__ == "__main__":
    main()
