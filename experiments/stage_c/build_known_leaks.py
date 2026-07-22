# -*- coding: utf-8 -*-
"""Строит docs/known_leaks_stage_c.json — РЕЕСТР известного непокрытого долга ADDRESS
для гейта D (НЕ читается детектором; отдельный от детекции). Ровно те ADDRESS, что
HEAD маскировал через over-capture, а якорный этап C (с фиксом сеяния agency_0009)
НЕ детектирует и утекают: found HEAD=True, found FIX=False, leak_v2!=none.

Источник — дампы experiments/stage_c/sub_head.json vs sub_stagecfix.json (чтение,
не прогон). Спаны/значения — из tests/corpus/gold.json (индекс i выровнен по порядку
entities, как в run_measurement.process_doc)."""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GOLD = {d["doc_id"]: d for d in json.load(open(os.path.join(ROOT, "tests", "corpus", "gold.json"), encoding="utf-8"))}
H = {r["doc_id"]: r for r in json.load(open(os.path.join(HERE, "sub_head.json"), encoding="utf-8"))}
F = {r["doc_id"]: r for r in json.load(open(os.path.join(HERE, "sub_stagecfix.json"), encoding="utf-8"))}


def leaks(e): return e["leak_v2"]["status"] != "none"


rows = []
for did, fr in sorted(F.items()):
    hr = H.get(did)
    if not hr or fr.get("outcome") != "processed" or hr.get("outcome") != "processed":
        continue
    for i, fe in enumerate(fr["entities"]):
        he = hr["entities"][i]
        if not (fe["type"] == "ADDRESS" and leaks(fe) and not leaks(he) and he["found"] and not fe["found"]):
            continue
        ge = GOLD[did]["entities"][i]
        assert ge["type"] == "ADDRESS" and ge["text"] == fe["text"], (did, i)
        rows.append({
            "doc_id": did,
            "entity_type": "ADDRESS",
            "span": {"start": ge["start"], "end": ge["end"]},
            "value": ge["text"],
            "trick": ge.get("trick"),
            "category": ge.get("category"),
            "reason": "adversarial-mutation: yargy-unassembled (рваный/склеенный спан под мутацией)",
            "closes_on": "entity-spans",
        })

out = {
    "_comment": (
        "РЕЕСТР известного непокрытого долга ADDRESS для гейта этапа D. НЕ стоп-словарь, "
        "НЕ читается детектором — только гейт вычитает эти известные адверсариальные "
        "утечки при оценке регресса. Каждая закроется штатно на Entity.spans "
        "(мульти-спановые сущности). Сформирован из дампов sub_head vs sub_stagecfix "
        "(этап C + фикс сеяния agency_0009). agency_0009 «Москва Ленина 47/3 кв 67» "
        "ЗДЕСЬ ОТСУТСТВУЕТ — он покрыт фиксом, больше не долг."
    ),
    "stage": "C",
    "type": "ADDRESS",
    "criterion": "found on HEAD (greedy over-capture) AND missed on stage-C-fix AND leak_v2 != none",
    "count": len(rows),
    "leaks": rows,
}
dst = os.path.join(ROOT, "docs", "known_leaks_stage_c.json")
json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("known_leaks: %d записей -> %s" % (len(rows), dst))
from collections import Counter
print("по trick:", dict(Counter(r["trick"] for r in rows)))
print("agency_0009 присутствует?:", any(r["doc_id"] == "agency_0009" for r in rows))
print("base-документов:", len(set(r["doc_id"].split("__")[0] for r in rows)))
