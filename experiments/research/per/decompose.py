# -*- coding: utf-8 -*-
"""decompose.py — РАЗВЕДКА PER-RECON. Разложение 688 незащищённых людей по
механизмам. КОДА НЕ ПРАВИТ: читает дамп прогона и классифицирует.

    venv/Scripts/python.exe experiments/research/per/decompose.py [дамп.json]

Единица счёта — ЧЕЛОВЕК (person_key из tests/corpus/by_entity.py: самый длинный
буквенный токен значения, обычно фамилия), внутри одного документа.
Человек НЕ ЗАЩИЩЁН, если хотя бы одно его вхождение не замаскировано ИЛИ
хотя бы от одного дожил фрагмент в анонимном тексте (leak_v2 >= 6).

Механизм приписывается ВХОЖДЕНИЮ, которое провалилось; человеку — набор
механизмов его провалившихся вхождений. Вопрос «сколько людей закроет ОДИН
механизм» считается строго: человек закрывается механизмом M, только если ВСЕ
его провалившиеся вхождения имеют механизм M.
"""
import io
import json
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML          # noqa: E402
import by_entity as BE            # noqa: E402

CYR = re.compile(r"[Ѐ-ӿ]")
LAT = re.compile(r"[A-Za-z]")
INVIS = re.compile(r"[­​-‏⁠﻿]")


# ---------------------------------------------------------------- морфология
_MORPH = None


def morph():
    global _MORPH
    if _MORPH is None:
        from natasha import MorphVocab
        _MORPH = MorphVocab()
    return _MORPH


def is_oblique(text):
    """Стоит ли самый длинный токен НЕ в именительном падеже.

    Осторожно: у многих фамилий разбор неоднозначен. Считаем косвенным, только
    если НИ ОДИН разбор не даёт Nom (то есть однозначно косвенный)."""
    toks = [t for t in ML.v2_norm_text(text).replace(".", " ").split()
            if t.isalpha() and len(t) >= 4]
    if not toks:
        return False
    tok = max(toks, key=len)
    try:
        forms = morph().parse(tok)
    except Exception:
        return False
    if not forms:
        return False
    cases = set()
    for f in forms:
        c = (f.feats or {}).get("Case")
        if c:
            cases.add(c)
    return bool(cases) and "Nom" not in cases


# ---------------------------------------------------------------- облик ФИО
def shape(text):
    """Облик значения: как оно выглядит на поверхности."""
    s = ML.v2_norm_text(text)
    raw = s.replace(".", " ").split()
    words = [t for t in raw if t.isalpha()]
    longs = [t for t in words if len(t) >= 3]
    inits = [t for t in words if len(t) <= 2]
    if len(longs) >= 3:
        return "ФИО полностью"
    if len(longs) == 2:
        return "имя+фамилия"
    if len(longs) == 1 and inits:
        return "фамилия+инициалы"
    if len(longs) == 1:
        return "фамилия одна"
    if not longs and inits:
        return "одни инициалы"
    return "прочий облик"


