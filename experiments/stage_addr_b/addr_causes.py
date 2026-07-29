# -*- coding: utf-8 -*-
"""
addr_causes.py — группировка недоборов/переборов границ адреса ПО ПРИЧИНАМ.

    venv/Scripts/python.exe experiments/stage_addr_b/addr_causes.py probe_full_before.json [--show N]

Классификатор СТРУКТУРНЫЙ (по форме открытого/лишнего куска и по виду мутации
документа), приоритет сверху вниз — случай попадает в первую подошедшую группу.
Он нужен, чтобы чинить ПРИЧИНЫ, а не отдельные документы: без разбивки правка
границ — угадывание.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# Аббревиатура типа улицы/помещения с точкой на ХВОСТЕ открытого куска:
# маска началась ПОСЛЕ неё («бул. Победы» -> закрыто только «Победы»).
ABBR_TAIL = re.compile(r"(?i)(?<![а-яёa-z])[а-яёa-z]{2,10}\.[\s ]*$")
PO_BOX = re.compile(r"(?i)а\s*[/\\]\s*я")
# Ведущая 6-символьная группа «индекс», в которой есть буква (омоглиф) или дефис
BAD_INDEX = re.compile(r"^[\s,]*(?=[^\s,]{5,9}[\s,])(?=[^\s,]*\d)[0-9A-Za-zА-Яа-яЁё\-­]{5,9}\s*,")
HIGHWAY = re.compile(r"(?i)автодорог|трасс")


def significant(pieces):
    return [p for p in pieces if any(c.isalnum() for c in p)]


def mutation(src):
    return src.split(":")[-1] if ":" in src else "base"


def cause_under(e):
    """Причина НЕДОБОРА (маска короче эталона)."""
    op = significant(e["open_pieces"])
    if not op:
        return "U0-хвостовая-пунктуация"
    joined = "".join(op)
    mut = mutation(e["source"])
    if HIGHWAY.search(e["gold_text"]):
        return "U5-трасса-«км автодороги»"
    if PO_BOX.search(joined):
        return "U2-а/я срезан обрезкой краёв"
    if any("\n" in p or "\r" in p for p in e["open_pieces"]):
        return "U4-разрыв строкой внутри адреса"
    if ABBR_TAIL.search(op[0]):
        return "U1-аббревиатура типа улицы не опознана"
    if BAD_INDEX.match(op[0]):
        return "U3-индекс испорчен мутацией (омоглиф/дефис)"
    if mut in ("case", "combo"):
        return "U6-регистровая мутация"
    if mut == "homoglyph":
        return "U7-омоглиф в теле адреса"
    return "U9-прочее"


LABEL_TAIL = re.compile(r":\s*$")


def cause_over(e):
    """Причина ПЕРЕБОРА (маска длиннее эталона)."""
    ex = significant(e["extra_pieces"])
    if not ex:
        return "O0-краевая пунктуация"
    if any(LABEL_TAIL.search(p) for p in ex):
        return "O1-метка поля («Почтовый адрес:»)"
    if e["over_on_other_gold"] > 0:
        return "O2-соседний реквизит/сущность поглощены"
    if any("\n" in p or "\r" in p for p in e["extra_pieces"]):
        return "O3-перелезли через перенос строки"
    return "O9-прочее"


def main():
    path = sys.argv[1]
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    show = 0
    if "--show" in sys.argv:
        show = int(sys.argv[sys.argv.index("--show") + 1])
    dump = json.load(open(path, encoding="utf-8"))
    ents = [e for d in dump for e in d["entities"]]

    for title, sel, fn in (
        ("НЕДОБОР (маска КОРОЧЕ эталона — утечка)",
         lambda e: e["direction"] in ("shorter", "both"), cause_under),
        ("ПЕРЕБОР (маска ДЛИННЕЕ эталона — лишний текст)",
         lambda e: e["direction"] in ("longer", "both"), cause_over),
    ):
        sub = [e for e in ents if sel(e)]
        groups = defaultdict(list)
        for e in sub:
            groups[fn(e)].append(e)
        print(f"\n=== {title}: {len(sub)} случаев ===")
        for g in sorted(groups, key=lambda g: -len(groups[g])):
            items = groups[g]
            cats = Counter(x["category"] for x in items)
            muts = Counter(mutation(x["source"]) for x in items)
            print("  %-42s %4d  [%s]  мутации: %s"
                  % (g, len(items),
                     " ".join("%s=%d" % (c, n) for c, n in cats.most_common()),
                     " ".join("%s=%d" % (m, n) for m, n in muts.most_common(4))))
            for e in items[:show]:
                print("      %s | эталон %r" % (e["doc_id"], e["gold_text"][:90]))
                print("        маска %s" % (" + ".join(repr(t) for t in e["mask_texts"])[:100]))
                print("        %s %s" % (
                    "открыто" if fn is cause_under else "лишнее",
                    " | ".join(repr(p) for p in (
                        e["open_pieces"] if fn is cause_under else e["extra_pieces"]))[:110]))

    # «не найдено» — отдельная колонка, это не границы, а полнота
    nf = [e for e in ents if e["direction"] == "not_found"]
    print(f"\n=== НЕ НАЙДЕНО (полнота, не границы): {len(nf)} ===")
    print("  категории:", dict(Counter(e["category"] for e in nf)))
    print("  мутации:  ", dict(Counter(mutation(e["source"]) for e in nf)))


if __name__ == "__main__":
    main()
