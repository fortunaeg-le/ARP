# -*- coding: utf-8 -*-
"""Тот же замер по корпусу v1, но раскладка масок — НАБОР ПО УМОЛЧАНИЮ.

ЗАЧЕМ. Гейт и все числа STATE меряют МАКСИМУМ (все типы включены). Покупатель
получает продукт с набором «личные данные» (type_policy.PERSONAL: PERSON,
ADDRESS, PHONE, EMAIL, PASSPORT, SNILS, BIRTHDATE, INN_PERSON,
PASSPORT_ISSUER). Полнота и утечка на этом наборе НИКОГДА не мерились целиком —
линия «з» гейта видит только узкий класс «на максимуме закрыто, по умолчанию
открыто», а не общую картину.

КАК. run_measurement.process_doc делает ДВЕ раскладки поверх ОДНОГО арбитража и
все метрики считает по второй (type_policy.MAXIMUM). Здесь MAXIMUM подменяется
на PERSONAL в каждом воркере — арбитраж, детекция и прибор не трогаются вообще,
меняется только набор включённых типов, по которому собирается анонимный текст.
Следствие, названное явно: линия «з» в этом прогоне вырождается в ноль по
построению (обе раскладки совпали), её числа отсюда брать нельзя.
"""
import json
import os
import sys
import time
import multiprocessing

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

OUT = os.path.join(HERE, "results_default_profile.json")


def _init():
    import type_policy
    import run_measurement as RM
    RM.type_policy.MAXIMUM = type_policy.PERSONAL


def main():
    import run_measurement as RM
    gold = RM.load_gold()
    done = {}
    if os.path.exists(OUT):
        try:
            for rec in json.load(open(OUT, encoding="utf-8")):
                done[rec["doc_id"]] = rec
        except Exception:
            done = {}
    todo = [d for d in gold if d["doc_id"] not in done]
    print("готово %d/%d, осталось %d" % (len(done), len(gold), len(todo)), flush=True)
    if not todo:
        print("ПРОГОН ПОЛНЫЙ", flush=True)
        return
    t0 = time.time()
    workers = RM._default_workers()
    with multiprocessing.Pool(workers, initializer=_init) as pool:
        for i, rec in enumerate(pool.imap(RM._safe_process_doc, todo)):
            done[rec["doc_id"]] = rec
            if (i + 1) % 40 == 0:
                print("[%d/%d] %.0fs" % (i + 1, len(todo), time.time() - t0), flush=True)
                json.dump([done[d["doc_id"]] for d in gold if d["doc_id"] in done],
                          open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump([done[d["doc_id"]] for d in gold if d["doc_id"] in done],
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("DONE %d/%d за %.0fs -> %s" % (len(done), len(gold), time.time() - t0, OUT), flush=True)
    if len(done) == len(gold):
        print("ПРОГОН ПОЛНЫЙ", flush=True)


if __name__ == "__main__":
    main()
