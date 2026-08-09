# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 2 — ПРОБОЙ механизма AUD1-HEADER-FALSEMISS.

Вопрос: почему прибор пишет «не найдено» по значениям колонтитула, если живая
проба показывает, что они замаскированы?

Гипотеза: `measure_lib.build_segment_offsets` ищет текст сегмента ТОЛЬКО в
участке тела PT-1 (курсор стартует с body_start), а сегменты колонтитулов
(этап NODES научил extract их читать) лежат в PT-1 ДО тела (header1) и ПОСЛЕ
сносок (footer1). Значит `find` не находит их никогда -> offsets=None ->
seg_ok=False -> маска не глобализуется -> эталон в колонтитуле остаётся
«не найден».

Скрипт НИЧЕГО не чинит: печатает счётчики.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import json  # noqa: E402
import measure_lib as ML  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
GOLD = os.path.join(ROOT, "tests", "corpus", "gold.json")


def main():
    gold = json.load(open(GOLD, encoding="utf-8"))
    gold.sort(key=lambda d: d["doc_id"])
    docx = [d for d in gold if d["format"] == "docx"]

    n_parts_seg = 0
    n_parts_unloc = 0
    n_body_unloc = 0
    gold_in_header = 0
    gold_in_header_masked = 0
    per_doc = []
    stats = {}
    for d in docx:
        path = os.path.join(DOCS, d["doc_id"] + ".docx")
        G = ML.pt1_text(path)
        body_start = ML.body_offset(path, G)
        _, body_text = ML._docx_header_and_body_text(path)
        body_end = body_start + len(body_text)
        doc = extract(path)
        offs, misses = ML.build_segment_offsets(doc, G, body_start)
        part_segs = [s for s in doc.segments if s.metadata.get("part")]
        if not part_segs:
            continue
        n_parts_seg += len([s for s in part_segs if s.text])
        unloc = [s for s in part_segs if s.text and offs.get(s.id) is None]
        n_parts_unloc += len(unloc)
        n_body_unloc += len([s for s in doc.segments
                             if not s.metadata.get("part") and s.text
                             and offs.get(s.id) is None])

        # эталонные сущности ВНЕ тела (в PT-1 это колонтитул/сноска/футер)
        outside = [e for e in d["entities"]
                   if not (e["start"] >= body_start and e["end"] <= body_end)]
        if not outside:
            continue
        entities = run_detection(doc, CONFIG)
        anon, kept = tokenize(doc, entities, CONFIG,
                              enabled_types=type_policy.MAXIMUM)
        anon_norm = ML.v2_norm_text(anon)
        # ЧТО СИСТЕМА ВООБЩЕ ПРОЧИТАЛА: объединённый текст её сегментов.
        # Без этой проверки «значения нет в anon» ничего не доказывает: текста
        # непрочитанной зоны (сноски) там нет и не было НИКОГДА.
        read_norm = ML.v2_norm_text("\n".join(s.text for s in doc.segments))
        for e in outside:
            gold_in_header += 1
            v = ML.v2_norm_text(e["text"])
            if not v:
                continue
            was_read = v in read_norm
            if was_read and v not in anon_norm:
                gold_in_header_masked += 1
            zone = "header" if e["end"] < body_start else "footnote/footer"
            stats.setdefault((zone, was_read), 0)
            stats[(zone, was_read)] += 1
        per_doc.append((d["doc_id"], len(outside), len(unloc)))

    print(f"сегментov chastey (kolontitul/futer) s tekstom: {n_parts_seg}")
    print(f"iz nih NE lokalizovano priborom:               {n_parts_unloc}")
    print(f"segmentov TELA ne lokalizovano:                {n_body_unloc}")
    print(f"etalonnyh sushnostey vne tela:                 {gold_in_header}")
    print(f"iz nih PROCHITANY sistemoy i ischezli iz anon: {gold_in_header_masked}")
    print("razbivka (zona, prochitano systemoy) -> shtuk:")
    for k in sorted(stats, key=str):
        print("   ", k, stats[k])
    for row in per_doc[:20]:
        print("   ", row)


if __name__ == "__main__":
    main()
