# -*- coding: utf-8 -*-
"""ЭТАП T1 — приёмка 1 и 2 по дампам `sweep.py`.

  ПРИЁМКА 1. `before.json` (прошлый коммит, вариант «Максимум») против
  `after.json` (этот коммит, вариант «Максимум»): sha обезличенного текста и
  карта масок обязаны совпасть у КАЖДОГО документа. Этап не вводит ни одного
  нового типа — расхождение означало бы, что фильтр протёк в общий путь.

  ПРИЁМКА 2. Для каждого из 14 типов: в варианте «без типа T» масок типа T нет
  вовсе, а карта масок остальных типов совпадает с «Максимумом» посимвольно.

Запуск:
    venv/Scripts/python.exe experiments/stage_t1/compare.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "src")))

from sweep import CORPUS_TYPES  # noqa: E402  (тот же список, одна копия)


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    before = load("before.json")["documents"]
    after = load("after.json")["documents"]

    print("=" * 78)
    print("ПРИЁМКА 1 — «Максимум» побайтно как до этапа")
    print("=" * 78)
    assert set(before) == set(after), "разный состав документов в дампах"
    diff_sha = [d for d in sorted(after) if before[d]["maximum"]["sha"] != after[d]["maximum"]["sha"]]
    diff_map = [d for d in sorted(after) if before[d]["maximum"]["masks"] != after[d]["maximum"]["masks"]]
    print(f"документов сравнено:            {len(after)}")
    print(f"расхождений sha текста:         {len(diff_sha)}")
    print(f"расхождений карты масок:        {len(diff_map)}")
    if diff_sha or diff_map:
        for d in (diff_sha or diff_map)[:5]:
            print("   ", d)
    ok1 = not diff_sha and not diff_map
    print("ИТОГ 1:", "ЗЕЛЁНЫЙ" if ok1 else "КРАСНЫЙ")

    print()
    print("=" * 78)
    print("ПРИЁМКА 2 — выключение типа не двигает границы остальных")
    print("=" * 78)
    print(f"{'тип':<14} {'масок на макс.':>14} {'убрано':>8} {'осталось':>9} "
          f"{'сдвигов границ':>15} {'док. затронуто':>15}")
    print("-" * 78)
    ok2 = True
    for victim in CORPUS_TYPES:
        total_v = removed = rest = shifted = touched = 0
        for d in sorted(after):
            full = after[d]["maximum"]["masks"]
            cut = after[d][victim]["masks"]
            base = [m for m in full if m[3] != victim]
            nv = sum(1 for m in full if m[3] == victim)
            total_v += nv
            if nv:
                touched += 1
            leftover = sum(1 for m in cut if m[3] == victim)
            removed += nv - leftover
            rest += leftover
            if cut != base:
                shifted += 1
                ok2 = False
        print(f"{victim:<14} {total_v:>14} {removed:>8} {rest:>9} {shifted:>15} {touched:>15}")
    print("-" * 78)
    print("ИТОГ 2:", "ЗЕЛЁНЫЙ" if ok2 else "КРАСНЫЙ")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