def case_class(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "нет букв"
    up = sum(1 for c in letters if c.isupper())
    if up == len(letters) and len(letters) > 2:
        return "ВЕРХНИЙ"
    if up == 0:
        return "нижний"
    # чередование: смена регистра чаще, чем на границах слов
    flips = sum(1 for a, b in zip(letters, letters[1:]) if a.isupper() != b.isupper())
    if flips >= max(3, len(letters) // 3):
        return "чередование"
    return "обычный"


def has_homoglyph(text):
    """Латиница внутри кириллического слова (или наоборот)."""
    for w in re.split(r"\s+", text):
        if CYR.search(w) and LAT.search(w):
            return True
    return False


# ------------------------------------------------------------- механизм
#: Порядок разбора — от механически определимого к остаточному.
def mechanism(e, group):
    """Механизм, из-за которого вхождение осталось открытым."""
    text = e["text"]
    tr = e.get("trick") or ""

    # 1. Подмена символов (омоглифы / невидимые) — видна прямо в значении.
    if has_homoglyph(text) or "homoglyph" in tr:
        return "подменённые символы"
    if INVIS.search(text) or "invisible" in tr or "zwj" in tr:
        return "невидимые символы"

    # 2. Разрыв значения вёрсткой.
    if "\n" in text or "linebreak" in tr or "cell_split" in tr \
            or "split_runs" in tr or "cell_boundary" in tr:
        return "разрыв вёрсткой"

    # 3. Регистр.
    cc = case_class(text)
    if cc in ("ВЕРХНИЙ", "нижний", "чередование"):
        return "регистр (%s)" % cc

    # 4. Чужой тип — фамилия опознана как организация/адрес.
    mr = e.get("miss_reason") or ""
    if mr.startswith("wrong_type:"):
        return "чужой тип (%s)" % mr.split(":", 1)[1]

    # 5. Падеж.
    if is_oblique(text):
        return "косвенный падеж"

    # 6. Облик: голая фамилия / отдельные инициалы.
    sh = shape(text)
    if sh in ("фамилия одна", "одни инициалы", "фамилия+инициалы"):
        return "облик: " + sh

    return "прочее"


# ------------------------------------------------------------------ разбор
def failed(e):
    """Вхождение провалилось: не замаскировано ИЛИ утекло."""
    hit6, _ = ML._leak_v2_hits(e["type"], e["leak_v2"])
    return (not e["found"]) or hit6


def collect(results):
    groups = collections.OrderedDict()
    for d in results:
        if d.get("outcome") != "processed":
            continue
        for e in d["entities"]:
            if e["type"] != "PER":
                continue
            k = (d["doc_id"], BE.person_key(e["text"]))
            g = groups.setdefault(k, {"doc": d["doc_id"], "key": k[1],
                                      "occ": [], "fail": []})
            g["occ"].append(e)
            if failed(e):
                g["fail"].append(e)
    return groups


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "tests", "corpus", "results_gate_current.json")
    results = json.load(open(path, encoding="utf-8"))
    groups = collect(results)
    total = len(groups)
    bad = [g for g in groups.values() if g["fail"]]

    out = io.open(os.path.join(HERE, "decompose.txt"), "w", encoding="utf-8")
    w = out.write
    w("дамп: %s\n" % os.path.basename(path))
    w("людей (person_key внутри документа): %d\n" % total)
    w("незащищённых: %d (%.2f%%)\n\n" % (len(bad), 100.0 * len(bad) / total))

    # --- 1. по механизмам: вхождения и люди
    occ_mech = collections.Counter()
    per_person = []
    for g in bad:
        ms = collections.Counter()
        for e in g["fail"]:
            m = mechanism(e, g)
            occ_mech[m] += 1
            ms[m] += 1
        per_person.append((g, ms))

    w("=== 1. МЕХАНИЗМЫ ПРОВАЛИВШИХСЯ ВХОЖДЕНИЙ ===\n")
    w("%-28s %8s %8s\n" % ("механизм", "вхожд", "людей"))
    people_touch = collections.Counter()
    for g, ms in per_person:
        for m in ms:
            people_touch[m] += 1
    for m, n in occ_mech.most_common():
        w("%-28s %8d %8d\n" % (m, n, people_touch[m]))
    w("%-28s %8d %8d\n\n" % ("ИТОГО", sum(occ_mech.values()), len(bad)))

    # --- 2. сколько людей закрывает ОДИН механизм целиком
    w("=== 2. ЛЮДИ, КОТОРЫХ ЗАКРЫВАЕТ ОДИН МЕХАНИЗМ ЦЕЛИКОМ ===\n")
    w("(все провалившиеся вхождения человека — этот механизм и только он)\n")
    w("%-28s %8s %8s\n" % ("механизм", "людей", "доля от 1824"))
    solo = collections.Counter()
    for g, ms in per_person:
        if len(ms) == 1:
            solo[next(iter(ms))] += 1
    for m, n in solo.most_common():
        w("%-28s %8d %11.2f%%\n" % (m, n, 100.0 * n / total))
    w("%-28s %8d %11.2f%%\n" % ("людей с ОДНИМ механизмом", sum(solo.values()),
                                100.0 * sum(solo.values()) / total))
    w("%-28s %8d\n\n" % ("людей с НЕСКОЛЬКИМИ", len(bad) - sum(solo.values())))

    # --- 3. сколько вхождений из скольких провалено
    w("=== 3. ГЛУБИНА ПРОВАЛА ===\n")
    part = sum(1 for g in bad if len(g["fail"]) < len(g["occ"]))
    whole = len(bad) - part
    w("человек с ЧАСТЬЮ открытых вхождений (одна форма из нескольких): %d\n" % part)
    w("человек, у кого открыты ВСЕ вхождения: %d\n" % whole)
    w("распределение числа вхождений на человека (незащищённые):\n")
    dist = collections.Counter(len(g["occ"]) for g in bad)
    for k in sorted(dist):
        w("  %2d вхожд.: %d чел.\n" % (k, dist[k]))
    w("\n")

    # --- 4. причина провала: маска или утечка
    w("=== 4. ЧЕМ ИМЕННО ПРОВАЛЕНО ВХОЖДЕНИЕ ===\n")
    why = collections.Counter()
    for g in bad:
        for e in g["fail"]:
            hit6, _ = ML._leak_v2_hits(e["type"], e["leak_v2"])
            if not e["found"] and hit6:
                why["не найдено И утекло"] += 1
            elif not e["found"]:
                why["не найдено (утечки метрика не видит)"] += 1
            else:
                why["найдено, но фрагмент дожил"] += 1
    for k, n in why.most_common():
        w("%-40s %6d\n" % (k, n))
    w("\n")

    # --- 5. облик и регистр по всем провалившимся
    w("=== 5. ОБЛИК И РЕГИСТР ПРОВАЛИВШИХСЯ ВХОЖДЕНИЙ ===\n")
    sh = collections.Counter()
    cs = collections.Counter()
    for g in bad:
        for e in g["fail"]:
            sh[shape(e["text"])] += 1
            cs[case_class(e["text"])] += 1
    for k, n in sh.most_common():
        w("облик  %-20s %6d\n" % (k, n))
    for k, n in cs.most_common():
        w("регистр %-19s %6d\n" % (k, n))
    w("\n")

    # --- 6. категория корпуса (насколько механизм искусственный)
    w("=== 6. КАТЕГОРИЯ КОРПУСА У ПРОВАЛИВШИХСЯ ВХОЖДЕНИЙ ===\n")
    cat = collections.Counter()
    for g in bad:
        for e in g["fail"]:
            cat[(e.get("category"), e.get("trick"))] += 1
    for (c, t), n in cat.most_common(25):
        w("%-12s %-28s %6d\n" % (c, t, n))
    out.close()

    # машиночитаемый срез для следующих шагов
    dump = []
    for g, ms in per_person:
        dump.append({"doc": g["doc"], "key": g["key"],
                     "n_occ": len(g["occ"]), "n_fail": len(g["fail"]),
                     "mech": dict(ms)})
    json.dump(dump, io.open(os.path.join(HERE, "persons_bad.json"), "w",
                            encoding="utf-8"), ensure_ascii=False)
    print("ok: %d / %d" % (len(bad), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
