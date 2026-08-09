# -*- coding: utf-8 -*-
"""
R-GUARANTEE, шаг 1: прогон корпуса v1 с ЗАПИСЬЮ признака уверенности и
пост-хок развёрткой по порогу lambda.

РАЗВЕДКА. Код в продукт не идёт, src/ не меняется. Признак уверенности
снимается ПАТЧЕМ поверх импортированных модулей (обезьяний патч живёт только
внутри этого процесса).

Что считается:
  * детекция гоняется ОДИН раз на документ (она дорогая ~2 c/док);
  * для каждого lambda из LAMBDAS сущности якорного движка (ORG/PERSON) с
    уверенностью реестра < lambda ВЫБРАСЫВАЮТСЯ, дальше обычные tokenize +
    метрика утечки;
  * единица риска — СУЩНОСТЬ (группа вхождений одного значения одного типа
    внутри документа), утёкшей считается та, у которой утекло хотя бы одно
    вхождение (leak_v2.status != none);
  * рядом считается перемаскирование (маска, не пересёкшая эталон своего типа)
    — вторая половина асимметричной функции потерь.

Выход: results_lambda.json — по документу, по lambda.
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, CORPUS)

LAMBDAS = [1, 2, 3, 4]
OUT = os.path.join(HERE, "results_lambda.json")


# --------------------------------------------------------------------------- #
#            ПАТЧ: снять уверенность якоря на каждой порождённой группе        #
# --------------------------------------------------------------------------- #
CONF_BY_GROUP: dict = {}
_CUR = {"conf": None}


def _install_patch():
    import anchor_registry as AR

    _orig_get = AR.Registry.get

    def _get(self, key):
        rec = _orig_get(self, key)
        if rec is not None:
            _CUR["conf"] = rec.confidence
        return rec

    AR.Registry.get = _get

    _orig_match = AR.AnchorEngine._match_key

    def _match(self, entities, emitted_src, seg, norm, low, omap,
               tokens, lemsets, npar, rec, det, group_of):
        _CUR["conf"] = rec.confidence
        return _orig_match(self, entities, emitted_src, seg, norm, low, omap,
                           tokens, lemsets, npar, rec, det, group_of)

    AR.AnchorEngine._match_key = _match

    _orig_emit = AR.AnchorEngine._emit.__func__ \
        if hasattr(AR.AnchorEngine._emit, "__func__") else AR.AnchorEngine._emit

    def _emit(entities, emitted_src, seg, norm, omap, ns, ne, entity_type,
              group_key=None):
        n0 = len(entities)
        _orig_emit(entities, emitted_src, seg, norm, omap, ns, ne, entity_type,
                   group_key=group_key)
        c = _CUR["conf"]
        if c is None:
            return
        for e in entities[n0:]:
            gk = e.group_key
            if gk is None:
                continue
            prev = CONF_BY_GROUP.get(gk)
            CONF_BY_GROUP[gk] = c if prev is None else max(prev, c)

    AR.AnchorEngine._emit = staticmethod(_emit)


_install_patch()

import measure_lib as ML            # noqa: E402
import run_measurement as RM        # noqa: E402
from extractor import extract       # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize      # noqa: E402
import type_policy                  # noqa: E402


def _overlap(a0, a1, b0, b1):
    return max(a0, b0) < min(a1, b1)


def process_doc(d):
    doc_id = d["doc_id"]
    path = os.path.join(CORPUS, "docs", doc_id + "." + d["format"])

    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)

    doc = extract(path)
    CONF_BY_GROUP.clear()
    entities = run_detection(doc, RM.CONFIG)
    conf_map = dict(CONF_BY_GROUP)

    offs, _ = ML.build_segment_offsets(doc, G, body_start)

    # эталонные ВХОЖДЕНИЯ -> СУЩНОСТИ (единица риска)
    ent_key = []
    for e in d["entities"]:
        ent_key.append((e["type"], ML.norm_text(e["text"])))

    out = {"doc_id": doc_id, "format": d["format"], "source": d["source"],
           "contract_type": d.get("contract_type", ""),
           "n_gold_mentions": len(d["entities"]),
           "n_gold_entities": len(set(ent_key)),
           "by_lambda": {}}

    for lam in LAMBDAS:
        keep = [e for e in entities
                if e.group_key is None or conf_map.get(e.group_key, 99) >= lam]
        anon_text, kept = tokenize(doc, keep, RM.CONFIG,
                                   enabled_types=type_policy.MAXIMUM)
        anon_norm = ML.norm_text(anon_text)
        anon_glue = RM._GLUE_RE.sub("", anon_norm)
        anon_norm_v2 = ML.v2_norm_text(anon_text)
        anon_digit_field = ML.v2_digit_runs(anon_text)
        anon_date_field = ML.v2_date_field(anon_text)

        det = ML.map_entities_to_pt1(kept, offs, G)
        det_located = [x for x in det if x["start"] is not None]

        leaked_ent = {}
        for (k, e) in zip(ent_key, d["entities"]):
            v2 = RM.leak_v2(e["type"], e["text"], anon_norm_v2,
                            anon_digit_field, anon_date_field)
            v1 = RM.leak_pieces(e["type"], e["text"], anon_norm, anon_glue)
            prev = leaked_ent.get(k, (False, False))
            leaked_ent[k] = (prev[0] or v2["status"] != "none",
                             prev[1] or bool(v1))

        # перемаскирование: маска, не пересёкшая эталон СВОЕГО типа
        over = 0
        for x in det_located:
            hit = any(x["gtype"] == e["type"]
                      and _overlap(x["start"], x["end"], e["start"], e["end"])
                      for e in d["entities"])
            if not hit:
                over += 1

        n_ent = len(leaked_ent)
        out["by_lambda"][str(lam)] = {
            "n_entities": n_ent,
            "leaked_v2": sum(1 for v in leaked_ent.values() if v[0]),
            "leaked_v1": sum(1 for v in leaked_ent.values() if v[1]),
            "n_masks": len(det_located),
            "overmask": over,
            "leaked_by_type": _by_type(leaked_ent),
        }
    return out


def _by_type(leaked_ent):
    agg = {}
    for (t, _), v in leaked_ent.items():
        a = agg.setdefault(t, [0, 0])
        a[0] += 1
        a[1] += 1 if v[0] else 0
    return agg


def _safe(d):
    try:
        return process_doc(d)
    except Exception as ex:      # документ-креш не обрывает прогон
        return {"doc_id": d["doc_id"], "crashed": repr(ex)[:300]}


def main():
    gold = RM.load_gold()
    t0 = time.time()
    import multiprocessing as mp
    workers = RM._default_workers()
    print(f"docs={len(gold)} workers={workers} lambdas={LAMBDAS}", flush=True)
    res = []
    with mp.Pool(workers) as pool:
        for i, r in enumerate(pool.imap(_safe, gold)):
            res.append(r)
            if (i + 1) % 25 == 0:
                print(f"[{i+1}/{len(gold)}] {time.time()-t0:.0f}s", flush=True)
    json.dump({"lambdas": LAMBDAS, "docs": res}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False)
    n_cr = sum(1 for r in res if "crashed" in r)
    print(f"done {time.time()-t0:.0f}s  crashed={n_cr}  -> {OUT}")


if __name__ == "__main__":
    main()
