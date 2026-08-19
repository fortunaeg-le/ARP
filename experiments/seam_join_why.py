# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN, разведка №2: ПОЧЕМУ ремонт вёрстки не собирает значение.

Повторяет цикл layout_repair.repair_pass с журналом на каждом отказе и печатает
для указанных документов корпуса, какое эталонное значение с признаком
`mut:linebreak`/`mut:cell_split` осталось ненайденным и на каком именно стороже.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

from extractor import extract                    # noqa: E402
from pipeline import run_detection               # noqa: E402
import layout_repair as LR                       # noqa: E402
from models import SourceDocument, TextSegment   # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")


def why(doc_id):
    path = os.path.join(DOCS, doc_id + ".txt")
    if not os.path.exists(path):
        path = os.path.join(DOCS, doc_id + ".docx")
    doc = extract(path)
    LR.ENABLED = False
    primary = run_detection(doc, CONFIG)
    LR.ENABLED = True

    prim_by_seg = {}
    for e in primary:
        prim_by_seg.setdefault(e.segment_id, []).append((e.start, e.end, e.entity_type))

    print(f"##### {doc_id}")
    for seg in doc.segments:
        if not seg.text or seg.metadata.get(LR._REPAIR_FLAG):
            continue
        det = seg.metadata.get("detection_text", seg.text)
        runs = LR._scan_runs(det)
        if not runs:
            continue
        prim = prim_by_seg.get(seg.id, ())
        prim_spans = [(s, e) for s, e, _t in prim]
        drop_meta = []
        for drops, klass, left, right in runs:
            if LR._covers(prim_spans, drops[0], drops[-1]):
                cov = [(s_, e_, t_) for s_, e_, t_ in prim
                       if s_ <= drops[0] and drops[-1] < e_]
                print(f"  [covers] прогон {drops} накрыт {cov} "
                      f":: {seg.text[max(0,drops[0]-25):drops[-1]+25]!r}")
                continue
            ok_per = LR._person_ok(klass, left, right)
            for p in drops:
                drop_meta.append((p, klass, ok_per))
        if not drop_meta:
            continue
        dropset = {p for p, _k, _o in drop_meta}
        rmap = [i for i in range(len(seg.text)) if i not in dropset]
        rtext = "".join(seg.text[i] for i in rmap)
        rdet = "".join(det[i] for i in rmap)
        metadata = {k: v for k, v in seg.metadata.items()
                    if k != "detection_text" and not k.startswith("_")}
        metadata[LR._REPAIR_FLAG] = True
        if rdet != rtext:
            metadata["detection_text"] = rdet
        tmp_seg = TextSegment(id=seg.id, text=rtext,
                              source_type=seg.source_type, metadata=metadata)
        tmp_doc = SourceDocument(segments=[tmp_seg], source_format=doc.source_format,
                                 source_path=doc.source_path)
        found = run_detection(tmp_doc, CONFIG, skip_address=True)
        maskable = LR._maskable_types(CONFIG)
        print(f"  == сегмент {seg.id} :: {seg.text[:90]!r}")
        print(f"     ремонт: {rtext[:120]!r}")
        for e in found:
            if e.end <= e.start or e.end > len(rmap):
                continue
            rs, re_ = rmap[e.start], rmap[e.end - 1] + 1
            inside = [(p, ok) for p, _k, ok in drop_meta if rs < p < re_]
            tag = f"     {e.entity_type:12s} {rtext[e.start:e.end]!r}"
            if not inside:
                print(tag, "мимо: лечение не потребовалось (дубль)")
                continue
            if e.entity_type == "PERSON":
                if not any(ok for _p, ok in inside):
                    print(tag, "ОТКАЗ: person_ok=False")
                    continue
            elif e.detector != "regex" or e.entity_type not in maskable:
                print(tag, f"ОТКАЗ: detector={e.detector} не regex/не маскируемый")
                continue
            elif any(seg.text[p] == "\n" for p, _ok in inside):
                if not LR._strict_seam_ok(rtext[e.start:e.end], e.entity_type,
                                          rdet, e.start, CONFIG):
                    print(tag, "ОТКАЗ: strict_seam (КС/якорь голой цепи)")
                    continue
            if not LR._absorb_ok(rs, re_, e.entity_type, prim,
                                 allow_absorb=(e.entity_type == "PERSON")):
                print(tag, "ОТКАЗ: страж поглощения")
                continue
            print(tag, "ПРИНЯТО")


if __name__ == "__main__":
    for d in sys.argv[1:]:
        why(d)
