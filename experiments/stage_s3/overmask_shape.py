# -*- coding: utf-8 -*-
"""ЗАДАЧА 3 этапа S3 — ЗАМЕР предлагаемой линии гейта (НЕ внедрено).

Считает по дампу прогона «маски-калеки»: маска, которая (1) не легла ни на одну
эталонную сущность и ни на один объявленный негатив (диагностический класс
`nothing` этапа D — over-mask НЕАННОТИРОВАННОЙ прозы) И (2) не проходит
СОБСТВЕННОЕ правило своего типа, применённое к её же тексту.

Смысл: `nothing` целиком в гейт брать нельзя — набор неоднозначный (этап D),
там законно живут банки-контрагенты, которых корпус не размечает. Но
подмножество «маска, которую не обосновывает даже правило её собственного типа»
неоднозначным не является: «3.2» под [ADDRESS] и «к» под [ORG] не оправдывает
ничто.

    venv/Scripts/python.exe experiments/stage_s3/overmask_shape.py <dump.json> [...]
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ner_detector import _addr_span_strong          # noqa: E402
from normalizer import normalize_for_detection      # noqa: E402
from tokenizer import _org_half_carries_name        # noqa: E402


def justified(gtype: str, text: str) -> bool:
    """Обосновывает ли ПРАВИЛО ТИПА эту маску на её же тексте."""
    if gtype == "ADDRESS":
        norm, _ = normalize_for_detection(text)
        return _addr_span_strong(norm, 0, len(norm))
    if gtype == "ORG":
        return _org_half_carries_name(text)
    if gtype == "PER":
        # ФИО: хотя бы одно слово из >=2 букв (огрызок/инициал сам по себе — нет)
        return any(len(w) >= 2 and w.isalpha() for w in text.split())
    return True          # regex-типы: значение обосновано самим паттерном


def cripples(results):
    per = collections.Counter()
    texts = collections.defaultdict(collections.Counter)
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for f in r.get("false_positives", []):
            if f.get("on_negative") is not None or f.get("cross_type") is not None:
                continue
            if not justified(f["gtype"], f["text"]):
                per[f["gtype"]] += 1
                texts[f["gtype"]][f["text"][:40]] += 1
    return per, texts


def main():
    for p in sys.argv[1:]:
        res = json.load(open(p, encoding="utf-8"))
        noth = sum(1 for r in res if r.get("outcome") == "processed"
                   for f in r.get("false_positives", [])
                   if f.get("on_negative") is None and f.get("cross_type") is None)
        per, texts = cripples(res)
        print("%-55s over-mask прозы: %5d   ИЗ НИХ КАЛЕК: %4d %s"
              % (os.path.basename(p), noth, sum(per.values()), dict(per)))
        for t, c in texts.items():
            for k, n in c.most_common(8):
                print("      %-8s %3d  %r" % (t, n, k))


if __name__ == "__main__":
    main()
