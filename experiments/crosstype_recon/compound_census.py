# -*- coding: utf-8 -*-
"""CROSSTYPE-RECON, часть 2 — ПЕРЕПИСЬ СКЛЕЙКИ по ВСЕМУ корпусу v1 (324 док.).

Часть 1 (`per_recon.py`) считала класс линии «и» и назвала единственный
механизм — склейку «ИП + ФИО» в единый ORG. Эта часть отвечает на вопрос ЦЕНЫ:
что ещё держится на той же склейке, кроме класса. Для КАЖДОЙ составной
сущности (`Entity.shadowed` непусто на выходе `run_detection`, то есть рождена
`syntax_compound.merge_compound_entities`, а не разрешением пересечений):

  * форма ORG-стороны: аббревиатура «ИП» / «Индивидуальный предприниматель»
    прописью / РЕАЛЬНАЯ ORG-сущность (кавычки, ООО/АО и пр.);
  * какие ЭТАЛОННЫЕ сущности лежат под спаном склейки и каких они типов —
    ровно это решает, во что обойдётся смена типа склейки на PERSON;
  * сколько таких сущностей в документе.

Правок в `src/` не делает. Запуск:
  venv/Scripts/python.exe experiments/crosstype_recon/compound_census.py [N_док]
"""
import os
import re
import sys
import json
import collections
import multiprocessing as mp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

CONFIG = os.path.join(ROOT, "entity_types.yaml")
GOLD = os.path.join(ROOT, "tests", "corpus", "gold.json")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
OUT = os.path.join(HERE, "compound_census.json")

_IP_ABBR_RE = re.compile(r"(?i)^\s*ип\b")
_IP_SPELLED_RE = re.compile(r"(?i)^\s*индивидуальн\w*\s+предпринимател")
_QUOTE_RE = re.compile("[«»“”\"]")


def _overlap(a1, a2, b1, b2):
    return max(a1, b1) < min(a2, b2)


def _shape(text):
    if _IP_ABBR_RE.search(text):
        return "ип_аббревиатура"
    if _IP_SPELLED_RE.search(text):
        return "ип_прописью"
    if _QUOTE_RE.search(text):
        return "реальная_ORG_в_кавычках"
    return "иное"


def process(args):
    doc_id, fmt, gold_ents = args
    import measure_lib as ML
    from extractor import extract
    from pipeline import run_detection

    path = os.path.join(DOCS, doc_id + "." + fmt)
    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)
    doc = extract(path)
    ents = run_detection(doc, CONFIG)
    offs, _ = ML.build_segment_offsets(
        doc, G, body_start, regions=ML.part_regions(path, G))
    mapped = ML.map_entities_to_pt1(ents, offs, G)

    out = []
    n_org = 0
    for e, m in zip(ents, mapped):
        if e.entity_type == "ORG":
            n_org += 1
        if not e.shadowed:
            continue
        gs, ge = m["start"], m["end"]
        gold_over = []
        if gs is not None:
            gold_over = [(x["type"], x["text"]) for x in gold_ents
                         if _overlap(x["start"], x["end"], gs, ge)]
        out.append({
            "doc_id": doc_id, "type": e.entity_type, "text": e.original_text,
            "span": [gs, ge], "shape": _shape(e.original_text),
            "n_shadowed": len(e.shadowed),
            "shadowed_types": sorted({s.entity_type for s in e.shadowed}),
            "gold_types": sorted({t for (t, _) in gold_over}),
            "gold_n": len(gold_over),
        })
    return {"doc_id": doc_id, "n_org": n_org, "compounds": out}


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    g = json.load(open(GOLD, encoding="utf-8"))
    docs = g["documents"] if isinstance(g, dict) else g
    items = [(d["doc_id"], d["format"], d["entities"]) for d in docs]
    if limit:
        items = items[:limit]
    print("документов: %d" % len(items))
    with mp.Pool(6) as pool:
        res = pool.map(process, items)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    comps = [c for r in res for c in r["compounds"]]
    print("\nВСЕГО составных сущностей: %d в %d док. (ORG-сущностей всего %d)"
          % (len(comps), len({c["doc_id"] for c in comps}),
             sum(r["n_org"] for r in res)))
    print("\nФОРМА ORG-СТОРОНЫ:")
    for k, v in collections.Counter(c["shape"] for c in comps).most_common():
        print("  %-26s %4d" % (k, v))
    print("\nТИП СОСТАВНОЙ:")
    for k, v in collections.Counter(c["type"] for c in comps).most_common():
        print("  %-26s %4d" % (k, v))
    print("\nЭТАЛОН ПОД СПАНОМ СКЛЕЙКИ:")
    for k, v in collections.Counter(
            "+".join(c["gold_types"]) or "НИЧЕГО" for c in comps).most_common():
        print("  %-26s %4d" % (k, v))
    print("\nФОРМА x ЭТАЛОН:")
    pairs = collections.Counter(
        (c["shape"], "+".join(c["gold_types"]) or "НИЧЕГО") for c in comps)
    for (a, b), v in pairs.most_common():
        print("  %-26s %-16s %4d" % (a, b, v))
    print("\nСКОЛЬКО ЭТАЛОННЫХ СУЩНОСТЕЙ ПОД ОДНИМ СПАНОМ:")
    for k, v in sorted(collections.Counter(c["gold_n"] for c in comps).items()):
        print("  %d эталонных: %4d" % (k, v))
    print("\nJSON: %s" % OUT)


if __name__ == "__main__":
    main()
