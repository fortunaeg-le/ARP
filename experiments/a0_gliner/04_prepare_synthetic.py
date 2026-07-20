"""A0 bake-off, шаг 4 — вход СИНТЕТИЧЕСКОЙ подвыборки (проектный venv, natasha).

Берёт БАЗОВУЮ подвыборку корпуса через subsample.select_doc_ids (без фильтров —
2 base-документа на каждый contract_type + граничные, ~20 док.), извлекает и
нормализует каждый ровно как пайплайн, гоняет Natasha (detect_ner). НЕ полный
корпус (запрет ТЗ: 20 мин × сетка). Координаты — норм-текст, как для dogovor.

Выход:
  input/synthetic_segments.json  — {doc_id: [{seg_id, orig_text, norm_text}]}
  dumps/natasha_synthetic.json   — {doc_id: [сущности PER/ORG/ADDRESS]} + латентность

sha256 корпуса НЕ трогаем (только чтение docx). Запуск: venv/Scripts/python.exe ...
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, os.path.join(_ROOT, "tests", "corpus"))

from extractor import extract                    # noqa: E402
from normalizer import detection_view            # noqa: E402
from regex_detector import detect_regex          # noqa: E402
from ner_detector import detect_ner              # noqa: E402
import run_measurement as RM                      # noqa: E402
import subsample as SS                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(_ROOT, "entity_types.yaml")
DOCS = os.path.join(_ROOT, "tests", "corpus", "docs")


def main():
    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    doc_ids = SS.select_doc_ids(gold)      # базовая подвыборка, без фильтров
    print(f"Подвыборка: {len(doc_ids)}/{len(gold)} документов")

    seg_out = {}
    nat_out = {}
    t_total = 0.0
    for doc_id in doc_ids:
        d = by_id[doc_id]
        path = os.path.join(DOCS, f"{doc_id}.{d['format']}")
        if not os.path.isfile(path):
            print(f"  ПРОПУСК (нет файла): {doc_id}")
            continue
        doc = extract(path)
        segs = []
        for seg in doc.segments:
            if not seg.text:
                continue
            norm, _ = detection_view(seg)
            segs.append({"seg_id": seg.id, "orig_text": seg.text, "norm_text": norm})
        seg_out[doc_id] = segs

        t0 = time.perf_counter()
        regex_e = detect_regex(doc, CONFIG)
        ner_e = detect_ner(doc, CONFIG, regex_entities=regex_e)
        t_total += time.perf_counter() - t0
        nat_out[doc_id] = [
            {"seg_id": e.segment_id, "type": e.entity_type, "text": e.original_text,
             "start": e.start, "end": e.end} for e in ner_e
        ]

    with open(os.path.join(HERE, "input", "synthetic_segments.json"), "w",
              encoding="utf-8") as f:
        json.dump(seg_out, f, ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, "dumps", "natasha_synthetic.json"), "w",
              encoding="utf-8") as f:
        json.dump({"latency_ner_total_s": round(t_total, 2),
                   "n_docs": len(nat_out), "by_doc": nat_out},
                  f, ensure_ascii=False, indent=1)

    n_ents = sum(len(v) for v in nat_out.values())
    n_segs = sum(len(v) for v in seg_out.values())
    print(f"Документов: {len(seg_out)}, сегментов: {n_segs}, "
          f"Natasha NER сущностей: {n_ents}, латентность NER {t_total:.2f}c")


if __name__ == "__main__":
    main()
