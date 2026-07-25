# -*- coding: utf-8 -*-
"""ЭТАП E′ — пересборка docs/known_leaks_stage_c.json по ФАКТИЧЕСКОМУ долгу.

До сессии файл содержал 61 запись (долг этапа C), при фактическом долге 116
(диагностика состава гейта D работала на неполном наборе). После сборки E′
файл обязан содержать РОВНО текущий фактический долг: убраны закрытые,
дозаписаны пред-существующие, у каждой записи — класс причины.
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "stage_d"))
import run_measurement as RM  # noqa: E402
import gate_d  # noqa: E402

RESULTS = os.path.join(HERE, "results_d_eprime.json")
KL = os.path.join(ROOT, "docs", "known_leaks_stage_c.json")

res = json.load(open(RESULTS, encoding="utf-8"))
ids = sorted(gate_d._missed_leak_ids(res))
print("фактический долг:", len(ids))

gold = {d["doc_id"]: d for d in RM.load_gold()}
old = json.load(open(KL, encoding="utf-8"))
old_by_id = {(r["doc_id"], r["value"]): r for r in old["leaks"]}


def classify(doc_id, value):
    mut = doc_id.split("__")[1] if "__" in doc_id else "clean"
    if "case" in mut:
        return ("case", "case-mutation: сплошной регистр ломает Titlecase-критерии "
                        "якоря (is_cap/house-pattern); вне охвата E′ (не разрыв)")
    if "combo" in mut:
        return ("combo", "combo-mutation: регистр+омоглифы поверх разрывов; сборка E′ "
                         "склеивает разрывы, но регистровый/омоглифный компонент "
                         "оставляет якорь слепым")
    if "homoglyph" in mut:
        return ("homoglyph", "homoglyph-mutation: буква-омоглиф на КРАЮ цифрового "
                             "токена не сводится нормализацией (защита «кв. 2б») — "
                             "индекс не распознаётся")
    if "linebreak" in mut:
        return ("linebreak-resid", "linebreak-остаток: улица-однофамилец после склейки "
                                   "захвачена PER (B-ADDR-HOMONYM) ИЛИ продолжение "
                                   "слова в верхнем регистре (гибрид с case — склейка "
                                   "\\n требует строчного продолжения, иначе мусор "
                                   "на чистых доках)")
    return ("other", "прочее")


leaks = []
from collections import Counter
cc = Counter()
for doc_id, value in ids:
    old_rec = old_by_id.get((doc_id, value))
    g = gold[doc_id]
    span = None
    cat = trick = None
    for e in g["entities"]:
        if e["type"] == "ADDRESS" and e["text"] == value:
            span = {"start": e["start"], "end": e["end"]}
            cat = e.get("category")
            trick = e.get("trick") or ("mut:" + doc_id.split("__m")[1].split("_", 1)[1]
                                       if "__" in doc_id else None)
            break
    cls, reason = classify(doc_id, value)
    cc[cls] += 1
    leaks.append({
        "doc_id": doc_id,
        "entity_type": "ADDRESS",
        "span": span or (old_rec or {}).get("span"),
        "value": value,
        "trick": trick or (old_rec or {}).get("trick"),
        "category": cat or (old_rec or {}).get("category", "adversarial"),
        "reason": reason,
        "closes_on": ("case-normalization" if cls in ("case", "combo")
                      else "per-addr-arbitration" if cls == "linebreak-resid"
                      else "normalizer-edge" if cls == "homoglyph"
                      else "entity-spans"),
    })

out = {
    "_comment": ("Реестр ИЗВЕСТНОГО долга утечки ADDRESS (found=False & leak_v2). "
                 "Пересобран этапом E′ 2026-07-23 по ПОЛНОМУ фактическому долгу "
                 "(до E′ файл держал 61 из 116 — диагностика состава гейта D "
                 "работала на неполном наборе). Сборка E′ закрыла 64 позиции "
                 "(разрывы \\n/невидимыми/индексы); остаток — регистровый класс "
                 "(case/combo), PER-омонимы улиц (B-ADDR-HOMONYM), омоглиф на краю "
                 "числа. Читается гейтом D, детектор его НЕ читает."),
    "stage": "e-prime",
    "type": "ADDRESS",
    "criterion": "found=False and leak_v2.status != none (per gate_d._missed_leak_ids)",
    "count": len(leaks),
    "leaks": leaks,
}
json.dump(out, open(KL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("записано:", len(leaks), "по классам:", dict(cc))
