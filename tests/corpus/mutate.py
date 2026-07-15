# -*- coding: utf-8 -*-
"""
mutate.py — детерминированный мутатор корпуса.

КОНТРАКТ: мутант без корректного gold — брак. Здесь контракт выполняется
не «аккуратностью», а конструкцией: мутатор правит МОДЕЛЬ документа
(_model/<doc_id>.json), а границы start/end пересчитываются сериализатором
из мутированной модели. Пересчитать их «неправильно» технически невозможно —
они не хранятся, они выводятся. validate.py затем перепроверяет каждый спан
по файлу с диска.

Запуск:
    python3 mutate.py                 # сиды по умолчанию: 1337, 2718
    python3 mutate.py --seeds 1 2 3

Правила:
    homoglyph       омоглифы (кириллица<->латиница, буквы на месте цифр)
    invisible       NBSP / narrow NBSP / ZWSP / ZWJ / мягкий перенос / word joiner
    case            регистр: lower / UPPER / ПеРеМеЖаЮщИйСя
    digit_spaces    пробелы и дефисы внутри цифровых групп
    linebreak       перенос строки в середине сущности
    cell_split      сущность разорвана границей ячейки таблицы (только .docx)
    combo           омоглифы + невидимые + регистр одновременно
"""
import argparse
import copy
import json
import os
import random

import corpus_lib as CL
from corpus_lib import (CYR2LAT, LAT2CYR, DIGIT2CYR, NBSP, NNBSP, ZWSP, ZWJ, SHY, WJ,
                        gold_entry, render, update_gold)

ROOT = os.path.dirname(os.path.abspath(__file__))
NUMERIC = {"INN", "OGRN", "KPP", "ACCOUNT", "BIK", "PHONE", "SNILS", "PASSPORT", "BIRTHDATE"}
TEXTUAL = {"PER", "ORG", "ADDRESS", "EMAIL"}
NOISE_P = 0.15          # доля НЕ-сущностных чанков, которые тоже мутируем
                        # (иначе «странный символ» = маркер сущности, и замер врёт)


# ------------------------------------------------------------------ трансформы
def t_homoglyph(s, rnd):
    out, n = [], 0
    for c in s:
        if n < 4 and rnd.random() < 0.35:
            if c in DIGIT2CYR:
                out.append(DIGIT2CYR[c]); n += 1; continue
            if c in CYR2LAT:
                out.append(CYR2LAT[c]); n += 1; continue
            if c in LAT2CYR:
                out.append(LAT2CYR[c]); n += 1; continue
        out.append(c)
    if n == 0:
        for i, c in enumerate(out):
            if c in DIGIT2CYR:
                out[i] = DIGIT2CYR[c]; break
            if c in CYR2LAT:
                out[i] = CYR2LAT[c]; break
    return "".join(out)


