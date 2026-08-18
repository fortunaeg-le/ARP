# -*- coding: utf-8 -*-
"""Разбор просадки masking C (96.58% -> 96.34%) МЕХАНИЗМОМ, а не догадкой.

C = доля масок, у которых тип маски совпал с типом эталонной сущности,
с НАИБОЛЬШИМ пересечением (measure_lib.mc_check_bc:1195). Знаменатель —
только маски, легшие хоть на одну эталонную сущность (scored=True,
measure_lib.py:1219-1222). Значит C падает по одной из трёх причин:
  (1) маска, раньше бывшая c_ok=True, стала c_ok=False;
  (2) в знаменатель ВОШЛИ новые маски, и среди них доля неверного типа
      выше средней (эффект состава знаменателя — гипотеза задания);
  (3) из знаменателя ВЫШЛИ верные маски.
Скрипт разделяет эти три вклада численно и печатает примеры.

Записи маски координат не содержат, поэтому ключ — (doc_id, gtype, text)
с кратностью: этого достаточно, чтобы сопоставить одинаковые маски двух
прогонов, и честно помечает случаи, где кратность изменилась.
"""
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

BASE = os.path.join(ROOT, "tests", "corpus", "results_baseline.json")
CUR = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")

out = open(os.path.join(HERE, "masking_c_analysis.txt"), "w", encoding="utf-8")


def say(*a):
    s = " ".join(str(x) for x in a)
    out.write(s + "\n")


def load(path):
    return {d["doc_id"]: d for d in json.load(open(path, encoding="utf-8"))}


base, cur = load(BASE), load(CUR)


def totals(dump):
    n_masks = n_scored = c_ok = b_ok = 0
    for d in dump.values():
        for m in d.get("masks", []):
            n_masks += 1
            if m.get("scored"):
                n_scored += 1
                c_ok += int(m["c_ok"])
                b_ok += int(m["b_ok"])
    return n_masks, n_scored, c_ok, b_ok


bm, bs, bc, bb = totals(base)
cm, cs, cc, cb = totals(cur)
say("=== 0. АРИФМЕТИКА ПРОСАДКИ ===")
say(f"было:  масок {bm}  знаменатель C (scored) {bs}  верный тип {bc}  "
    f"C={100.0*bc/bs:.4f}%   неверный тип {bs-bc}")
say(f"стало: масок {cm}  знаменатель C (scored) {cs}  верный тип {cc}  "
    f"C={100.0*cc/cs:.4f}%   неверный тип {cs-cc}")
say(f"дельта: масок {cm-bm:+d}  знаменатель {cs-bs:+d}  "
    f"верный тип {cc-bc:+d}  неверный тип {(cs-cc)-(bs-bc):+d}")
say("")
say("КОНТРФАКТ (разделение эффекта состава и эффекта качества):")
if cs:
    say(f"  если бы прибавка знаменателя ({cs-bs:+d}) была ТАКОГО ЖЕ качества, "
        f"как старый знаменатель, C была бы {100.0*bc/bs:.4f}% (не меняется).")
    say(f"  фактический прирост верных {cc-bc:+d} из {cs-bs:+d} новых мест "
        f"знаменателя -> качество ПРИБАВКИ "
        f"{100.0*(cc-bc)/(cs-bs):.2f}% против {100.0*bc/bs:.2f}% у старого.")
say("")

# --- сопоставление масок по кратности ключа (doc, gtype, text) --------------
say("=== 1. ЧТО ИМЕННО ИЗМЕНИЛОСЬ, ПО МАСКАМ ===")


def key_counter(doc, pred):
    c = Counter()
    for m in doc.get("masks", []):
        if pred(m):
            c[(m["gtype"], m["text"], m.get("gold_type"))] += 1
    return c


appeared = Counter()   # новые маски в знаменателе (scored)
vanished = Counter()   # ушедшие из знаменателя
flip_bad = Counter()   # тип был верен -> стал неверен (по паре gtype/gold_type)
examples_new_bad = []
examples_vanished_ok = []
examples_new_ok = []

for did in sorted(set(base) | set(cur)):
    b = base.get(did, {})
    c = cur.get(did, {})
    kb = key_counter(b, lambda m: m.get("scored"))
    kc = key_counter(c, lambda m: m.get("scored"))
    for k, n in (kc - kb).items():
        appeared[k] += n
        gtype, text, gold_type = k
        rec = (did, gtype, gold_type, text[:70], n)
        (examples_new_ok if gtype == gold_type else examples_new_bad).append(rec)
    for k, n in (kb - kc).items():
        vanished[k] += n
        gtype, text, gold_type = k
        if gtype == gold_type:
            examples_vanished_ok.append((did, gtype, gold_type, text[:70], n))


