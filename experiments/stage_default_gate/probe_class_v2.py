# -*- coding: utf-8 -*-
"""ЭТАП DEFAULT-GATE — тот же класс на корпусе v2 (полный ответ на «сколько ещё»).

Ответ только по корпусу v1 был бы половиной ответа: у v2 другой состав типов и
другие конструкции. Считается ровно то же, что линией «з»: эталонная сущность
типа, ВХОДЯЩЕГО в набор по умолчанию, закрытая на МАКСИМУМЕ и открытая при
настройках по умолчанию.
"""
import os
import sys
import time
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus_v2"))

import measure_lib as ML  # noqa: E402
import measure_v2 as MV  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import resolve_for_masking, apply_masking  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")


def worker(d):
    path = os.path.join(MV.DOCS, d["doc_id"] + "." + d["format"])
    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)
    doc = extract(path)
    ents = run_detection(doc, CONFIG)
    offs, _ = ML.build_segment_offsets(doc, G, body_start,
                                       regions=ML.part_regions(path, G))
    resolved = resolve_for_masking(doc, ents, CONFIG)
    spans = {}
    for label, types in (("max", type_policy.MAXIMUM),
                         ("default", type_policy.PERSONAL)):
        _anon, kept = apply_masking(doc, resolved, CONFIG, types)
        det = ML.map_entities_to_pt1(kept, offs, G)
        spans[label] = [(x["start"], x["end"]) for x in det if x["start"] is not None]
    out = []
    for e in d["entities"]:
        gt = MV.gold_type(e["type"])
        if gt not in ML.DEFAULT_PROFILE_GOLD_TYPES:
            continue
        u_max = ML.uncovered_chars(e["start"], e["end"], spans["max"])
        u_def = ML.uncovered_chars(e["start"], e["end"], spans["default"])
        if u_def > u_max:
            out.append({"doc_id": d["doc_id"], "type": gt,
                        "uncovered_max": u_max, "uncovered_default": u_def})
    return out


def main():
    gold = MV.load_gold()
    t0 = time.time()
    import multiprocessing
    recs = []
    n_relevant = 0
    for d in gold:
        n_relevant += sum(1 for e in d["entities"]
                          if MV.gold_type(e["type"]) in ML.DEFAULT_PROFILE_GOLD_TYPES)
    with multiprocessing.Pool(4) as pool:
        for i, part in enumerate(pool.imap(worker, gold)):
            recs.extend(part)
            if (i + 1) % 50 == 0:
                print(f"[{i+1}/{len(gold)}] {time.time()-t0:.0f}s", flush=True)
    print(f"\nкорпус v2: эталонных сущностей типов НАБОРА ПО УМОЛЧАНИЮ: {n_relevant}")
    print(f"КЛАСС «видно только на дефолтном наборе»: {len(recs)}")
    print("  по типам:", dict(collections.Counter(r["type"] for r in recs)))
    print(f"  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
