# -*- coding: utf-8 -*-
"""
validate.py (КОРПУС V2) — независимая проверка целостности корпуса.

ЭТО ПРОВЕРКА КООРДИНАТ, которой требует задача 1 сессии CORPUS-V2:
берём координаты ИЗ РАЗМЕТКИ, вырезаем кусок ТЕКСТА ИЗ ФАЙЛА НА ДИСКЕ и
сравниваем с тем, что разметка объявила вставленным. Расхождений должно быть
ноль. Проверка механическая: ничего не ищется, ничего не нормализуется.

Независимость от генератора существенна: файл читается с диска эталонным
экстрактором (правило PT-1), а не берётся из модели. Дополнительно
проверяется, что спаны не пересекаются и что у КАЖДОЙ величины нового вида
есть идентификатор формы записи — без него тест разнообразия считал бы формы,
которых не видит.
"""
import json
import os
import sys
from collections import Counter

from corpus_lib import extract_docx, GOLD_NAME

ROOT = os.path.dirname(os.path.abspath(__file__))

# Виды данных, ради которых корпус V2 и делался.
NEW_TYPES = ("MONEY", "PERCENT", "TERM", "TRANCHE")


def doc_text(doc_id, fmt):
    p = os.path.join(ROOT, "docs", "%s.%s" % (doc_id, fmt))
    if fmt == "txt":
        with open(p, encoding="utf-8", newline="") as f:
            return f.read()
    return extract_docx(p)


def main():
    gold = json.load(open(os.path.join(ROOT, GOLD_NAME), encoding="utf-8"))
    errs = []
    n_ent = n_neg = n_ign = 0
    by_type = Counter()
    by_form = {}
    by_group = Counter()
    checked_new = 0

    for g in gold:
        by_group[g["structure_group"]] += 1
        try:
            text = doc_text(g["doc_id"], g["format"])
        except FileNotFoundError:
            errs.append("%s: файл документа отсутствует" % g["doc_id"])
            continue
        spans = []
        for e in g["entities"]:
            n_ent += 1
            by_type[e["type"]] += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: сущность %d-%d: в файле %r, в разметке %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
            elif e["type"] in NEW_TYPES:
                checked_new += 1
            if e["type"] in NEW_TYPES:
                if not e.get("form"):
                    errs.append("%s: %s %d-%d без идентификатора формы записи"
                                % (g["doc_id"], e["type"], e["start"], e["end"]))
                else:
                    by_form.setdefault(e["type"], Counter())[e["form"]] += 1
            spans.append((e["start"], e["end"], "ENT"))
        for e in g.get("negatives", []):
            n_neg += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: негатив %d-%d: в файле %r, в разметке %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
            spans.append((e["start"], e["end"], "NEG"))
        for e in g.get("ignore", []):
            n_ign += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: серая зона %d-%d: в файле %r, в разметке %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
        spans.sort()
        for i in range(1, len(spans)):
            if spans[i][0] < spans[i - 1][1]:
                errs.append("%s: пересечение спанов %s и %s"
                            % (g["doc_id"], spans[i - 1], spans[i]))

    print("документов: %d (простая %d, сложная %d) | сущностей: %d | негативов: %d "
          "| серых зон: %d" % (len(gold), by_group["simple"], by_group["complex"],
                               n_ent, n_neg, n_ign))
    print("--- новые виды данных: вхождений / разных форм записи ---")
    for t in NEW_TYPES:
        forms = by_form.get(t, Counter())
        print("  %-9s %5d вхождений | %d разных форм" % (t, by_type[t], len(forms)))
        for f, c in sorted(forms.items()):
            print("        %-34s %4d" % (f, c))
    print("сверено координат у новых видов данных: %d" % checked_new)

    # Фантом удалённой правки не имеет права появиться ни в тексте, ни в
    # разметке: он живёт только в <w:delText>. Если он всплыл — либо
    # сериализатор перестал пропускать `del`, либо реестр форм научился его
    # порождать, и класс потерь стал неизмеримым.
    import values as V
    ghost_hits = []
    for g in gold:
        text = doc_text(g["doc_id"], g["format"])
        if V.DEL_GHOST in text:
            ghost_hits.append(g["doc_id"] + " (в тексте)")
        for e in g["entities"]:
            if V.DEL_GHOST in e["text"]:
                ghost_hits.append(g["doc_id"] + " (в разметке)")
    print("фантом удалённой правки в тексте/разметке: %d (должно быть 0)"
          % len(ghost_hits))
    if ghost_hits:
        errs.append("фантом удалённого текста виден: %s" % ", ".join(ghost_hits[:5]))

    if errs:
        print("ОШИБОК: %d" % len(errs))
        for e in errs[:25]:
            print("  " + e)
        sys.exit(1)
    print("OK — все спаны совпали с содержимым файлов, пересечений нет, "
          "у каждой новой величины есть идентификатор формы")


if __name__ == "__main__":
    main()
