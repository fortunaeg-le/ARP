# -*- coding: utf-8 -*-
"""causal.py — ПРИЧИННАЯ проба механизмов. РАЗВЕДКА, `src/` не трогает.

    venv/Scripts/python.exe experiments/research/per/causal.py [n_docs]

ЗАЧЕМ. Метка механизма (`multilabel.py`) говорит только «в этом значении есть
такой признак». Она НЕ доказывает, что детектор промахнулся ИЗ-ЗА него: в
корпусе мутации накладываются пачкой (`mut:combo`), и вхождение с омоглифом
может быть не найдено по совсем другой причине. Здесь признак СНИМАЕТСЯ и
детекция гоняется заново — разница и есть цена механизма.

ЧТО ДЕЛАЕТСЯ. Починки ДЛИНОСОХРАНЯЮЩИЕ (символ в символ), поэтому смещения
эталона в PT-1 остаются верны и ничего не пересчитывается:
  homo — латинские двойники букв сводятся к кириллице ВО ВСЁМ документе
         (общее правило, знания эталона не требует);
  case — регистр значения приводится к «Иванов И. И.» ВНУТРИ эталонных спанов;
  lb   — перевод строки внутри эталонного спана заменяется пробелом.

ОГОВОРКА, БЕЗ КОТОРОЙ ЧИСЛА ЧИТАТЬ НЕЛЬЗЯ. `case` и `lb` применяются ПО
ЭТАЛОНУ, то есть чинят ровно там, где надо, и нигде больше. Это ВЕРХНЯЯ ГРАНИЦА
механизма — «если бы правило работало идеально и без ложных срабатываний».
Настоящее правило столько не даст. `homo` границей не является: оно применено
вслепую ко всему тексту, как это и делал бы нормализатор.
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
sys.path.insert(0, os.path.join(ROOT, "src"))

import measure_lib as ML                      # noqa: E402
import by_entity as BE                        # noqa: E402
import run_measurement as RM                  # noqa: E402
from extractor import extract                 # noqa: E402
from pipeline import run_detection            # noqa: E402
from tokenizer import resolve_for_masking, apply_masking  # noqa: E402
import type_policy                            # noqa: E402

#: латинские буквы, которыми подменяют кириллические (свод обратно)
LAT2CYR = {"A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
           "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
           "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
           "y": "у", "s": "ѕ", "i": "і"}
CYR_RE = re.compile(r"[Ѐ-ӿ]")


def fix_homo(s):
    """Латиница внутри кириллического слова -> кириллица. Длина сохраняется."""
    out = []
    for w in re.split(r"(\s+)", s):
        if CYR_RE.search(w):
            w = "".join(LAT2CYR.get(c, c) for c in w)
        out.append(w)
    return "".join(out)


def fix_case(s):
    """Каждое слово к виду «Иванов». Длина сохраняется."""
    return "".join(
        (w[:1].upper() + w[1:].lower()) if w.strip() else w
        for w in re.split(r"(\s+)", s))


def fix_lb(s):
    return s.replace("\n", " ").replace("\r", " ")


VARIANTS = ["base", "homo", "case", "lb", "all"]


def _apply(variant, G, doc, gold_spans):
    """Вернуть (G', doc') с применённой починкой. Длины не меняются."""
    if variant == "base":
        return G, doc
    # 1) сплошные починки (по всему тексту)
    def whole(s):
        return fix_homo(s) if variant in ("homo", "all") else s
    # 2) починки ПО ЭТАЛОНУ — нужны позиции сегментов в PT-1
    G2 = whole(G)
    segs = {}
    for seg in doc.segments:
        segs[seg.id] = whole(seg.text)

    if variant in ("case", "lb", "all"):
        fns = []
        if variant in ("case", "all"):
            fns.append(fix_case)
        if variant in ("lb", "all"):
            fns.append(fix_lb)

        def rep(s):
            for f in fns:
                s = f(s)
            return s
        # правим PT-1 в спанах эталона
        buf = list(G2)
        for a, b in gold_spans:
            frag = rep("".join(buf[a:b]))
            if len(frag) == b - a:
                buf[a:b] = list(frag)
        G2 = "".join(buf)
        # те же спаны в сегментах: сегмент лежит в PT-1 отрезком [off, off+len)
        for seg in doc.segments:
            off = doc._probe_offs.get(seg.id)
            if off is None:
                continue
            t = list(segs[seg.id])
            for a, b in gold_spans:
                lo, hi = max(a, off), min(b, off + len(t))
                if lo >= hi:
                    continue
                frag = rep("".join(t[lo - off:hi - off]))
                if len(frag) == hi - lo:
                    t[lo - off:hi - off] = list(frag)
            segs[seg.id] = "".join(t)

    for seg in doc.segments:
        seg.text = segs[seg.id]
    return G2, doc


def probe_doc(d):
    """Для одного документа: по каждому варианту — found/leak каждой PER-эталонной."""
    doc_id = d["doc_id"]
    path = os.path.join(RM.DOCS, doc_id + "." + d["format"])
    gold_per = [(i, e) for i, e in enumerate(d["entities"]) if e["type"] == "PER"]
    if not gold_per:
        return None
    G0 = ML.pt1_text(path)
    body_start = ML.body_offset(path, G0)
    regions = ML.part_regions(path, G0)
    gold_spans = [(e["start"], e["end"]) for _, e in gold_per]

    res = {"doc_id": doc_id, "gold": [e["text"] for _, e in gold_per],
           "types": {}}
    for variant in VARIANTS:
        doc = extract(path)
        offs0, _ = ML.build_segment_offsets(doc, G0, body_start, regions=regions)
        doc._probe_offs = offs0
        G, doc = _apply(variant, G0, doc, gold_spans)
        try:
            ents = run_detection(doc, RM.CONFIG)
            resolved = resolve_for_masking(doc, ents, RM.CONFIG)
            anon, kept = apply_masking(doc, resolved, RM.CONFIG, type_policy.MAXIMUM)
        except Exception as exc:                       # noqa: BLE001
            res["types"][variant] = {"error": repr(exc)[:120]}
            continue
        offs, _ = ML.build_segment_offsets(doc, G, body_start, regions=regions)
        det = [x for x in ML.map_entities_to_pt1(kept, offs, G)
               if x["start"] is not None]
        anon_v2 = ML.v2_norm_text(anon)
        rows = []
        for (_i, e), (gs, ge) in zip(gold_per, gold_spans):
            same = [x for x in det if x["gtype"] == "PER"
                    and RM._overlap(gs, ge, x["start"], x["end"])]
            gtext = G[gs:ge]
            v2 = RM.leak_v2("PER", gtext, anon_v2, "", "", anon_runs=None)
            hit6, _ = ML._leak_v2_hits("PER", v2)
            rows.append({"text": gtext, "found": bool(same), "leak": bool(hit6)})
        res["types"][variant] = rows
    return res


def _safe(d):
    try:
        return probe_doc(d)
    except Exception as exc:                           # noqa: BLE001
        return {"doc_id": d["doc_id"], "fatal": repr(exc)[:200]}


def main():
    # ЗАЩИТА ТОЧКИ ВХОДА ОБЯЗАТЕЛЬНА (правило сессии): без неё воркеры Pool на
    # Windows реимпортируют скрипт как __main__ и он запускает сам себя.
    import multiprocessing as mp
    gold = RM.load_gold()
    docs = [d for d in gold if any(e["type"] == "PER" for e in d["entities"])]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(docs)
    docs = docs[:limit]
    out = []
    with mp.Pool(processes=min(8, (os.cpu_count() or 2))) as pool:
        for i, r in enumerate(pool.imap_unordered(_safe, docs, chunksize=1)):
            if r:
                out.append(r)
            if (i + 1) % 25 == 0:
                print("%d/%d" % (i + 1, len(docs)), flush=True)
    json.dump(out, io.open(os.path.join(HERE, "causal.json"), "w",
                           encoding="utf-8"), ensure_ascii=False)
    print("docs: %d" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
