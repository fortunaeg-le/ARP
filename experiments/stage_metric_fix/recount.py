# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 4 — ПЕРЕСЧЁТ КАРТИНЫ на починенном приборе.

    venv/Scripts/python.exe experiments/stage_metric_fix/recount.py ДО.json ПОСЛЕ.json

Считает поимённо то, на что опирается план ядра: утечку по вхождениям и по
типам, класс вёрстки (значение, разорванное переносом/невидимыми/омоглифами до
детектора), полноту. Печатает СТАРОЕ и НОВОЕ рядом — сравнение поимённо и есть
предмет части 4, а не одна итоговая цифра.

Счёт по сущностям и по людям — отдельный давно живущий прибор
(tests/corpus/by_entity.py), он зовётся тем же дампом и здесь не дублируется.
"""
import json
import sys
import collections

sys.path.insert(0, "tests/corpus")
sys.path.insert(0, "src")
import measure_lib as ML  # noqa: E402

# Признаки вёрстки в ЭТАЛОННОМ значении: значение доехало до детектора рваным.
INVIS = {" ", " ", "​", "‍", "‌", "­", "⁠"}
# ОМОГЛИФ — это НЕ «в значении есть кириллическая А». Наивная проверка «символ
# входит в таблицу омоглифов» пометила бы 7227 вхождений из 12033, то есть
# почти весь русский текст, и число не значило бы ничего. Признак ставится по
# СМЕШЕНИЮ алфавитов (латиница внутри кириллического значения и наоборот) либо
# по букве НА МЕСТЕ ЦИФРЫ (буква вплотную к цифрам).
_CYR = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")
_LAT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _letter_in_digit_run(text):
    for i, ch in enumerate(text):
        if ch not in ML._V2_LETTER2DIGIT:
            continue
        left = text[i - 1] if i else ""
        right = text[i + 1] if i + 1 < len(text) else ""
        if left.isdigit() or right.isdigit():
            return True
    return False


def layout_marks(text):
    marks = set()
    if "\n" in text:
        marks.add("перенос строки")
    if any(ch in INVIS for ch in text):
        marks.add("невидимые/NBSP")
    if (any(c in _CYR for c in text) and any(c in _LAT for c in text)) \
            or _letter_in_digit_run(text):
        marks.add("омоглифы")
    return marks


def picture(results):
    p = {"n": 0, "found": 0, "leak6": 0, "leak8": 0,
         "by_type": collections.defaultdict(lambda: {"n": 0, "leak6": 0}),
         "layout_n": 0, "layout_leak": 0,
         "mech": collections.Counter(), "mech_all": collections.Counter()}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            t = e["type"]
            hit6, hit8 = ML._leak_v2_hits(t, e["leak_v2"])
            p["n"] += 1
            p["found"] += int(e["found"])
            p["leak6"] += int(hit6)
            p["leak8"] += int(hit8)
            p["by_type"][t]["n"] += 1
            p["by_type"][t]["leak6"] += int(hit6)
            marks = layout_marks(e["text"])
            if marks:
                p["layout_n"] += 1
                for m in marks:
                    p["mech_all"][m] += 1
                if hit6:
                    p["layout_leak"] += 1
                    for m in marks:
                        p["mech"][m] += 1
    return p


def pct(a, b):
    return f"{100.0 * a / b:.2f}%" if b else "—"


def main():
    b = picture(json.load(open(sys.argv[1], encoding="utf-8")))
    a = picture(json.load(open(sys.argv[2], encoding="utf-8")))

    print("КАРТИНА ПО ВХОЖДЕНИЯМ (корпус v1, 324 док.)")
    print(f"  эталонных вхождений      {b['n']} -> {a['n']}")
    print(f"  полнота                  {pct(b['found'], b['n'])} -> {pct(a['found'], a['n'])}"
          f"   ({b['found']} -> {a['found']})")
    print(f"  утечка >= 6              {pct(b['leak6'], b['n'])} -> {pct(a['leak6'], a['n'])}"
          f"   ({b['leak6']} -> {a['leak6']}, +{a['leak6'] - b['leak6']})")
    print(f"  утечка >= 8              {pct(b['leak8'], b['n'])} -> {pct(a['leak8'], a['n'])}"
          f"   ({b['leak8']} -> {a['leak8']}, +{a['leak8'] - b['leak8']})")

    print("\nПО ТИПАМ — только там, где число сдвинулось")
    for t in sorted(set(b["by_type"]) | set(a["by_type"])):
        bt, at = b["by_type"][t], a["by_type"][t]
        if bt["leak6"] == at["leak6"]:
            continue
        print(f"  {t:<12} утечка {bt['leak6']} -> {at['leak6']}"
              f"   ({pct(bt['leak6'], bt['n'])} -> {pct(at['leak6'], at['n'])})")

    print("\nКЛАСС ВЁРСТКИ — значение доехало до детектора рваным")
    print(f"  вхождений с признаком вёрстки: {b['layout_n']} -> {a['layout_n']}")
    print(f"  из них текут:                  {b['layout_leak']} -> {a['layout_leak']}"
          f"   (+{a['layout_leak'] - b['layout_leak']})")
    print(f"  утечка ВНЕ класса вёрстки:     {b['leak6'] - b['layout_leak']}"
          f" -> {a['leak6'] - a['layout_leak']}")
    print("  по механизмам (с пересечениями: у значения бывает два признака):")
    for m in sorted(set(b["mech"]) | set(a["mech"])):
        print(f"    {m:<18} текут {b['mech'][m]} -> {a['mech'][m]}"
              f"   из {b['mech_all'][m]} -> {a['mech_all'][m]} вхождений с признаком")


if __name__ == "__main__":
    main()
