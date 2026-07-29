# -*- coding: utf-8 -*-
"""
neighbours.py — БАРЬЕРЫ ЦЕЛЫ: маски соседних типов не сдвинулись.

    venv/Scripts/python.exe experiments/stage_addr_b/neighbours.py before.json after.json

Приёмка этапа ADDR-B, пункт 3. Правка границ адреса обязана менять ТОЛЬКО
адрес: если расширившийся адресный спан поглотил соседний реквизит или ФИО, тот
теряет свой токен (или сдвигает границы) — и это регресс, который агрегатные
метрики покажут лишь косвенно.

Сверяем ПОБАЙТНО, по каждому документу: последовательность
(тип маски, её текст, её токен) для ВСЕХ типов, КРОМЕ ADDRESS. Совпала —
барьеры целы; разошлась — печатаем первые расхождения поимённо.

Вход — два дампа run_measurement (results_*.json), не корпус.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP = "ADDRESS"


def load(p):
    if not os.path.isabs(p):
        p = os.path.join(HERE, p)
    return {r["doc_id"]: r for r in json.load(open(p, encoding="utf-8"))}


def sig(rec):
    return [(m["gtype"], m["token"], m["text"])
            for m in rec.get("masks", []) if m["gtype"] != SKIP]


def addr_texts(rec):
    return [m["text"] for m in rec.get("masks", []) if m["gtype"] == SKIP]


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    docs = sorted(set(a) | set(b))
    lost, absorbed, renumbered, arrived = [], [], [], []
    for d in docs:
        ra, rb = a.get(d), b.get(d)
        if ra is None or rb is None:
            lost.append((d, "документ есть только в одном прогоне"))
            continue
        sa, sb = sig(ra), sig(rb)
        if sa == sb:
            continue
        texts_b = {(t, x) for t, _tok, x in sb}
        new_addr = addr_texts(rb)
        for t, tok, x in sa:
            if (t, x) in texts_b:
                renumbered.append((d, t, x))       # тот же тип и текст, другой номер
            elif any(x in na for na in new_addr):
                absorbed.append((d, t, x))         # территорию закрыл ADDRESS
            else:
                lost.append((d, "%s %r больше НИЧЕМ не закрыт" % (t, x)))
        for t, tok, x in sb:
            if (t, x) not in {(tt, xx) for tt, _k, xx in sa}:
                arrived.append((d, t, x))
    print("документов сравнено:               %d" % len(docs))
    print("масок НЕ-ADDRESS «до» / «после»:   %d / %d"
          % (sum(len(sig(r)) for r in a.values()), sum(len(sig(r)) for r in b.values())))
    print()
    print("ПОТЕРЯНО (значение больше не закрыто ничем): %d   <- только это регресс" % len(lost))
    for d, why in lost[:15]:
        print("  !!", d, why)
    print("поглощено адресом (та же территория, но под ADDRESS): %d" % len(absorbed))
    for d, t, x in absorbed[:10]:
        print("   -", d, t, repr(x))
    print("новых масок чужих типов: %d" % len(arrived))
    for d, t, x in arrived[:10]:
        print("   +", d, t, repr(x))
    print("перенумеровано (тип и текст те же, номер токена другой): %d" % len(renumbered))
    print("\nИТОГ: %s" % ("ЗЕЛЁНЫЙ — ни одно значение соседнего типа не осталось открытым"
                          if not lost else "КРАСНЫЙ — соседняя маска потеряна"))
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
