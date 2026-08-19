# -*- coding: utf-8 -*-
"""ЭТАП INSTR-SEAM: сидит ли ТОТ ЖЕ артефакт в линии «б» (утечка) и в линии «е».

    python experiments/instr_seam_lines_b_e_check.py <дамп ПОСЛЕ>

Берёт ПОИМЁННЫЙ список эталонных значений, чей вердикт линии «г» перевернул шов
(`instr_seam_narrowness.json`), и смотрит на те же значения глазами соседних
линий того же прогона:
  * линия «е» (недобор): если шов там уже исключён (этап REG), у ВСЕХ этих
    значений обязано быть `under == 0` — то есть две линии отвечают на один
    вопрос одинаково, и починка «г» их не рассинхронизировала;
  * линия «б» (утечка): статус leak_v2 у тех же значений + счёт фрагментов
    утечки, состоящих ТОЛЬКО из разделителей сборки, по ВСЕМУ корпусу.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "instr_seam_narrowness.json")
SEAM_CHARS = "\n\t"


def main(p_after):
    B = json.load(open(p_after, encoding="utf-8"))
    P = json.load(open(PROBE, encoding="utf-8"))
    touched = set((r["doc_id"], r["gold_type"], r["gold_text"]) for r in P["diffs"])
    print("записей масок, чей вердикт «г» перевернул шов: %d" % len(P["diffs"]))
    print("эталонных ЗНАЧЕНИЙ за ними:                     %d" % len(touched))

    leak = Counter()
    under = Counter()
    n = 0
    for d in B:
        for e in d.get("entities", []):
            if (d["doc_id"], e["type"], e["text"]) in touched:
                n += 1
                leak[e["leak_v2"]["status"]] += 1
                under[e["bnd"]["under"] > 0] += 1
    print("записей эталона, найденных по этим значениям:    %d" % n)
    print("\nЛИНИЯ «е» у них: недобор > 0 — %d записей, недобор == 0 — %d записей"
          % (under[True], under[False]))
    print("ЛИНИЯ «б» у них: %s" % dict(leak))

    frags = seam_frags = 0
    for d in B:
        for e in d.get("entities", []):
            for f in e["leak_v2"].get("fragments", []):
                frags += 1
                if isinstance(f, str) and f and all(ch in SEAM_CHARS for ch in f):
                    seam_frags += 1
    print("\nПО ВСЕМУ КОРПУСУ: фрагментов утечки %d; из них ТОЛЬКО из '\\n'/'\\t': %d"
          % (frags, seam_frags))
    print("Ноль во второй цифре означает: разделитель сборки не может БЫТЬ утечкой —"
          "\nлиния «б» меряет фрагменты ЯДРА значения в анонимном тексте, а '\\n'/'\\t'"
          "\nв ядро не входят ни у одного типа. Того же артефакта в линии «б» нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
