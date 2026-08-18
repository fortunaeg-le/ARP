# -*- coding: utf-8 -*-
"""Возобновляемый полный прогон корпуса v1 (324 док.) для сессии MEASURE-2.

ЗАЧЕМ ОТДЕЛЬНЫЙ ДРАЙВЕР. Полный прогон ~9-10 минут, а ход сессии ограничен
по времени жёстче. Фоновый запуск запрещён заданием. Драйвер докладывает
результаты в один и тот же дамп: уже посчитанные doc_id пропускаются, так
что прогон можно доводить до конца несколькими синхронными вызовами.

Ничего в src/ и в корпусе не трогает: только читает gold.json и пишет
свой дамп в measurements/.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, CORPUS)

import run_measurement as RM  # noqa: E402

OUT = os.path.join(HERE, "results_measure2.json")


def main():
    gold = RM.load_gold()
    done = {}
    if os.path.exists(OUT):
        try:
            for rec in json.load(open(OUT, encoding="utf-8")):
                done[rec["doc_id"]] = rec
        except Exception as exc:
            print(f"дамп нечитаем ({exc}) — начинаю заново", flush=True)
            done = {}
    todo = [d for d in gold if d["doc_id"] not in done]
    print(f"готово {len(done)}/{len(gold)}, осталось {len(todo)}", flush=True)
    if not todo:
        print("ПРОГОН ПОЛНЫЙ", flush=True)
        return

    def flush(res):
        for rec in res:
            done[rec["doc_id"]] = rec
        ordered = [done[d["doc_id"]] for d in gold if d["doc_id"] in done]
        json.dump(ordered, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

    t0 = time.time()
    res = RM.run_all(todo, verbose=True, on_checkpoint=flush, checkpoint_every=25)
    flush(res)
    print(f"DONE {len(done)}/{len(gold)} за {time.time()-t0:.0f}s -> {OUT}", flush=True)
    if len(done) == len(gold):
        print("ПРОГОН ПОЛНЫЙ", flush=True)


if __name__ == "__main__":
    main()
