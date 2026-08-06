# -*- coding: utf-8 -*-
"""Сколько РАЗНОГО измерено: видов случая на тип и различных текстов на вид.

Вопрос владельца по итогам первого замера: «138 из 138 по девяти типам сразу —
подозрительно ровно; это ОДНА конструкция, повторённая в 138 документах, или
разные случаи внутри типа?». Прибор отвечает числом, а не рассуждением:

  видов случая  — сколько РАЗНЫХ отрицательных примеров заведено на тип
                  (различаются по причине `why`);
  разных текстов — сколько различных строк реально попало в документы:
                  138 означает «значение своё в каждом документе» (цифры
                  случайные), 1 означает «одна и та же строка 138 раз»;
  разных окружений — сколько различных 40-символьных окон СЛЕВА от значения.
                  Именно левое окно решает у якорных типов, и если оно одно —
                  измерена одна формулировка, а не класс.

Запуск:
    venv/Scripts/python.exe experiments/stage_a3/variety.py
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GOLD = os.path.join(ROOT, "tests", "corpus_v2", "gold_v2.json")
DOCS = os.path.join(ROOT, "tests", "corpus_v2", "docs")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def doc_text(doc_id, fmt):
    if fmt == "txt":
        with open(os.path.join(DOCS, doc_id + ".txt"), encoding="utf-8") as f:
            return f.read()
    # .docx: текст берём из модели через уже собранный эталон — здесь он не
    # нужен, левое окно считается только по txt-документам (их 60+, хватает).
    return None


def main():
    gold = json.load(open(GOLD, encoding="utf-8"))
    kinds = collections.defaultdict(set)          # тип -> {why}
    texts = collections.defaultdict(set)          # (тип, why) -> {текст}
    lefts = collections.defaultdict(set)          # (тип, why) -> {левое окно}
    docs = collections.Counter()                  # (тип, why) -> в скольких док.
    per_doc = collections.defaultdict(collections.Counter)

    for d in gold:
        text = doc_text(d["doc_id"], d["format"])
        for n in d.get("negatives", []):
            if not n.get("lookalike"):
                continue
            key = (n["type"], n["why"][:44])
            kinds[n["type"]].add(n["why"][:44])
            texts[key].add(n["text"])
            docs[key] += 1
            per_doc[n["type"]][d["doc_id"]] += 1
            if text is not None:
                lefts[key].add(text[max(0, n["start"] - 40):n["start"]])

    print("=== ЧТО ИМЕННО ИЗМЕРЕНО: РАЗНООБРАЗИЕ ОТРИЦАТЕЛЬНЫХ ПРИМЕРОВ ===\n")
    print("  %-9s %6s %7s %9s %9s  %s"
          % ("тип", "видов", "вхожд.", "текстов", "окружений", "вид случая"))
    for t in sorted(kinds):
        first = True
        for why in sorted(kinds[t]):
            key = (t, why)
            print("  %-9s %6s %7d %9d %9d  %s"
                  % (t if first else "", len(kinds[t]) if first else "",
                     docs[key], len(texts[key]), len(lefts[key]), why))
            first = False
    print("\n  вхождений на документ (макс.): %s"
          % ", ".join("%s=%d" % (t, max(c.values()))
                      for t, c in sorted(per_doc.items())))


if __name__ == "__main__":
    main()