def summarize(counter, title):
    say(f"\n{title}: {sum(counter.values())} масок")
    agg = Counter()
    for (gtype, _t, gold_type), n in counter.items():
        agg[(gtype, gold_type, gtype == gold_type)] += n
    for (gtype, gold_type, ok), n in agg.most_common(25):
        say(f"   {n:5d}  маска {gtype:<12} на эталоне {str(gold_type):<12} "
            f"{'тип ВЕРЕН' if ok else 'тип НЕВЕРЕН (портит C)'}")


summarize(appeared, "ВОШЛИ в знаменатель C (маски, ставшие scored)")
summarize(vanished, "ВЫШЛИ из знаменателя C")

say("\n=== 2. ПРИМЕРЫ: НОВЫЕ МАСКИ НЕВЕРНОГО ТИПА (тянут C вниз) ===")
say("документ | тип маски | тип эталона | текст под маской | шт")
for r in sorted(examples_new_bad, key=lambda r: -r[4])[:40]:
    say(f"  {r[0]:<34} {r[1]:<12} {str(r[2]):<12} {r[3]!r:<50} x{r[4]}")

say("\n=== 3. ПРИМЕРЫ: МАСКИ ВЕРНОГО ТИПА, ВЫПАВШИЕ ИЗ ЗНАМЕНАТЕЛЯ ===")
for r in sorted(examples_vanished_ok, key=lambda r: -r[4])[:30]:
    say(f"  {r[0]:<34} {r[1]:<12} {str(r[2]):<12} {r[3]!r:<50} x{r[4]}")

say("\n=== 4. ПРИМЕРЫ: НОВЫЕ МАСКИ ВЕРНОГО ТИПА (тянут C вверх) ===")
for r in sorted(examples_new_ok, key=lambda r: -r[4])[:20]:
    say(f"  {r[0]:<34} {r[1]:<12} {str(r[2]):<12} {r[3]!r:<50} x{r[4]}")

# --- разрез по типу маски ---------------------------------------------------
say("\n=== 5. C ПО ТИПУ МАСКИ: было -> стало ===")


def per_type(dump):
    d = defaultdict(lambda: [0, 0])
    for doc in dump.values():
        for m in doc.get("masks", []):
            if m.get("scored"):
                d[m["gtype"]][0] += 1
                d[m["gtype"]][1] += int(m["c_ok"])
    return d


pb, pc = per_type(base), per_type(cur)
say(f"{'тип маски':<16}{'scored б->т':>18}{'верных б->т':>18}{'C% б->т':>22}")
for t in sorted(set(pb) | set(pc)):
    n0, k0 = pb.get(t, [0, 0])
    n1, k1 = pc.get(t, [0, 0])
    c0 = 100.0 * k0 / n0 if n0 else float("nan")
    c1 = 100.0 * k1 / n1 if n1 else float("nan")
    flag = "  <-- ИЗМЕНИЛСЯ" if (n0, k0) != (n1, k1) else ""
    say(f"{t:<16}{n0:>8} ->{n1:>7}{k0:>9} ->{k1:>7}"
        f"{c0:>10.2f} ->{c1:>8.2f}{flag}")

# --- вклад в дельту C -------------------------------------------------------
say("\n=== 6. РАЗЛОЖЕНИЕ ДЕЛЬТЫ C ПО ТИПУ МАСКИ ===")
say("вклад типа = (верных_т - верных_б) - C_старая_общая * (scored_т - scored_б)")
say("положительный вклад тянет C вверх, отрицательный — вниз")
Cold = bc / bs
rows = []
for t in sorted(set(pb) | set(pc)):
    n0, k0 = pb.get(t, [0, 0])
    n1, k1 = pc.get(t, [0, 0])
    contrib = (k1 - k0) - Cold * (n1 - n0)
    if abs(contrib) > 1e-9:
        rows.append((contrib, t, n1 - n0, k1 - k0))
for contrib, t, dn, dk in sorted(rows):
    say(f"  {t:<14} Δscored {dn:+5d}  Δверных {dk:+5d}   вклад в числитель "
        f"{contrib:+8.2f}  -> {100.0*contrib/cs:+.4f} пп")
say(f"\n  сумма вкладов {sum(r[0] for r in rows):+.2f} шт  "
    f"= {100.0*sum(r[0] for r in rows)/cs:+.4f} пп "
    f"(сверка: фактическая дельта C "
    f"{100.0*cc/cs - 100.0*bc/bs:+.4f} пп)")

out.close()
print("written")
