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

import values as V

ROOT = os.path.dirname(os.path.abspath(__file__))

# Виды данных, ради которых корпус V2 и делался. Список берётся из реестра, а
# не дублируется здесь: разъехавшись, копия молча перестала бы проверять
# новый вид данных.
NEW_TYPES = V.NEW_TYPES


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
    by_axis = {}
    by_trick = Counter()
    checked_new = 0

    def account(e):
        """Учесть вхождение нового вида данных (величину ИЛИ типизированный
        негатив) в покрытии осей."""
        t = e.get("type")
        if t not in NEW_TYPES:
            return None
        # ЭТАП A3: двойник (`lookalike`) — не вхождение вида данных, а похожая
        # на него конструкция другого назначения (влажность «14 %», условие
        # хранения «в течение 24 часов»). Формы в реестре values.py у него нет
        # по построению, и в покрытие осей он не входит: иначе проценты по осям
        # считались бы от вхождений, которых в реестре не существует.
        # Точность на нём меряется отдельно (measure_v2, линия двойников).
        if e.get("lookalike"):
            return None
        if not e.get("form"):
            return "без идентификатора формы записи"
        if not e.get("axes"):
            return "без набора признаков (осей)"
        by_form.setdefault(t, Counter())[e["form"]] += 1
        for name, value in e["axes"].items():
            by_axis.setdefault(t, {}).setdefault(name, Counter())[value] += 1
        if e.get("trick"):
            by_trick[e["trick"]] += 1
        return None

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
            bad = account(e)
            if bad:
                errs.append("%s: %s %d-%d %s"
                            % (g["doc_id"], e["type"], e["start"], e["end"], bad))
            spans.append((e["start"], e["end"], "ENT"))
        for e in g.get("negatives", []):
            n_neg += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: негатив %d-%d: в файле %r, в разметке %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
            elif e.get("type") in NEW_TYPES:
                checked_new += 1
            # Типизированный негатив — полноценное вхождение вида данных: он
            # участвует в покрытии осей наравне с величиной.
            bad = account(e)
            if bad:
                errs.append("%s: негатив %s %d-%d %s"
                            % (g["doc_id"], e["type"], e["start"], e["end"], bad))
            if e.get("type") in NEW_TYPES and not e.get("lookalike"):
                by_type[e["type"]] += 1
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
    print("--- новые виды данных: вхождений / разных форм (комбинаций осей) ---")
    for t in NEW_TYPES:
        forms = by_form.get(t, Counter())
        total = by_type[t]
        possible = 1
        for vals in V.AXES[t].values():
            possible *= len(vals)
        print("  %-9s %5d вхождений | %4d разных форм (предел комбинаций %d)"
              % (t, total, len(forms), possible))
        for name in V.AXIS_ORDER[t]:
            hits = by_axis.get(t, {}).get(name, Counter())
            applies = sum(hits.values())
            miss = [v for v in V.AXES[t][name] if not hits.get(v)]
            neutral = V.NEUTRAL.get((t, name))
            print("        ось «%s»%s — применима к %d из %d"
                  % (name, " [нейтраль: «%s»]" % neutral if neutral else "",
                     applies, total))
            for v in V.AXES[t][name]:
                n = hits.get(v, 0)
                print("            %-58s %4d  %5.1f%%"
                      % (v, n, 100.0 * n / total if total else 0.0))
            if miss:
                errs.append("%s: ось «%s» — значения не встретились: %s"
                            % (t, name, ", ".join(miss)))
    print("--- состязательные приёмы над новыми видами данных ---")
    for t, c in sorted(by_trick.items(), key=lambda z: -z[1]):
        print("  %-16s %4d" % (t, c))
    print("сверено координат у новых видов данных: %d" % checked_new)

    # Пометка печатается КАЖДЫЙ РАЗ и рядом с цифрами, а не только в README:
    # тот, кто смотрит на «TRANCHE 37 вхождений, 5 форм», обязан увидеть, что
    # эти цифры мерить нечем, в ту же секунду.
    tranche_digits = sum(1 for g in gold for e in g["entities"]
                         if e.get("type") == "TRANCHE"
                         and any(c.isdigit() for c in e["text"]))
    print()
    print("!" * 78)
    print("! TRANCHE: запрет детекции СНЯТ владельцем 2026-08-08 (TYPE-FACTORY-2).")
    print("!   Основание: черновик владельца составлен по конкретной документации")
    print("!   — условие сторожа («формулировки владельца из живых договоров»)")
    print("!   исполнено. Маскируется ТОЛЬКО именная ветвь (транш «Имя»,")
    print("!   typedefs/TRANCHE.yaml); порядковые и описательные обороты — ")
    print("!   негативы kind='ordinal-tranche' (решение владельца).")
    print("!   Эталонных TRANCHE: %d, форм: %d, вхождений с числом: %d."
          % (by_type["TRANCHE"], len(by_form.get("TRANCHE", ())), tranche_digits))
    print("!" * 78)
    if tranche_digits:
        errs.append("TRANCHE: появились вхождения с числом (%d). Владелец давал "
                    "только словесные именные транши; число в транше означает, "
                    "что величину сочинил генератор — это недопустимо."
                    % tranche_digits)

    # Фантом удалённой правки не имеет права появиться ни в тексте, ни в
    # разметке: он живёт только в <w:delText>. Если он всплыл — либо
    # сериализатор перестал пропускать `del`, либо реестр форм научился его
    # порождать, и класс потерь стал неизмеримым.
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
