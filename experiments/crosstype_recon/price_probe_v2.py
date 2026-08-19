# -*- coding: utf-8 -*-
"""CROSSTYPE-RECON, часть 4 — та же цена на КОРПУСЕ v2 (138 док.).

Часть 3 считает цену гипотезы «составная «ИП + ФИО» = PERSON, а не ORG» на
корпусе v1. Здесь — тот же патч и тот же вопрос на корпусе v2, у которого своя
разметка и свой генератор. Прогон снимает ДВА дампа одним запуском:

  * `results_v2_base.json`  — БЕЗ патча (точки отсчёта v2 в репозитории нет,
    свежий дамп v2 не коммитился, поэтому базу снимаем сами);
  * `results_v2_pertype.json` — С патчем.

Отчёты по обоим:
  venv/Scripts/python.exe tests/corpus_v2/measure_v2.py --results <файл>

Правок в `src/` не делает. Патч — тот же монкипатч, что в `price_probe.py`.
Запуск: venv/Scripts/python.exe experiments/crosstype_recon/price_probe_v2.py [N_док]
"""
import os
import sys
import json
import time
import multiprocessing as mp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus_v2"))

OUT_BASE = os.path.join(HERE, "results_v2_base.json")
OUT_PATCH = os.path.join(HERE, "results_v2_pertype.json")

_patched = False


def _patch():
    global _patched
    if _patched:
        return
    import syntax_compound as SC
    import pipeline

    _orig = SC.merge_compound_entities

    def merged_as_person(doc, entities):
        out = _orig(doc, entities)
        for e in out:
            if e.entity_type == "ORG" and e.shadowed:
                e.entity_type = "PERSON"
        return out

    SC.merge_compound_entities = merged_as_person
    pipeline.merge_compound_entities = merged_as_person
    _patched = True


def _run(d, patch):
    import measure_v2 as M2
    if patch:
        _patch()
    try:
        return M2.process_doc(d)
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "crashed", "doc_id": d["doc_id"], "error": repr(exc)}


def base_one(d):
    return _run(d, False)


def patch_one(d):
    return _run(d, True)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    import measure_v2 as M2
    gold = M2.load_gold()
    if limit:
        gold = gold[:limit]
    for tag, fn, out in (("БЕЗ патча", base_one, OUT_BASE),
                         ("С патчем «составная = PERSON»", patch_one, OUT_PATCH)):
        print("v2, %s: %d документов" % (tag, len(gold)), flush=True)
        t0 = time.time()
        res = []
        with mp.Pool(6) as pool:
            for i, rec in enumerate(pool.imap(fn, gold)):
                res.append(rec)
                if (i + 1) % 20 == 0:
                    print("  [%d/%d] %.0fs" % (i + 1, len(gold), time.time() - t0),
                          flush=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        print("  ГОТОВО за %.0f с -> %s" % (time.time() - t0, out), flush=True)


if __name__ == "__main__":
    main()
