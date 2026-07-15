# -*- coding: utf-8 -*-
"""
validate.py — независимая проверка целостности корпуса.

Читает docs/ с диска (НЕ модели), применяет правило PT-1 и сверяет каждый
спан из gold.json посимвольно. Проверяет также непересечение сущностей
с негативами. Этап 3 должен прогонять этот скрипт ПЕРЕД замером.
"""
import json
import os
import sys

from corpus_lib import extract_docx

ROOT = os.path.dirname(os.path.abspath(__file__))


def doc_text(doc_id, fmt):
    p = os.path.join(ROOT, "docs", "%s.%s" % (doc_id, fmt))
    if fmt == "txt":
        with open(p, encoding="utf-8", newline="") as f:
            return f.read()
    return extract_docx(p)


def main():
    gold = json.load(open(os.path.join(ROOT, "gold.json"), encoding="utf-8"))
    errs = []
    n_ent = n_neg = n_ign = 0
    for g in gold:
        try:
            text = doc_text(g["doc_id"], g["format"])
        except FileNotFoundError:
            errs.append("%s: файл документа отсутствует" % g["doc_id"])
            continue
        spans = []
        for e in g["entities"]:
            n_ent += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: сущность %d-%d: в файле %r, в gold %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
            spans.append((e["start"], e["end"], "ENT"))
        for e in g.get("negatives", []):
            n_neg += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: негатив %d-%d: в файле %r, в gold %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
            spans.append((e["start"], e["end"], "NEG"))
        for e in g.get("ignore", []):
            n_ign += 1
            got = text[e["start"]:e["end"]]
            if got != e["text"]:
                errs.append("%s: серая зона %d-%d: в файле %r, в gold %r"
                            % (g["doc_id"], e["start"], e["end"], got, e["text"]))
        spans.sort()
        for i in range(1, len(spans)):
            if spans[i][0] < spans[i - 1][1]:
                errs.append("%s: пересечение спанов %s и %s" % (g["doc_id"], spans[i - 1], spans[i]))
    print("документов: %d | сущностей: %d | негативов: %d | серых зон: %d"
          % (len(gold), n_ent, n_neg, n_ign))
    if errs:
        print("ОШИБОК: %d" % len(errs))
        for e in errs[:25]:
            print("  " + e)
        sys.exit(1)
    print("OK — все спаны совпали с содержимым файлов, пересечений нет")


if __name__ == "__main__":
    main()
