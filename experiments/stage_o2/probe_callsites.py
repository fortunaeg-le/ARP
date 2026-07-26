# -*- coding: utf-8 -*-
"""
O2 — ГДЕ ИМЕННО зовут дорогие движки (yargy / natasha NER / deweave / org-детектор).

cProfile показал: yargy = 51% собственного времени, B3-проход = 53% стенных часов.
Профиль не говорит, КАКОЙ из пяти вызовов yargy стоит этих денег. Скрипт вешает
счётчики на конкретные точки вызова и печатает: сколько раз, сколько символов,
сколько секунд — по КАЖДОЙ точке.

Ничего в src/ не меняет: только оборачивает функции на время замера.
"""
import argparse
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
CFG = os.path.join(ROOT, "entity_types.yaml")
FIX = os.path.join(ROOT, "tests", "fixtures")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

STATS = defaultdict(lambda: [0, 0, 0.0])   # site -> [calls, chars, seconds]
_site = ["?"]


def bump(site, chars, dt):
    a = STATS[site]
    a[0] += 1
    a[1] += chars
    a[2] += dt


def install():
    import ner_detector as ND
    import tokenizer as TK
    import anchor_registry as AR
    import normalizer as NM
    from natasha import Doc

    # --- yargy (AddrExtractor) ---
    _addr = ND._addr_extractor

    def addr_wrap(text):
        t = time.perf_counter()
        r = list(_addr(text))
        bump("yargy @" + _site[0], len(text), time.perf_counter() - t)
        return r
    ND._addr_extractor = addr_wrap
    TK._addr_extractor = addr_wrap   # tokenizer импортирует символ внутрь функции

    # --- natasha NER-тэггер ---
    _tag = ND._ner_tagger

    class TagWrap:
        def __call__(self, *a, **k):
            return _tag(*a, **k)
    # tag_ner вызывается как nd.tag_ner(_ner_tagger); оборачиваем Doc.tag_ner
    _doc_tag = Doc.tag_ner

    def tag_wrap(self, tagger, *a, **k):
        t = time.perf_counter()
        r = _doc_tag(self, tagger, *a, **k)
        bump("ner_tag @" + _site[0], len(self.text), time.perf_counter() - t)
        return r
    Doc.tag_ner = tag_wrap

    _doc_seg = Doc.segment

    def seg_wrap(self, segmenter, *a, **k):
        t = time.perf_counter()
        r = _doc_seg(self, segmenter, *a, **k)
        bump("segment @" + _site[0], len(self.text), time.perf_counter() - t)
        return r
    Doc.segment = seg_wrap

    # --- deweave / виды ---
    _dw = ND._addr_deweave

    def dw_wrap(base):
        t = time.perf_counter()
        r = _dw(base)
        bump("deweave @" + _site[0] + ("  [changed]" if r[2] else "  [no-change]"),
             len(base), time.perf_counter() - t)
        return r
    ND._addr_deweave = dw_wrap
    TK._addr_deweave = dw_wrap

    _nfd = NM.normalize_for_detection

    def nfd_wrap(base):
        t = time.perf_counter()
        r = _nfd(base)
        bump("normalize @" + _site[0], len(base), time.perf_counter() - t)
        return r
    NM.normalize_for_detection = nfd_wrap
    AR.normalize_for_detection = nfd_wrap
    ND.normalize_for_detection = getattr(ND, "normalize_for_detection", _nfd)

    # --- OrgAnchorDetector.detect (зовётся и по сегментам, и по каждому B3-окну) ---
    _org_detect = AR.OrgAnchorDetector.detect

    def org_wrap(self, seg, norm, low, omap, reqs):
        t = time.perf_counter()
        r = _org_detect(self, seg, norm, low, omap, reqs)
        bump("OrgDetector.detect @" + _site[0], len(norm), time.perf_counter() - t)
        return r
    AR.OrgAnchorDetector.detect = org_wrap

    _per_detect = AR.PerAnchorDetector.detect

    def per_wrap(self, seg, norm, low, omap, reqs):
        t = time.perf_counter()
        r = _per_detect(self, seg, norm, low, omap, reqs)
        bump("PerDetector.detect @" + _site[0], len(norm), time.perf_counter() - t)
        return r
    AR.PerAnchorDetector.detect = per_wrap

    # --- разметка «где мы сейчас» ---
    _detect_ner = ND.detect_ner

    def detect_ner_wrap(*a, **k):
        _site[0] = "detect_ner"
        try:
            return _detect_ner(*a, **k)
        finally:
            _site[0] = "?"
    ND.detect_ner = detect_ner_wrap
    import pipeline
    pipeline.detect_ner = detect_ner_wrap

    _boundary = TK._detect_boundary_entities

    def boundary_wrap(*a, **k):
        _site[0] = "B3"
        try:
            return _boundary(*a, **k)
        finally:
            _site[0] = "?"
    TK._detect_boundary_entities = boundary_wrap

    _struct = AR.detect_structural

    def struct_wrap(*a, **k):
        _site[0] = "structural"
        try:
            return _struct(*a, **k)
        finally:
            _site[0] = "?"
    AR.detect_structural = struct_wrap
    pipeline.detect_structural = struct_wrap

    _extract_mod = __import__("extractor")
    _extract = _extract_mod.extract

    def extract_wrap(*a, **k):
        _site[0] = "extract"
        try:
            return _extract(*a, **k)
        finally:
            _site[0] = "?"
    _extract_mod.extract = extract_wrap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", default="large")
    args = ap.parse_args()

    import extractor
    import ner_detector  # noqa
    import anchor_registry  # noqa
    install()

    from extractor import extract as _e  # уже обёрнут
    from pipeline import run_detection
    from tokenizer import tokenize

    if args.load == "large":
        paths = [os.path.join(FIX, "synthetic_corporate_large.docx")]
    elif args.load == "styles":
        paths = [os.path.join(FIX, "synthetic_many_styles.docx")]
    elif args.load == "txt":
        paths = [os.path.join(FIX, "synthetic_corporate_large.txt")]
    elif args.load.startswith("corpus"):
        names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
        if ":" in args.load:
            names = names[:int(args.load.split(":")[1])]
        paths = [os.path.join(DOCS, f) for f in names]
    else:
        paths = [args.load]

    t0 = time.perf_counter()
    nseg = 0
    for p in paths:
        doc = extractor.extract(p)
        nseg += len(doc.segments)
        ents = run_detection(doc, CFG)
        tokenize(doc, ents, CFG)
    total = time.perf_counter() - t0

    print("нагрузка %s: %d файл(ов), %d сегментов, ПОЛНОЕ %.2f s"
          % (args.load, len(paths), nseg, total))
    print("%-46s %9s %12s %10s %8s %10s"
          % ("точка вызова", "вызовов", "символов", "секунд", "% полного", "мкс/вызов"))
    for site, (c, ch, sec) in sorted(STATS.items(), key=lambda kv: -kv[1][2]):
        print("%-46s %9d %12d %10.2f %7.1f%% %10.0f"
              % (site, c, ch, sec, sec / total * 100, sec / c * 1e6 if c else 0))


if __name__ == "__main__":
    main()
