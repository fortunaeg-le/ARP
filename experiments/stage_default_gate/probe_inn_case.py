# -*- coding: utf-8 -*-
"""ЭТАП DEFAULT-GATE — почему 6 остаточных случаев не закрылись возвратом тени.

Остаток класса после починки склейки «ИП + ФИО»: 5 случаев под крышей `INN`
(ИНН физлица и телефон, разорванные переносом) и 1 под `ADDRESS,ORG`.
Вопрос ровно один: КАКОЙ кандидат кого вытеснил и на каком шаге — если
вытеснения не было вовсе, то возвращать нечего, и механизм тут ни при чём.

Печатает сырых кандидатов детекции и результат арбитража по сегменту.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import resolve_for_masking  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

CASES = [("lease_0010__m1337_linebreak", "docx"),
         ("works_0011__m2718_linebreak", "docx")]


def main():
    for doc_id, fmt in CASES:
        path = os.path.join(DOCS, doc_id + "." + fmt)
        if not os.path.exists(path):
            path = os.path.join(DOCS, doc_id + ".txt")
        doc = extract(path)
        ents = run_detection(doc, CONFIG)
        kept = resolve_for_masking(doc, ents, CONFIG)
        segs = {e.segment_id for e in ents if e.entity_type in ("INN", "INN_PERSON")}
        print("=" * 70)
        print(doc_id)
        for sid in sorted(segs):
            print("  сегмент", sid)
            print("   СЫРЫЕ кандидаты:")
            for e in ents:
                if e.segment_id == sid:
                    print(f"      {e.entity_type:<12} [{e.start}:{e.end}] {e.original_text!r}")
            print("   ПОСЛЕ арбитража:")
            for e in kept:
                if e.segment_id == sid:
                    sh = [(s.entity_type, s.start, s.end) for s in (e.shadowed or ())]
                    print(f"      {e.entity_type:<12} [{e.start}:{e.end}] {e.original_text!r}"
                          f"   тень: {sh}")
            print(f"   (в наборе по умолчанию включены: "
                  f"{sorted(set(type_policy.PERSONAL))})")


if __name__ == "__main__":
    main()
