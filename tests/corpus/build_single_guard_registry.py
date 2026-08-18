# -*- coding: utf-8 -*-
"""ЭТАП SINGLE-GUARD — пересборка реестра линии «и» по дампу прогона гейта.

Реестр `known_single_guard.json` — поимённый список значений, спрятанных под
маской ЧУЖОГО типа (класс «одиночная защита», долг DEFGATE-CROSSTYPE-LATENT).
Линия «и» гейта краснеет, если реестровый случай перестал быть прикрытым.

Реестр НЕ планка и НЕ эталон: он не задаёт порог качества, а перечисляет
состояние. Пересобирать его можно и нужно, когда состав класса законно
изменился, — но КАЖДАЯ пересборка обязана сопровождаться записью в
docs/JOURNAL.md с причиной: иначе достаточно будет пересобрать реестр, чтобы
красное стало зелёным, и сторож превратится в бумажку.

Запуск:  venv/Scripts/python.exe tests/corpus/build_single_guard_registry.py \
             [tests/corpus/results_gate_current.json]
"""
import io
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402

OUT = os.path.join(HERE, "known_single_guard.json")
DEFAULT_DUMP = os.path.join(HERE, "results_gate_current.json")

WHY = (
    "ЭТАП SINGLE-GUARD, линия «и» (долг DEFGATE-CROSSTYPE-LATENT). Поимённый "
    "список эталонных значений, которые на МАКСИМУМЕ закрыты маской ЧУЖОГО "
    "типа: тип из набора по умолчанию (пользователь ждёт маску) спрятан под "
    "именем типа вне набора. Сегодня это НЕ утечка — значение закрыто, и при "
    "настройках по умолчанию его подхватывает другая раскладка масок. Но "
    "защита у него ОДИНОЧНАЯ, и потеря её не видна ни одному счётчику: выпав "
    "из класса, случай УМЕНЬШАЕТ и число «под чужой маской», и разницу "
    "раскладок линии «з», то есть выглядит улучшением. Поэтому линия «и» "
    "судит поимённо: реестровый случай, ставший открытым, красит гейт "
    "безусловно. Пересборка реестра — только через "
    "tests/corpus/build_single_guard_registry.py и с записью в JOURNAL."
)


def main():
    dump = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DUMP
    results = json.load(open(dump, encoding="utf-8"))
    sg = ML.single_guard_summary(results)
    if not sg["present"]:
        print("в дампе %s нет поля single_guard — реестр собирать не из чего"
              % os.path.basename(dump))
        return 2
    cases = []
    for key in sg["covered"]:
        rec = sg["state"][key]
        doc_id, etype, start, end = key
        cases.append({
            "doc_id": doc_id, "type": etype, "start": start, "end": end,
            "len": rec.get("len"), "mask_types": rec.get("mask_types", []),
        })
    payload = {
        "why": WHY,
        "source_dump": os.path.basename(dump),
        "n": len(cases),
        "per_type": sg["per_type"],
        "pairs": {"%s->%s" % k: v for k, v in sorted(sg["pairs"].items())},
        "cases": cases,
    }
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("реестр записан: %s — %d случаев" % (os.path.basename(OUT), len(cases)))
    print("по типам эталона: %s" % sg["per_type"])
    for k, v in sorted(sg["pairs"].items(), key=lambda kv: -kv[1]):
        print("  %s -> %s: %d" % (k[0], k[1], v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
