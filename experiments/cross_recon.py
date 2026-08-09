# -*- coding: utf-8 -*-
"""CROSSTYPE, часть 1 — разбор 6 случаев линии «з» ПОИМЁННО.

Для каждого случая из tests/corpus/known_default_leaks.json печатает:
  * текст эталонного значения и его окрестность (корпус v1 синтетический —
    реальных ПДн в нём нет, дамп остаётся в experiments/);
  * какие спаны детекции его накрывают на МАКСИМУМЕ и какого они типа;
  * какие накрывают при наборе ПО УМОЛЧАНИЮ.
Правок не делает. Повторяет раскладку run_measurement.process_doc.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import resolve_for_masking, apply_masking  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
GOLD = os.path.join(ROOT, "tests", "corpus", "gold.json")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
REG = os.path.join(ROOT, "tests", "corpus", "known_default_leaks.json")


def load_gold():
    g = json.load(open(GOLD, encoding="utf-8"))
    docs = g["documents"] if isinstance(g, dict) else g
    return g, {d["doc_id"]: d for d in docs}


def main():
    gold, by_id = load_gold()
    reg = json.load(open(REG, encoding="utf-8"))

    for case in reg["cases"]:
        d = by_id[case["doc_id"]]
        path = os.path.join(DOCS, d["doc_id"] + "." + d["format"])
        G = ML.pt1_text(path)
        body_start = ML.body_offset(path, G)
        doc = extract(path)
        ents = run_detection(doc, CONFIG)
        offs, _ = ML.build_segment_offsets(
            doc, G, body_start, regions=ML.part_regions(path, G))
        resolved = resolve_for_masking(doc, ents, CONFIG)
        _, kept_def = apply_masking(doc, resolved, CONFIG, type_policy.PERSONAL)
        _, kept_max = apply_masking(doc, resolved, CONFIG, type_policy.MAXIMUM)
        m_max = ML.map_entities_to_pt1(kept_max, offs, G)
        m_def = ML.map_entities_to_pt1(kept_def, offs, G)

        gs, ge = case["start"], case["end"]
        print("=" * 78)
        print("%s  %s [%d:%d] len=%d  u_max=%d u_def=%d"
              % (case["doc_id"], case["type"], gs, ge, case["len"],
                 case["uncovered_max"], case["uncovered_default"]))
        print("  ЗНАЧЕНИЕ: %r" % G[gs:ge])
        print("  ОКРЕСТНОСТЬ: %r" % G[max(0, gs - 120):ge + 60])
        for name, mm in (("MAX", m_max), ("DEFAULT", m_def)):
            print("  -- %s --" % name)
            for x in mm:
                if x["start"] is None:
                    continue
                if x["start"] < ge and x["end"] > gs:
                    print("     %-10s (raw %-10s det=%-10s) [%d:%d] %r"
                          % (x["gtype"], x["raw_type"], x.get("detector"),
                             x["start"], x["end"], x["text"]))


if __name__ == "__main__":
    main()
