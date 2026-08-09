# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX — ПРОБОЙ находки усиленного круга.

Круг (часть 3.2) на .docx показал: ФИО «Беляев Олег Олегович» остаётся ОТКРЫТЫМ
в анонимном тексте, хотя интерфейс работает набором по умолчанию «Только
персональные данные», где PERSON включён.

Гипотеза: в МАКСИМАЛЬНОМ наборе (на котором меряет гейт) этот спан закрывается
маской ДРУГОГО типа — ORG (класс Aprime-1: организация без якоря съедает ФИО).
В наборе по умолчанию ORG выключен, закрывать нечем, и значение уходит
открытым. Гейт этого не видит по построению: он всегда меряет на максимуме.

Скрипт печатает, чем закрыт спан в каждом наборе. Ничего не чинит.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOC = os.path.join(ROOT, "tests", "corpus", "docs", "agency_0002.docx")
VALUE = "Беляев Олег Олегович"


def main():
    doc = extract(DOC)
    ents = run_detection(doc, CONFIG)
    for label, types in (("МАКСИМУМ", type_policy.MAXIMUM),
                         ("по умолчанию (personal)", type_policy.PERSONAL)):
        anon, kept = tokenize(doc, ents, CONFIG, enabled_types=types)
        open_ = anon.count(VALUE)
        print(f"{label}: значение открытым текстом {open_} раз(а), масок {len(kept)}")
    # чем закрыт спан на максимуме
    anon, kept = tokenize(doc, ents, CONFIG, enabled_types=type_policy.MAXIMUM)
    for e in kept:
        seg = next(s for s in doc.segments if s.id == e.segment_id)
        if VALUE.split()[0] in (e.original_text or ""):
            print("  маска на максимуме:", e.entity_type, repr(e.original_text[:60]))


if __name__ == "__main__":
    main()
