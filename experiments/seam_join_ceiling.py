# -*- coding: utf-8 -*-
"""ЭТАП SEAM-JOIN, разведка №4: ПОТОЛОК сшивки.

Для каждого утекающего эталонного значения класса вёрстки берёт окно текста
вокруг значения, выбрасывает вёрсточные прогоны ТОЛЬКО ВНУТРИ самого значения
(идеальная сшивка — как если бы система знала границы значения) и гоняет по
окну штатную посегментную детекцию. Отвечает на вопрос «а найдётся ли значение
вообще, если разрыв убрать» — до всякой работы над тем, КАК его убирать.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract                     # noqa: E402
from models import SourceDocument, TextSegment    # noqa: E402
from regex_detector import detect_regex           # noqa: E402
from pipeline import run_detection                # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DUMP = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
_LAYOUT = "\n\r\t    ​­"
WIN = 90

GTYPE = {"PERSON": "PER", "INN_PERSON": "INN_PER", "BANK_ACCOUNT": "ACCOUNT"}


def main():
    docs = json.load(open(DUMP, encoding="utf-8"))
    stat = collections.Counter()
    rows = []
    for r in docs:
        want = [e for e in r["entities"]
                if (e.get("trick") or "") in ("mut:linebreak", "mut:cell_split")
                and (e.get("leak_v2") or {}).get("status", "none") != "none"
                and not e.get("found")]
        if not want:
            continue
        doc = extract(os.path.join(DOCS, r["doc_id"] + "." + r["format"]))
        flat = "\n".join(s.text for s in doc.segments)
        for e in want:
            t = e["text"]
            pos = flat.find(t)
            if pos < 0:
                stat[(e["type"], "не найден в тексте")] += 1
                continue
            a, b = max(0, pos - WIN), min(len(flat), pos + len(t) + WIN)
            head, val, tail = flat[a:pos], t, flat[pos + len(t):b]
            joined = "".join(ch for ch in val if ch not in "\n\r\t")
            win = head + joined + tail
            seg = TextSegment(id="w", text=win, source_type="txt_line", metadata={})
            d2 = SourceDocument(segments=[seg], source_format="txt", source_path="w")
            vs, ve = len(head), len(head) + len(joined)
            hit = None
            for ent in run_detection(d2, CONFIG):
                if max(ent.start, vs) < min(ent.end, ve):
                    ok = ent.start <= vs and ve <= ent.end
                    tag = GTYPE.get(ent.entity_type, ent.entity_type)
                    if tag == e["type"]:
                        hit = "ЦЕЛИКОМ" if ok else "ЧАСТЬ"
                        if ok:
                            break
                    elif hit is None:
                        hit = "ЧУЖОЙ:" + tag
            stat[(e["type"], hit or "НЕТ")] += 1
            rows.append((e["type"], hit or "НЕТ", r["doc_id"], t))
    print("== потолок сшивки (только ненайденные сегодня) ==")
    for (ty, res), n in sorted(stat.items()):
        print(f"  {ty:12s} {res:16s} {n}")
    print("\n== поимённо ==")
    for row in sorted(rows):
        print(f"  {row[0]:11s} {row[1]:16s} {row[2]:34s} {row[3]!r}")


if __name__ == "__main__":
    main()
