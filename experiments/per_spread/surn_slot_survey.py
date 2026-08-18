# -*- coding: utf-8 -*-
"""Разведка F1: какие ТОКЕНЫ подобрало бы правило «слот фамилии» —
Titlecase-слово, ВПЛОТНУЮ примыкающее к паре имя+отчество, у которого САМОГО
морфология не даёт пометы Surn. Печатает поимённый список кандидатов по всему
корпусу: если в нём мусор (должности, org-формы), правило не годится как есть."""
import glob, json, os, re, sys, collections
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from extractor import extract
import anchor_registry as AR

gold = json.load(open(os.path.join(ROOT, "tests/corpus/gold.json"), encoding="utf-8"))
only_clean = "--all" not in sys.argv
cand = collections.Counter()
ctx = collections.defaultdict(list)
for g in gold:
    if only_clean and "__m" in g["doc_id"]:
        continue
    path = glob.glob(os.path.join(ROOT, "tests/corpus/docs", g["doc_id"] + ".*"))[0]
    doc = extract(path)
    for seg in doc.segments:
        if not seg.text:
            continue
        norm, omap = AR.per_search_view(seg)
        tokens = list(AR._WORD_RE.finditer(norm))
        npar = [AR._nameparts(t.group(0)) for t in tokens]
        for i, t in enumerate(tokens):
            if "name" not in npar[i]:
                continue
            # ищем пару имя(i) + отчество(i+1) вплотную
            if i + 1 >= len(tokens) or "patr" not in npar[i + 1]:
                continue
            if norm[tokens[i].end():tokens[i + 1].start()].strip(" \t."):
                continue
            for j, side in ((i - 1, "left"), (i + 2, "right")):
                if not (0 <= j < len(tokens)):
                    continue
                w = tokens[j].group(0)
                if npar[j] or AR._is_initial(w):
                    continue          # уже часть прогона
                a, b = (j, i) if side == "left" else (i + 1, j)
                if norm[tokens[a].end():tokens[b].start()].strip(" \t."):
                    continue
                if not w[:1].isupper() or len(w) < 2:
                    continue
                cand[(w.lower(), side)] += 1
                if len(ctx[(w.lower(), side)]) < 3:
                    s = max(0, tokens[j].start() - 40)
                    ctx[(w.lower(), side)].append(
                        g["doc_id"] + ": " + norm[s:tokens[i + 1].end() + 20].replace("\n", " "))
print("КАНДИДАТЫ В СЛОТ ФАМИЛИИ (%d уникальных):" % len(cand))
for (w, side), c in cand.most_common():
    print("  %5d  %-6s %-22s | %s" % (c, side, w, ctx[(w, side)][0]))
