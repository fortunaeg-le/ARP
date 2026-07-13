# -*- coding: utf-8 -*-
"""Прогон GOLDEN-набора адресов через текущий detect_ner: детекция + full-closure.

Метрика (см. tests/golden_addresses.py):
  detection    — хоть один ADDRESS-спан пересекает эталон.
  full-closure — объединение ADDRESS-спанов покрывает эталон целиком.
  на не-адресах — ADDRESS-спанов быть не должно.

Печатает сводку по категориям и список НЕ закрытых полностью (с тем, что вернул детектор).
Используется как измерительный прибор этапа A (до/после).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import uuid
from collections import Counter, defaultdict

from models import SourceDocument, TextSegment
from ner_detector import detect_ner
from golden_addresses import address_items, nonaddress_items

CONFIG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def address_spans(text):
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<golden>")
    ents = detect_ner(doc, CONFIG)
    return [(e.start, e.end) for e in ents if e.entity_type == "ADDRESS"]


def covers(spans, gs, ge):
    """True если объединение spans покрывает [gs, ge) целиком."""
    covered = [False] * (ge - gs)
    for s, e in spans:
        for i in range(max(s, gs), min(e, ge)):
            covered[i - gs] = True
    return all(covered)


def overlaps_any(spans, gs, ge):
    return any(max(s, gs) < min(e, ge) for s, e in spans)


def main():
    det = Counter()
    full = Counter()
    total = Counter()
    not_full = []
    not_det = []
    for cat, text, span in address_items():
        total[cat] += 1
        gs = text.index(span)
        ge = gs + len(span)
        spans = address_spans(text)
        d = overlaps_any(spans, gs, ge)
        f = covers(spans, gs, ge)
        det[cat] += 1 if d else 0
        full[cat] += 1 if f else 0
        if not d:
            not_det.append((cat, text, spans))
        elif not f:
            not_full.append((cat, text, span, spans, [text[s:e] for s, e in spans]))

    fp = []
    for cat, text, _ in nonaddress_items():
        spans = address_spans(text)
        if spans:
            fp.append((text, [text[s:e] for s, e in spans]))

    print("=" * 78)
    print(f"{'cat':10} {'N':>3} {'detect':>7} {'full':>6}")
    print("-" * 78)
    for cat in ("marker", "nomarker", "cell", "abbr", "legal"):
        print(f"{cat:10} {total[cat]:>3} {det[cat]:>7} {full[cat]:>6}")
    print("-" * 78)
    print(f"{'ALL':10} {sum(total.values()):>3} {sum(det.values()):>7} {sum(full.values()):>6}")
    print(f"non-addr false positives: {len(fp)} / {len(nonaddress_items())}")

    print("\n---- NOT DETECTED (leak: adres not found at all) ----")
    for cat, text, spans in not_det:
        print(f"  [{cat}] {text!r} -> spans={spans}")

    print("\n---- DETECTED BUT NOT FULLY CLOSED (identifying tail leaks) ----")
    for cat, text, span, spans, texts in not_full:
        print(f"  [{cat}] {text!r}")
        print(f"        golden={span!r}")
        print(f"        got   ={texts}")

    print("\n---- FALSE POSITIVES on non-addresses ----")
    for text, texts in fp:
        print(f"  {text!r} -> {texts}")


if __name__ == "__main__":
    main()
