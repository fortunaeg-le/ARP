# -*- coding: utf-8 -*-
"""CROSSTYPE — почему мультиспан не собрал \n-рваный ИНН физлица.

Прогоняет шаги _match_seam_type руками для каждой из 4 записей INN_PER линии
«з» и печатает, на каком именно страже цепь отвергнута. Вывод в UTF-8 файл
(консоль Windows режет кириллицу).
"""
import io
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402
from extractor import extract  # noqa: E402
import multispan as MS  # noqa: E402
from normalizer import detection_view  # noqa: E402
from regex_detector import VALIDATORS, _has_anchor  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
GOLD = os.path.join(ROOT, "tests", "corpus", "gold.json")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
REG = os.path.join(ROOT, "tests", "corpus", "known_default_leaks.json")
OUT = os.path.join(HERE, "cross_seam_probe.txt")


def main():
    g = json.load(open(GOLD, encoding="utf-8"))
    docs = g["documents"] if isinstance(g, dict) else g
    by_id = {d["doc_id"]: d for d in docs}
    reg = json.load(open(REG, encoding="utf-8"))
    spec = MS._load_seam_spec(CONFIG)
    w = io.open(OUT, "w", encoding="utf-8")

    w.write("spec: %s\n" % [(t, tot) for t, tot, _, _ in spec])
    seen = set()
    for case in reg["cases"]:
        if case["doc_id"] in seen:
            continue
        seen.add(case["doc_id"])
        d = by_id[case["doc_id"]]
        path = os.path.join(DOCS, d["doc_id"] + "." + d["format"])
        doc = extract(path)
        for segment in doc.segments:
            if not segment.text or "ИНН" not in segment.text:
                continue
            norm, omap = detection_view(segment)
            for chain in MS._chains(norm, MS._ACC_SEP):
                if len(chain) < 2:
                    continue
                if "\n" not in norm[chain[0].start():chain[-1].end()]:
                    continue
                digits = MS._eff_digits("".join(x.group(0) for x in chain))
                if len(digits) not in (10, 11, 12, 13, 15, 20):
                    continue
                w.write("\n--- %s seg=%s chain=%r digits=%s len=%d\n"
                        % (d["doc_id"], segment.id,
                           norm[chain[0].start():chain[-1].end()],
                           digits, len(digits)))
                for etype, totals, validator, anchor_re in spec:
                    if len(digits) not in totals:
                        continue
                    ok_v = validator is None or validator(digits)
                    ok_a = _has_anchor(norm, chain[0].start(), anchor_re)
                    parts = MS._chain_parts_at_newlines(norm, chain)
                    bad_part = len(parts) > 1 and any(
                        MS._part_is_complete(p, totals, validator) for p in parts)
                    w.write("    %-12s КС=%s якорь=%s часть-целая=%s -> %s\n"
                            % (etype, ok_v, ok_a, bad_part,
                               "ПРИНЯТ" if (ok_v and ok_a and not bad_part)
                               else "ОТВЕРГНУТ"))
                w.write("    контекст: %r\n"
                        % norm[max(0, chain[0].start() - 60):chain[-1].end() + 20])
    w.close()
    print("written", OUT)


if __name__ == "__main__":
    main()
