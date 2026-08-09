# -*- coding: utf-8 -*-
"""Кеш среза для итераций дерева: текст PT-1, маски конвейера, эталон, негативы.

Гоняется ОДИН раз (детекция дорогая), дальше дерево крутится по кешу.
"""
import json, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "corpus"))

import measure_lib as ML                      # noqa: E402
from extractor import extract                 # noqa: E402
from pipeline import run_detection            # noqa: E402
from tokenizer import tokenize                # noqa: E402
import type_policy                            # noqa: E402

CONFIG = str(ROOT / "entity_types.yaml")
DOCS = ROOT / "tests" / "corpus" / "docs"


def main():
    subset = set(json.load(open(ROOT / "tests/corpus/subset_iter.json", encoding="utf-8"))["doc_ids"])
    gold = [g for g in json.load(open(ROOT / "tests/corpus/gold.json", encoding="utf-8"))
            if g["doc_id"] in subset]
    dump = []
    for i, d in enumerate(gold):
        path = str(DOCS / (d["doc_id"] + "." + d["format"]))
        G = ML.pt1_text(path)
        body_start = ML.body_offset(path, G)
        doc = extract(path)
        ents = run_detection(doc, CONFIG)
        _, kept = tokenize(doc, ents, CONFIG, enabled_types=type_policy.MAXIMUM)
        offs, _ = ML.build_segment_offsets(doc, G, body_start)
        det = ML.map_entities_to_pt1(kept, offs, G)
        dump.append({
            "doc_id": d["doc_id"], "text": G,
            "masks": [{"t": x["gtype"], "s": x["start"], "e": x["end"]}
                      for x in det if x["start"] is not None],
            "gold": [{"t": e["type"], "s": e["start"], "e": e["end"], "text": e["text"]}
                     for e in d["entities"]],
            "negatives": d.get("negatives", []), "ignore": d.get("ignore", []),
        })
        print("  %2d/%d %s" % (i + 1, len(gold), d["doc_id"]), file=sys.stderr)
    json.dump(dump, open(HERE / "subset_dump.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("docs", len(dump))


if __name__ == "__main__":
    main()
