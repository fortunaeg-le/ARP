# -*- coding: utf-8 -*-
"""ЭТАП E — приёмка 1a/1b на корпусе (подвыборка или полный, --full).

1a СТРУКТУРНЫЙ (инвариант, 100% или СТОП):
   * exact-восстановление ПОБАЙТНО равно build_plain_text (текст между масками,
     разделители внутри мультиспан-hull, счёт вхождений — всё покрывается
     побайтным равенством целиком);
   * число вхождений токенов в anon = числу kept-сущностей;
   * после восстановления токенов не осталось; unresolved пуст.
   Исключение — известный класс Aprime-3 (`\\n`->' ' в ячейках таблиц, ПРЕД-
   существующая асимметрия _render_table, не этап E): считается отдельно как
   ws-only и НЕ засчитывается провалом 1a (как в masking A).

1b ТОЧНЫЙ (canonical, падение ожидается ТОЛЬКО от канона):
   * canonical-восстановление сравнивается с exact-восстановлением;
   * КАЖДОЕ расхождение обязано быть заменой surface->canonical какой-то маски
     (проверяется конструктивно: exact с точечной заменой canon в позициях масок
     == canonical-восстановление);
   * счёт расхождений и топ примеров (типы/токены) — в отчёт.

Также: перепись мультиспан-сборок (N>1) поимённо по документам.
"""
import os
import re
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize, build_plain_text  # noqa: E402
from session_store import save_session, load_session  # noqa: E402
from detokenizer import detokenize  # noqa: E402
import run_measurement as RM  # noqa: E402

CFG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
STORAGE = tempfile.mkdtemp(prefix="shifrator_e_")
TOKEN_RE = re.compile(r"\[[A-Z_]+_\d+\]")
WS = {" ", "\n", "\t", "\r"}


def check_doc(doc_id, fmt):
    path = os.path.join(DOCS, doc_id + "." + fmt)
    doc = extract(path)
    entities = run_detection(doc, CFG)
    anon, kept = tokenize(doc, entities, CFG)
    plain = build_plain_text(doc)
    sid = save_session(kept, storage_dir=STORAGE)

    res = {"doc_id": doc_id, "n_masks": len(kept)}

    # --- 1a структурный ---
    exact, unres = detokenize(anon, sid, storage_dir=STORAGE, mode="exact")
    res["unresolved"] = len(unres)
    res["tokens_left"] = len(TOKEN_RE.findall(exact))
    res["mask_count_ok"] = (len(TOKEN_RE.findall(anon)) == len(kept))
    if exact == plain:
        res["structural"] = "ok"
    else:
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(exact, plain)) if a != b]
        ws_only = (len(exact) == len(plain)) and all(
            a in WS and b in WS for _, a, b in diffs)
        res["structural"] = "ws_only" if ws_only else "FAIL"
        if res["structural"] == "FAIL":
            i0 = diffs[0][0] if diffs else min(len(exact), len(plain))
            res["fail_sample"] = (i0, repr(exact[max(0, i0-30):i0+30]),
                                  repr(plain[max(0, i0-30):i0+30]))

    # --- 1b канонический ---
    sess = load_session(sid, storage_dir=STORAGE)
    canon_map = {r["token"]: (r.get("canonical") or r["original_text"])
                 for r in sess["entities"]}
    surf_map = {r["token"]: [o["surface"] for o in (r.get("occurrences") or [])]
                for r in sess["entities"]}
    canonical, _ = detokenize(anon, sid, storage_dir=STORAGE)  # canonical
    # конструктивная проверка: canonical == anon с заменой каждого токена на канон
    expected = TOKEN_RE.sub(lambda m: canon_map.get(m.group(0), m.group(0)), anon)
    res["canon_construction_ok"] = (canonical == expected)
    # счёт замен канона: вхождения, чья surface != canonical
    n_sub = 0
    subs_by_type = {}
    for r in sess["entities"]:
        c = canon_map[r["token"]]
        for s in surf_map[r["token"]]:
            if s != c:
                n_sub += 1
                subs_by_type[r["entity_type"]] = subs_by_type.get(r["entity_type"], 0) + 1
    res["canon_substitutions"] = n_sub
    res["canon_subs_by_type"] = subs_by_type

    # --- мультиспан-сборки ---
    ms = [e for e in kept if e.spans and len(e.spans) > 1]
    res["multispan"] = [(e.entity_type, e.token, e.original_text) for e in ms]
    return res


def main():
    gold = RM.load_gold()
    if "--full" in sys.argv:
        docs = [(d["doc_id"], d["format"]) for d in gold]
    else:
        # репрезентативная подвыборка: чистые + мутационные, разножанровые
        picks = []
        seen_kind = set()
        for d in gold:
            base = d["doc_id"].split("__")[0]
            mut = d["doc_id"][len(base):] or "clean"
            genre = base.split("_")[0]
            k = (genre, mut)
            if k in seen_kind:
                continue
            seen_kind.add(k)
            picks.append((d["doc_id"], d["format"]))
        docs = picks
    print("docs to check: %d" % len(docs))

    stats = {"ok": 0, "ws_only": 0, "FAIL": 0}
    fails, ms_docs = [], []
    tot_subs, subs_by_type = 0, {}
    bad_counts = 0
    for doc_id, fmt in docs:
        try:
            r = check_doc(doc_id, fmt)
        except Exception as exc:
            stats["FAIL"] += 1
            fails.append((doc_id, "CRASH: %r" % exc))
            continue
        stats[r["structural"]] += 1
        if r["structural"] == "FAIL":
            fails.append((doc_id, r.get("fail_sample")))
        if not r["mask_count_ok"] or r["unresolved"] or r["tokens_left"] \
                or not r["canon_construction_ok"]:
            bad_counts += 1
            fails.append((doc_id, "counts/construction: %s" % {
                k: r[k] for k in ("mask_count_ok", "unresolved", "tokens_left",
                                  "canon_construction_ok")}))
        tot_subs += r["canon_substitutions"]
        for t, n in r["canon_subs_by_type"].items():
            subs_by_type[t] = subs_by_type.get(t, 0) + n
        if r["multispan"]:
            ms_docs.append((doc_id, r["multispan"]))

    print("\n=== 1a СТРУКТУРНЫЙ ===")
    print("ok=%d  ws_only(Aprime-3, пред-существующий)=%d  FAIL=%d  побочные=%d"
          % (stats["ok"], stats["ws_only"], stats["FAIL"], bad_counts))
    for f in fails[:20]:
        print("  FAIL:", f)
    print("\n=== 1b КАНОН ===")
    print("замен канона всего: %d; по типам: %s" % (tot_subs, subs_by_type))
    print("канон-конструкция сошлась во всех документах:", bad_counts == 0)
    print("\n=== МУЛЬТИСПАН (N>1) — поимённо ===")
    print("документов с мультиспаном: %d" % len(ms_docs))
    for doc_id, ms in ms_docs:
        for (t, tok, txt) in ms:
            print("  %-40s %-10s %r" % (doc_id, t, txt))

    out = os.path.join(HERE, "verify_1a1b_result.json")
    json.dump({"stats": stats, "fails": fails, "multispan_docs": ms_docs,
               "canon_subs": tot_subs, "canon_subs_by_type": subs_by_type},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nsaved:", out)
    sys.exit(1 if (stats["FAIL"] or bad_counts) else 0)


if __name__ == "__main__":
    main()
