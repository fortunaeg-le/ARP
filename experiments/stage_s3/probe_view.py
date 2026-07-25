# -*- coding: utf-8 -*-
"""Зонд второго (расплетённого) адресного прохода по ОДНОМУ сегменту:
печатает вид, склейки и спаны на каждом шаге. Только чтение.

    venv/Scripts/python.exe experiments/stage_s3/probe_view.py <doc_id> <segment_id>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import ner_detector as ND      # noqa: E402
from extractor import extract  # noqa: E402
from natasha import Doc        # noqa: E402

DOCS = os.path.join(ROOT, "tests", "corpus", "docs")


def main():
    doc_id, seg_id = sys.argv[1], sys.argv[2]
    path = next(os.path.join(DOCS, doc_id + e) for e in (".txt", ".docx")
                if os.path.isfile(os.path.join(DOCS, doc_id + e)))
    doc = extract(path)
    seg = next(s for s in doc.segments if s.id == seg_id)
    view, vmap, glue = ND._addr_view(seg)
    print("VIEW  =", repr(view[:600]))
    print("GLUE src =", glue)
    if not view:
        return
    d = Doc(view)
    d.segment(ND._segmenter)
    d.tag_ner(ND._ner_tagger)
    loc = [(sp.start, sp.stop) for sp in d.spans if sp.type == "LOC"]
    print("LOC  =", [(view[s:e]) for s, e in loc])
    yar = ND._glue_address_matches(view, list(ND._addr_extractor(view)))
    print("YARGY=", [view[s:e] for s, e in yar])
    raw = ND._build_address_spans(view, loc + yar, [])
    print("RAW  =", [view[s:e] for s, e in raw])
    for rs, re_ in raw:
        for ss, se in ND._addr_split_sentences(view, rs, re_):
            ts, te = ND._addr_strip_edges(view, ss, se)
            print("   sent %r -> strip %r strong=%s"
                  % (view[ss:se], view[ts:te],
                     ND._addr_span_strong(view, ts, te) if ts < te else None))
    fin = ND._finalize_address_spans(view, raw, [])
    print("FINAL=", [view[s:e] for s, e in fin])
    for s, e in fin:
        src_s, src_e = ND.norm_to_src(vmap, s, e)
        print("   emit? glue=%s  src=%r"
              % (ND._span_has_glue(src_s, src_e, glue), seg.text[src_s:src_e]))


if __name__ == "__main__":
    main()
