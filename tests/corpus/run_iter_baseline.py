# -*- coding: utf-8 -*-
"""
run_iter_baseline.py — пересборка ЗАМОРОЖЕННОГО baseline итерационного среза.

Зачем отдельный baseline
------------------------
`results_baseline.json` снят на 324 документах. Прогон на 25 документах даёт
другой знаменатель по КАЖДОМУ типу — сравнивать с ним нечего (docs/PERF_REPORT.md
§4.4.3). Без второй точки отсчёта срез остаётся «просто срезом без сравнения»:
видно число, но не видно, стало ли хуже. `results_iter_baseline.json` — та же
структура, что у основного baseline (это дамп `run_measurement.run_all`), снятая
на зафиксированном составе `subset_iter.json`.

ЧЕМ ЭТА ПЕРЕСБОРКА ОТЛИЧАЕТСЯ ОТ ПЕРЕСБОРКИ ОСНОВНОГО BASELINE
--------------------------------------------------------------
Ничем по смыслу и всем по последствиям:

  * основной `results_baseline.json` — ТОЧКА ОТСЧЁТА ПРИЁМКИ. Его пересборка
    означает «новое поведение принято как норма» и допустима только вместе с
    зелёным `gate.py` и записью в HANDOFF, что именно изменилось и почему;
  * `results_iter_baseline.json` — точка отсчёта ОТЛАДКИ. Сам по себе он ничего
    не принимает и никого не пускает в коммит.

Отсюда жёсткое правило: **срезовый baseline пересобирается ТОЛЬКО вместе с
основным и только после того, как основной принят.** Обратный порядок
(пересобрать срезовый «чтобы стало зелено», не трогая основной) превращает срез
в самоподтверждающийся: он начнёт согласовываться с регрессией, которую основной
baseline ещё считает регрессией.

Процедура пересборки (оба baseline, строго в этом порядке):
    1. venv/Scripts/python.exe tests/corpus/gate.py            # обязан быть ЗЕЛЁНЫМ
    2. venv/Scripts/python.exe tests/corpus/run_measurement.py  # -> results_current.json
       и только сознательным решением копировать его в results_baseline.json
    3. venv/Scripts/python.exe tests/corpus/run_iter_baseline.py  # -> results_iter_baseline.json

Если состав среза менялся (`build_subset_iter.py`), шаг 3 обязателен: baseline,
снятый на другом составе, несравним по знаменателю так же, как основной.

Запуск:
    venv/Scripts/python.exe tests/corpus/run_iter_baseline.py
    venv/Scripts/python.exe tests/corpus/run_iter_baseline.py --dry-run
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_measurement as RM  # noqa: E402
import subset_lib as SL  # noqa: E402

OUT = os.path.join(HERE, "results_iter_baseline.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="прогнать, но не записывать")
    args = ap.parse_args()

    gold = RM.load_gold()
    by_id = {d["doc_id"]: d for d in gold}
    doc_ids = SL.load_subset_ids()
    missing = [i for i in doc_ids if i not in by_id]
    if missing:
        print("!!! в gold.json нет документов среза: %s" % ", ".join(missing))
        return 1
    subset = [by_id[i] for i in doc_ids]

    print("Пересборка baseline СРЕЗА: %d документов из %d." % (len(subset), len(gold)))
    t0 = time.time()
    results = RM.run_all(subset, verbose=True)
    print("прогон: %.1f c" % (time.time() - t0))
    crashed = [r["doc_id"] for r in results if r.get("outcome") != "processed"]
    print("крешей: %d" % len(crashed))
    if args.dry_run:
        print("--dry-run: файл не записан.")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print("записано: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
