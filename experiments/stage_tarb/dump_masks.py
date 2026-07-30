# -*- coding: utf-8 -*-
"""ЭТАП T-ARB — поимённый дамп ВСЕХ масок полного корпуса (не только gold-метрик).

Приёмка этапа (по требованию, зафиксированному в диалоге) требует не только
зелёный gate.py, но и побайтный дифф КАЖДОЙ выставленной маски до/после правки:
кортеж (doc_id, entity_type, start, end, original_text) для каждой сущности,
прошедшей resolve_overlaps и получившей токен (kept-список tokenize()). Любое
расхождение вне класса B-ADDR-HOMONYM — стоп, а не строка отчёта.

Переиспользует загрузку/детекцию tests/corpus/run_measurement.py дословно
(extract -> run_detection -> tokenize, набор MAXIMUM) — НЕ копирует логику,
чтобы дамп по построению совпадал с тем, что реально меряет gate.py.

Запуск:
    venv/Scripts/python.exe experiments/stage_tarb/dump_masks.py <output.json>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_DIR = os.path.join(HERE, "..", "..", "tests", "corpus")
sys.path.insert(0, CORPUS_DIR)

import run_measurement as RM  # noqa: E402


def dump_all():
    gold = RM.load_gold()
    out = {}
    n = len(gold)
    for i, d in enumerate(gold):
        doc_id = d["doc_id"]
        path = os.path.join(RM.DOCS, doc_id + "." + d["format"])
        doc = RM.extract(path)
        entities = RM.run_detection(doc, RM.CONFIG)
        _anon, kept = RM.tokenize(
            doc, entities, RM.CONFIG, enabled_types=RM.type_policy.MAXIMUM
        )
        out[doc_id] = sorted(
            [e.entity_type, e.segment_id, e.start, e.end, e.original_text]
            for e in kept
        )
        if (i + 1) % 50 == 0 or i + 1 == n:
            print(f"  {i + 1}/{n}", file=sys.stderr)
    return out


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "masks_dump.json"
    result = dump_all()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    total = sum(len(v) for v in result.values())
    print(f"Готово: {len(result)} документов, {total} масок -> {out_path}")
