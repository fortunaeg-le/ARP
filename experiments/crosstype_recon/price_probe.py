# -*- coding: utf-8 -*-
"""CROSSTYPE-RECON, часть 3 — ЦЕНА ГИПОТЕЗЫ, замеренная БЕЗ правки продукта.

Части 1-2 показали: весь PER-класс линии «и» (долг `DEFGATE-CROSSTYPE-LATENT`)
рождает ОДИН механизм — склейка «ИП + ФИО» в единый ORG
(`syntax_compound.merge_compound_entities`), и под спанами склейки на всём
корпусе v1 лежит ТОЛЬКО эталон PER, ни одного ORG.

Гипотеза, цену которой считает этот прогон: **склейка остаётся склейкой (один
токен на «ИП + ФИО» — смысловая целостность стороны договора не трогается), но
ТИП составной сущности — PERSON, а не ORG.**

Патч живёт ТОЛЬКО в этом скрипте (обёртка вокруг `merge_compound_entities`,
монкипатч в процессе замера); `src/` не меняется ни на байт. Прогон пишет
СВОЙ дамп, штатный `results_gate_current.json` не трогает.

Оценивать результат:
  venv/Scripts/python.exe tests/corpus/gate.py --results \
      experiments/crosstype_recon/results_per_type.json

Запуск: venv/Scripts/python.exe experiments/crosstype_recon/price_probe.py [N_док]
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

OUT = os.path.join(HERE, "results_per_type.json")

_patched = False


def _patch():
    """Составная «ИП + ФИО» получает тип PERSON вместо ORG. Идемпотентно."""
    global _patched
    if _patched:
        return
    import syntax_compound as SC
    import pipeline

    _orig = SC.merge_compound_entities

    def merged_as_person(doc, entities):
        out = _orig(doc, entities)
        for e in out:
            # Составная рождается ровно здесь и ровно с непустым `shadowed`
            # (см. syntax_compound.py:238) — других ORG с этим полем на выходе
            # детекции нет (проверено переписью, compound_census.py).
            if e.entity_type == "ORG" and e.shadowed:
                e.entity_type = "PERSON"
        return out

    SC.merge_compound_entities = merged_as_person
    # pipeline импортировал функцию ПО ИМЕНИ — подменяем и там.
    pipeline.merge_compound_entities = merged_as_person
    _patched = True


def process(d):
    _patch()
    import run_measurement as RM
    return RM._safe_process_doc(d)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    import run_measurement as RM
    gold = RM.load_gold()
    if limit:
        gold = gold[:limit]
    print("прогон с ПАТЧЕМ «составная = PERSON»: %d документов" % len(gold))
    t0 = time.time()
    results = []
    with mp.Pool(RM._default_workers()) as pool:
        for i, rec in enumerate(pool.imap(process, gold)):
            results.append(rec)
            if (i + 1) % 20 == 0:
                print("[%d/%d] %.0fs" % (i + 1, len(gold), time.time() - t0),
                      flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print("ГОТОВО %d док. за %.0f с -> %s" % (len(results), time.time() - t0, OUT))


if __name__ == "__main__":
    main()
