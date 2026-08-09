# -*- coding: utf-8 -*-
"""R-RDR часть 1, шаг 1: собрать ЖИВЫЕ случаи остатка на СРЕЗЕ (33 док.).

Гоняет тот же конвейер, что CLI/гейт (pipeline.run_detection + tokenize), кладёт
рядом эталонный спан, покрывающие маски и окно контекста. Ничего за пределы
experiments/research/rdr/ не пишет, src/ и корпус только читает.
"""
import json, os, sys, pathlib, tempfile

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


def covering(gs, ge, masks):
    """маски своего типа, пересекающие эталонный спан"""
    return [m for m in masks if m["start"] is not None and m["start"] < ge and m["end"] > gs]


def main():
    subset = set(json.load(open(ROOT / "tests/corpus/subset_iter.json", encoding="utf-8"))["doc_ids"])
    gold = [g for g in json.load(open(ROOT / "tests/corpus/gold.json", encoding="utf-8"))
            if g["doc_id"] in subset]
    cases = []
    for k, d in enumerate(gold):
        doc_id = d["doc_id"]
        path = str(DOCS / (doc_id + "." + d["format"]))
        G = ML.pt1_text(path)
        body_start = ML.body_offset(path, G)
        doc = extract(path)
        entities = run_detection(doc, CONFIG)
        _, kept = tokenize(doc, entities, CONFIG, enabled_types=type_policy.MAXIMUM)
        offs, _ = ML.build_segment_offsets(doc, G, body_start)
        det = ML.map_entities_to_pt1(kept, offs, G)
        located = [x for x in det if x["start"] is not None]
        for e in d["entities"]:
            gs, ge, gt = e["start"], e["end"], e["type"]
            same = [m for m in located if m["gtype"] == gt]
            cov = covering(gs, ge, same)
            any_cov = covering(gs, ge, located)
            if cov:
                lo = min(m["start"] for m in cov); hi = max(m["end"] for m in cov)
                under = max(0, gs - lo if False else 0)
                # непокрытые символы эталона
                covered = set()
                for m in cov:
                    covered |= set(range(max(m["start"], gs), min(m["end"], ge)))
                n_under = (ge - gs) - len(covered)
                n_over = sum(len(set(range(m["start"], m["end"])) - set(range(gs, ge))) for m in cov)
                if n_under == 0 and n_over == 0:
                    kind = "ok"
                elif n_under and not n_over:
                    kind = "недобор"
                elif n_over and not n_under:
                    kind = "перебор"
                else:
                    kind = "сдвиг"
                span = (lo, hi)
            else:
                kind = "чужой_тип" if any_cov else "не_найдено"
                n_under, n_over, span = ge - gs, 0, None
            cases.append({
                "doc_id": doc_id, "fmt": d["format"], "type": gt, "cat": e.get("category"),
                "kind": kind, "under": n_under, "over": n_over,
                "gold": [gs, ge], "gold_text": e["text"], "mask_span": span,
                "mask_text": G[span[0]:span[1]] if span else None,
                "left": G[max(0, gs - 70):gs], "right": G[ge:ge + 70],
            })
        print("  %2d/%d %s" % (k + 1, len(gold), doc_id), file=sys.stderr)
    json.dump(cases, open(HERE / "cases_subset.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    from collections import Counter
    print("всего", len(cases), Counter(c["kind"] for c in cases))


if __name__ == "__main__":
    main()