def t_invisible(s, rnd):
    if len(s) < 3:
        return s
    chars = [NBSP, NNBSP, ZWSP, ZWJ, SHY, WJ]
    k = min(3, max(1, len(s) // 6))
    pos = sorted(rnd.sample(range(1, len(s)), k))
    out, prev = [], 0
    for p in pos:
        out.append(s[prev:p]); out.append(rnd.choice(chars)); prev = p
    out.append(s[prev:])
    return "".join(out)


def t_case(s, rnd):
    m = rnd.choice(["lower", "upper", "alt"])
    if m == "lower":
        return s.lower()
    if m == "upper":
        return s.upper()
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(s))


def t_digit_spaces(s, rnd):
    if not any(c.isdigit() for c in s):
        return s
    sep = rnd.choice([" ", " ", "-", NBSP])
    out, run = [], 0
    for c in s:
        if c.isdigit():
            run += 1
            out.append(c)
            if run % rnd.choice([3, 4]) == 0:
                out.append(sep)
        else:
            run = 0
            out.append(c)
    return "".join(out).strip()


def t_linebreak(s, rnd):
    if len(s) < 4 or "\n" in s:
        return s
    p = rnd.randrange(2, len(s) - 1)
    return s[:p] + "\n" + s[p:]


TRANSFORMS = {"homoglyph": t_homoglyph, "invisible": t_invisible, "case": t_case,
              "digit_spaces": t_digit_spaces, "linebreak": t_linebreak}


def applicable(chunk_, rule):
    e = chunk_.get("e")
    if rule == "case":
        return e is None or e["type"] in TEXTUAL
    if rule == "digit_spaces":
        return any(c.isdigit() for c in chunk_["s"])
    return True


# ------------------------------------------------------------------ обход модели
def walk_chunks(blocks):
    for b in blocks:
        if b["t"] in ("p", "tb"):
            for ch in b["chunks"]:
                yield ch
            if b.get("fn"):
                for ch in b["fn"]:
                    yield ch
        elif b["t"] == "tbl":
            for row in b["rows"]:
                for c in row:
                    for ch in walk_chunks(c["blocks"]):
                        yield ch


def all_chunks(model):
    for key in ("header", "body", "footer"):
        if model.get(key):
            for ch in walk_chunks(model[key]):
                yield ch


def apply_simple(model, rule, rnd):
    fns = [TRANSFORMS[r] for r in (["homoglyph", "invisible", "case"] if rule == "combo" else [rule])]
    touched = 0
    for ch in all_chunks(model):
        is_ent = "e" in ch
        if not is_ent and rnd.random() > NOISE_P:
            continue
        s0 = ch["s"]
        s = s0
        for fn in fns:
            r = rule if rule != "combo" else fn.__name__[2:]
            if not applicable({"s": s, "e": ch.get("e")}, r):
                continue
            s = fn(s, rnd)
        if s == s0:
            continue
        ch["s"] = s
        touched += 1
        if is_ent:
            e = ch["e"]
            e["cat"] = "adversarial"
            e["trick"] = "mut:" + rule
            old = e.get("note") or ""
            e["note"] = ("мутация %s; исходно: %s" % (rule, old)).strip("; ")
    return touched


def find_split_target(blocks):
    """Ищем строку таблицы, где последний блок первой ячейки — абзац,
    последний чанк которого несёт сущность. Тогда после разрыва части
    сущности окажутся смежными в plain-тексте (prefix + '\\t' + suffix)."""
    for b in blocks:
        if b["t"] != "tbl":
            continue
        for row in b["rows"]:
            if len(row) < 2:
                continue
            c0, c1 = row[0], row[1]
            if not c0["blocks"] or not c1["blocks"]:
                continue
            last = c0["blocks"][-1]
            if last["t"] != "p" or not last["chunks"]:
                continue
            ch = last["chunks"][-1]
            if "e" not in ch or len(ch["s"]) < 4 or "\n" in ch["s"]:
                continue
            first = c1["blocks"][0]
            if first["t"] != "p":
                continue
            return ch, first
        for row in b["rows"]:
            for c in row:
                r = find_split_target(c["blocks"])
                if r:
                    return r
    return None


def apply_cell_split(model, rnd):
    tgt = find_split_target(model.get("body", []))
    if not tgt:
        return 0
    ch, first_para = tgt
    s = ch["s"]
    p = rnd.randrange(2, len(s) - 1)
    head, tail = s[:p], s[p:]
    ch["s"] = head
    e = ch["e"]
    e["cat"] = "adversarial"
    e["trick"] = "mut:cell_split"
    e["note"] = "мутация cell_split: сущность разорвана границей ячейки таблицы"
    suffix = {"s": tail, "e": dict(e)}
    first_para["chunks"].insert(0, suffix)
    return 1


# ------------------------------------------------------------------ основной цикл
def rules_for(model):
    r = ["homoglyph", "invisible", "case", "digit_spaces", "linebreak", "combo"]
    if model["format"] == "docx" and find_split_target(model.get("body", [])):
        r.append("cell_split")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", type=int, default=[1337, 2718])
    args = ap.parse_args()

    gold = json.load(open(os.path.join(ROOT, "gold.json"), encoding="utf-8"))
    base_ids = [g["doc_id"] for g in gold if g["source"] == "base"]
    entries, per_rule = [], {}

    for seed in args.seeds:
        for i, doc_id in enumerate(sorted(base_ids)):
            model = CL.load_model(ROOT, doc_id)
            rnd = random.Random("%s|%d" % (doc_id, seed))
            rules = rules_for(model)
            rule = rules[(i + seed) % len(rules)]
            m = copy.deepcopy(model)
            if rule == "cell_split":
                touched = apply_cell_split(m, rnd)
            else:
                touched = apply_simple(m, rule, rnd)
            if not touched:                       # правило не сработало — берём запасное
                rule = "invisible"
                m = copy.deepcopy(model)
                apply_simple(m, rule, rnd)
            m["doc_id"] = "%s__m%d_%s" % (doc_id, seed, rule)
            m["source"] = "mutated:%d:%s" % (seed, rule)
            m["features"] = sorted(set(model.get("features", [])) | {"mut:" + rule})
            render(m, ROOT)                       # запись + сверка plain-текста с файлом
            entries.append(gold_entry(m))         # <- границы ПЕРЕСЧИТАНЫ из модели
            per_rule[rule] = per_rule.get(rule, 0) + 1

    n = update_gold(ROOT, entries)
    print("мутантов: %d | всего в gold: %d" % (len(entries), n))
    for k in sorted(per_rule):
        print("  %-12s %d" % (k, per_rule[k]))


if __name__ == "__main__":
    main()
