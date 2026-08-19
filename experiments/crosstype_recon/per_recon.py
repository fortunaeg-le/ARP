# -*- coding: utf-8 -*-
"""CROSSTYPE-RECON, часть 1 — РАЗВЕДКА БЕЗ ПРАВОК: почему человек лежит под
маской ORG.

Класс — долг `DEFGATE-CROSSTYPE-LATENT`, его PER-половина: эталонная сущность
PER, у которой на МАКСИМУМЕ нет ни одной маски СВОЕГО типа, а территория
закрыта маской ORG (линия «и» гейта, поле `single_guard` дампа).

Для каждого случая класса скрипт называет МЕХАНИЗМ, а не просто факт:

  * `compound_ip_fio`  — склейка «ИП + ФИО» в единый ORG (`syntax_compound.
    merge_compound_entities`); поглощённое имя лежит в `Entity.shadowed`;
  * `org_quoted`       — спан ORG с ИМЕНЕМ В КАВЫЧКАХ накрыл человека;
  * `orgform_run`      — спан ORG, начатый юридической формой, дотянулся до ФИО
    (name-run вправо / name-run перед реквизитом);
  * `bare_fio_key`     — спан БЕЗ формы и кавычек: ключ реестра (лемма ядра)
    выиграл арбитраж типов как ORG, и проход 2 разложил ORG по всем падежным
    вхождениям ЧЕЛОВЕКА;
  * `other`            — ни одно из выше (печатается поимённо, не прячется).

Дополнительно снимается АУДИТ РЕЕСТРА (monkeypatch `Registry.add` — только в
этом скрипте, продукт не трогается): какие заявки на ключ были и с какой
уверенностью, то есть чем именно ORG выиграл тип у PERSON.

Печатает сводку и кладёт JSON. Правок в `src/` не делает.
Запуск: venv/Scripts/python.exe experiments/crosstype_recon/per_recon.py [N_док]
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
DUMP = os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")
OUT = os.path.join(HERE, "per_recon.json")

_ORGFORM_RE = re.compile(
    r"(?i)(?:\bооо\b|\bоао\b|\bзао\b|\bпао\b|\bао\b|\bип\b|\bнко\b|\bано\b|"
    r"\bтсж\b|\bкфх\b|\bфгуп\b|\bгуп\b|\bмуп\b|\bодо\b|\bспк\b|"
    r"обществ\w*\s+с\s+ограниченн|акционерн\w+\s+обществ|"
    r"индивидуальн\w+\s+предпринимател|товариществ\w*|кооператив\w*|"
    r"учреждени\w*|предприяти\w*|фонд\w*|ассоциаци\w*|союз\w*)")
_QUOTE_RE = re.compile("[«»“”‘’\"']")


def _overlap(a1, a2, b1, b2):
    return max(a1, b1) < min(a2, b2)


def _cases_from_dump():
    """{doc_id: [случай линии «и» типа PER, закрытый ЧУЖОЙ маской]}"""
    data = json.load(open(DUMP, encoding="utf-8"))
    res = data["results"] if isinstance(data, dict) else data
    out = collections.OrderedDict()
    for r in res:
        if r.get("outcome") != "processed":
            continue
        got = [c for c in r.get("single_guard", ())
               if c["type"] == "PER" and not c.get("uncovered_max")
               and c.get("mask_types")]
        if got:
            out[r["doc_id"]] = got
    return out


def _gold_by_id():
    g = json.load(open(GOLD, encoding="utf-8"))
    docs = g["documents"] if isinstance(g, dict) else g
    return {d["doc_id"]: d for d in docs}


_AUDIT = []


def _patch_registry():
    """Аудит заявок на ключ реестра. Патч ставится ОДИН раз на процесс."""
    import anchor_registry as AR
    if getattr(AR.Registry, "_recon_patched", False):
        return
    _orig_add = AR.Registry.add

    def add(self, key, entity_type, canonical, evidence, confidence,
            canonical_src=None):
        _AUDIT.append({"key": list(key), "type": entity_type,
                       "evidence": sorted(evidence), "conf": confidence})
        return _orig_add(self, key, entity_type, canonical, evidence,
                         confidence, canonical_src=canonical_src)

    AR.Registry.add = add
    AR.Registry._recon_patched = True


def process(args):
    doc_id, cases, fmt = args
    import measure_lib as ML
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import resolve_for_masking, apply_masking
    import type_policy

    _patch_registry()

    path = os.path.join(DOCS, doc_id + "." + fmt)
    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)
    doc = extract(path)
    del _AUDIT[:]
    ents = run_detection(doc, CONFIG)
    offs, _ = ML.build_segment_offsets(
        doc, G, body_start, regions=ML.part_regions(path, G))
    resolved = resolve_for_masking(doc, ents, CONFIG)
    _, kept_max = apply_masking(doc, resolved, CONFIG, type_policy.MAXIMUM)
    _, kept_def = apply_masking(doc, resolved, CONFIG, type_policy.PERSONAL)
    m_max = ML.map_entities_to_pt1(kept_max, offs, G)
    m_def = ML.map_entities_to_pt1(kept_def, offs, G)

    claims = collections.defaultdict(list)
    for a in _AUDIT:
        claims["|".join(a["key"])].append(a)

    out = []
    for c in cases:
        gs, ge = c["start"], c["end"]
        val = G[gs:ge]
        over_max = [(e, m) for (e, m) in zip(kept_max, m_max)
                    if m["start"] is not None
                    and _overlap(m["start"], m["end"], gs, ge)]
        def_over = [(e, m) for (e, m) in zip(kept_def, m_def)
                    if m["start"] is not None
                    and _overlap(m["start"], m["end"], gs, ge)]
        rec = {"doc_id": doc_id, "start": gs, "end": ge, "value": val,
               "mask_types_max": sorted({m["gtype"] for (_, m) in over_max}),
               "mask_types_default": sorted({m["gtype"] for (_, m) in def_over}),
               "uncovered_default": c["uncovered_default"]}
        org = [(e, m) for (e, m) in over_max if m["gtype"] == "ORG"]
        if not org:
            rec["mech"] = "no_org_mask"
            out.append(rec)
            continue
        e, m = max(org, key=lambda p: p[1]["end"] - p[1]["start"])
        rec["org_text"] = e.original_text
        rec["org_span"] = [m["start"], m["end"]]
        rec["org_group"] = e.group_key
        rec["org_detector"] = e.detector
        rec["org_canonical"] = e.canonical
        rec["org_shadowed"] = [s.original_text for s in (e.shadowed or [])]
        txt = e.original_text
        if e.shadowed:
            mech = "compound_ip_fio"
        elif _QUOTE_RE.search(txt):
            mech = "org_quoted"
        elif _ORGFORM_RE.search(txt):
            mech = "orgform_run"
        elif e.group_key and e.group_key.startswith("ORG:"):
            mech = "bare_fio_key"
        else:
            mech = "other"
        rec["mech"] = mech
        if e.group_key and e.group_key.startswith("ORG:"):
            rec["key_claims"] = claims.get(e.group_key[4:], [])
        out.append(rec)
    return out


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    cases = _cases_from_dump()
    gold = _gold_by_id()
    items = [(d, c, gold[d]["format"]) for d, c in cases.items()]
    if limit:
        items = items[:limit]
    print("документов класса: %d, случаев: %d"
          % (len(items), sum(len(i[1]) for i in items)))
    with mp.Pool(6) as pool:
        chunks = pool.map(process, items)
    flat = [r for ch in chunks for r in ch]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(flat, f, ensure_ascii=False, indent=1)

    mech = collections.Counter(r["mech"] for r in flat)
    docs = collections.defaultdict(set)
    for r in flat:
        docs[r["mech"]].add(r["doc_id"])
    print("\nМЕХАНИЗМЫ (случаев %d):" % len(flat))
    for k, v in mech.most_common():
        print("  %-18s %4d  док. %d" % (k, v, len(docs[k])))
    dflt = collections.Counter(tuple(r["mask_types_default"]) for r in flat)
    print("\nЧЕМ ЗАКРЫТО В НАБОРЕ ПО УМОЛЧАНИЮ:")
    for k, v in dflt.most_common():
        print("  %-30s %4d" % ("+".join(k) or "НИЧЕМ", v))
    print("\nJSON: %s" % OUT)


if __name__ == "__main__":
    main()
