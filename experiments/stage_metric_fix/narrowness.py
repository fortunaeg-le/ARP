# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX — УЗОСТЬ ПРАВКИ ПРИБОРА, доказанная сличением дампов.

Правка измерительного слоя обязана двигать ТОЛЬКО то, ради чего сделана.
Утверждение «остальное не изменилось» ничего не стоит, пока не сличены дампы
двух прогонов на НЕИЗМЕННОМ src/ — поле за полем, вхождение за вхождением.

    venv/Scripts/python.exe experiments/stage_metric_fix/narrowness.py \
        dump_before.json dump_after.json [--expect leak|found]

`--expect leak` — ждём расхождений ТОЛЬКО в поле leak_v2 и только в сторону
«стало видно утечку» (часть 1: раздроблённое значение).
`--expect found` — ждём расхождений ТОЛЬКО в полях локализации/полноты и только
в сторону «стало найдено» (часть 2: колонтитулы).

Печатает: сколько записей сошлось побайтно, какие поля разошлись, в какую
сторону, с разбивкой по типам. Ненулевое расхождение ВНЕ ожидаемого поля —
провал узости, и он печатается поимённо.
"""
import json
import sys
import collections

LEAK_FIELDS = {"leak_v2"}
FOUND_FIELDS = {"found", "exact", "full", "left_trim", "right_trim",
                "miss_reason", "in_body"}


def key_of(doc_id, i, e):
    return (doc_id, i, e["type"])


def main():
    before = json.load(open(sys.argv[1], encoding="utf-8"))
    after = json.load(open(sys.argv[2], encoding="utf-8"))
    expect = None
    if "--expect" in sys.argv:
        expect = sys.argv[sys.argv.index("--expect") + 1]

    b_by = {r["doc_id"]: r for r in before}
    a_by = {r["doc_id"]: r for r in after}
    assert set(b_by) == set(a_by), "состав документов разошёлся — дампы несравнимы"

    same = 0
    field_diff = collections.Counter()
    leak_dir = collections.Counter()
    leak_by_type = collections.Counter()
    found_by_type = collections.Counter()
    other = []
    n_masks_diff = 0

    for doc_id in sorted(b_by):
        rb, ra = b_by[doc_id], a_by[doc_id]
        if len(rb.get("masks", [])) != len(ra.get("masks", [])):
            n_masks_diff += 1
        eb, ea = rb.get("entities", []), ra.get("entities", [])
        assert len(eb) == len(ea), f"{doc_id}: число эталонных сущностей разошлось"
        for i, (x, y) in enumerate(zip(eb, ea)):
            if x == y:
                same += 1
                continue
            keys = set(x) | set(y)
            diff = [k for k in keys if x.get(k) != y.get(k)]
            for k in diff:
                field_diff[k] += 1
            if set(diff) <= LEAK_FIELDS:
                sb = x["leak_v2"]["status"]
                sa = y["leak_v2"]["status"]
                leak_dir[(sb, sa)] += 1
                if sb == "none" and sa != "none":
                    leak_by_type[x["type"]] += 1
                else:
                    other.append((doc_id, i, x["type"], "leak", sb, sa))
            elif set(diff) <= FOUND_FIELDS | {"bnd"}:
                # ЧАСТЬ 2. Локализация колонтитула двигает не только «найдено»:
                # у сущности появляются координаты, а значит и границы (bnd).
                # Но всё это ОБЯЗАНО происходить только ВНЕ тела: сущность тела
                # правка не касается вовсе.
                if x["in_body"]:
                    other.append((doc_id, i, x["type"], "В ТЕЛЕ", sorted(diff), ""))
                elif not x["found"] and y["found"]:
                    found_by_type[x["type"]] += 1
                else:
                    other.append((doc_id, i, x["type"], "found",
                                  x["found"], y["found"]))
            else:
                other.append((doc_id, i, x["type"], "ПОЛЯ", sorted(diff), ""))

    print(f"сущностей сошлось побайтно: {same}")
    print(f"полей с расхождением: {dict(field_diff)}")
    print(f"документов с изменившимся ЧИСЛОМ масок: {n_masks_diff}")
    if leak_dir:
        print("утечка, направление (было -> стало):")
        for k, v in sorted(leak_dir.items(), key=str):
            print(f"    {k[0]} -> {k[1]}: {v}")
        print(f"  стало видно (none -> утечка), по типам: {dict(leak_by_type)}"
              f"  ИТОГО {sum(leak_by_type.values())}")
    if found_by_type:
        print(f"стало найдено (found False -> True), по типам: {dict(found_by_type)}"
              f"  ИТОГО {sum(found_by_type.values())}")
    if other:
        print(f"\nНЕОЖИДАННЫЕ расхождения: {len(other)}")
        for row in other[:40]:
            print("   ", row)
    else:
        print("\nнеожиданных расхождений НЕТ")

    if expect == "leak":
        bad = [k for k in field_diff if k not in LEAK_FIELDS]
        print(f"\nвердикт узости (ожидалось только leak_v2): "
              f"{'УЗКО' if not bad and not other else 'ШИРЕ ОЖИДАЕМОГО: ' + str(bad)}")
    elif expect == "found":
        bad = [k for k in field_diff if k not in FOUND_FIELDS]
        print(f"\nвердикт узости (ожидалось только локализация/полнота): "
              f"{'УЗКО' if not bad and not other else 'ШИРЕ ОЖИДАЕМОГО: ' + str(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
