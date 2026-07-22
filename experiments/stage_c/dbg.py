# -*- coding: utf-8 -*-
import os, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.abspath(os.path.join(HERE,"..",".."))
sys.path.insert(0, os.path.join(ROOT,"src"))
from extractor import extract
from pipeline import run_detection
import ner_detector as N
from normalizer import detection_view, src_to_norm
from regex_detector import detect_regex
from anchor_registry import detect_structural
CONFIG=os.path.join(ROOT,"entity_types.yaml")
doc=extract(sys.argv[1] if len(sys.argv)>1 else os.path.join(HERE,"dogovor_test.txt"))
regex_e=detect_regex(doc,CONFIG)
struct=detect_structural(doc,regex_entities=regex_e)
org=[e for e in struct if e.entity_type=="ORG"]; per=[e for e in struct if e.entity_type=="PERSON"]
for seg in doc.segments:
    if not seg.text: continue
    text,omap=detection_view(seg)
    has=N._ADDR_ANCHOR_RE.search(text)
    nlp=N.Doc(text); nlp.segment(N._segmenter); nlp.tag_ner(N._ner_tagger)
    loc=[(s.start,s.stop) for s in nlp.spans if s.type=="LOC"]
    if not (loc or has): continue
    yargy=N._glue_address_matches(text,list(N._addr_extractor(text)))
    org_n=[src_to_norm(omap,e.start,e.end) for e in org if e.segment_id==seg.id]
    per_n=[src_to_norm(omap,e.start,e.end) for e in per if e.segment_id==seg.id]
    rgx_n=[src_to_norm(omap,e.start,e.end) for e in regex_e if e.segment_id==seg.id]
    yargy=N._filter_suspect_yargy(text,yargy,list(org_n)+list(per_n),loc)
    occ=N._address_barriers(text,[],rgx_n,list(org_n)+list(per_n))
    raw=N._build_address_spans(text,loc+yargy,occ)
    fin=N._finalize_address_spans(text,raw,occ)
    print("\n[%s] %r"%(seg.id, seg.text.strip()[:90]))
    print("  LOC:",[text[s:e] for s,e in loc])
    print("  yargy:",[text[s:e] for s,e in yargy])
    print("  raw:",[text[s:e] for s,e in raw])
    print("  FINAL:",[(text[s:e]) for s,e in fin])
