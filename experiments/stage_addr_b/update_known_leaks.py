# -*- coding: utf-8 -*-
"""ЭТАП ADDR-B — пересборка docs/known_leaks_stage_c.json по ФАКТИЧЕСКОМУ долгу.

Реестр — не порог (порог нулевой и живёт в gate_config), а СПРАВОЧНИК СОСТАВА:
гейт печатает, сколько из текущего долга он документирует, и предупреждает о
подмене «N на другой N». Если реестр отстаёт от факта, эта диагностика работает
на неполном наборе — ровно то, что этап E′ и чинил (тогда 61 из 116).

ADDR-B закрыл 19 позиций (границы адреса перестали рвать спан на аббревиатуре,
«а/я» и метке поля — часть адресов стала находиться целиком). Долг 63 -> 44;
пришедших НЕТ. Пересобираем файл ровно по факту, сохраняя классы причин
из процедуры E′.

    venv/Scripts/python.exe experiments/stage_addr_b/update_known_leaks.py <results.json>
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import run_measurement as RM  # noqa: E402
import gate  # noqa: E402

KL = os.path.join(ROOT, "docs", "known_leaks_stage_c.json")


def classify(doc_id):
    mut = doc_id.split("__")[1] if "__" in doc_id else "clean"
    if "case" in mut:
        return ("case", "case-mutation: сплошной регистр ломает Titlecase-критерии "
                        "якоря (is_cap/house-pattern)")
    if "combo" in mut:
        return ("combo", "combo-mutation: регистр+омоглифы поверх разрывов — "
                         "регистровый/омоглифный компонент оставляет якорь слепым")
    if "homoglyph" in mut:
        return ("homoglyph", "homoglyph-mutation: буква-омоглиф на КРАЮ цифрового "
                             "токена не сводится нормализацией (защита «кв. 2б») — "
                             "индекс не распознаётся")
    if "linebreak" in mut:
        return ("linebreak-resid", "linebreak-остаток: значение разорвано переносом, "
                                   "склейка E′ его не покрывает")
    return ("other", "прочее")


def main():
    results = json.load(open(sys.argv[1], encoding="utf-8"))
    ids = sorted(gate.missed_leak_ids(results))
    print("фактический долг:", len(ids))

    gold = {d["doc_id"]: d for d in RM.load_gold()}
    old = json.load(open(KL, encoding="utf-8"))
    old_by_id = {(r["doc_id"], r["value"]): r for r in old["leaks"]}

    leaks, cc = [], Counter()
    for doc_id, value in ids:
        old_rec = old_by_id.get((doc_id, value)) or {}
        span = cat = trick = None
        for e in gold[doc_id]["entities"]:
            if e["type"] == "ADDRESS" and e["text"] == value:
                span = {"start": e["start"], "end": e["end"]}
                cat, trick = e.get("category"), e.get("trick")
                break
        cls, reason = classify(doc_id)
        cc[cls] += 1
        leaks.append({
            "doc_id": doc_id,
            "entity_type": "ADDRESS",
            "span": span or old_rec.get("span"),
            "value": value,
            "trick": trick or old_rec.get("trick"),
            "category": cat or old_rec.get("category", "adversarial"),
            "reason": reason,
            "closes_on": ("case-normalization" if cls in ("case", "combo")
                          else "entity-spans" if cls == "linebreak-resid"
                          else "normalizer-edge" if cls == "homoglyph"
                          else "entity-spans"),
        })

    out = {
        "_comment": ("Реестр ИЗВЕСТНОГО долга утечки ADDRESS (found=False & leak_v2). "
                     "Пересобран этапом ADDR-B 2026-07-29 по фактическому долгу: "
                     "63 -> 44, ушли 19, пришедших нет. Закрыли их не мультиспан и не "
                     "нормализация, а границы: адрес перестал рваться на аббревиатуре "
                     "типа улицы («бул.», «просп.»), «а/я» перестал срезаться обрезкой "
                     "краёв, метка поля больше не съедает якорь. Остаток — регистровый "
                     "класс (case/combo), омоглиф на краю числа, разрывы строкой. "
                     "Читается гейтом (условие 5), детектор его НЕ читает."),
        "stage": "addr-b",
        "type": "ADDRESS",
        "criterion": "found=False and leak_v2.status != none (per gate.missed_leak_ids)",
        "count": len(leaks),
        "leaks": leaks,
    }
    json.dump(out, open(KL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("записано:", len(leaks), "по классам:", dict(cc))


if __name__ == "__main__":
    main()
