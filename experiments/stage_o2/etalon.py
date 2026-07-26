# -*- coding: utf-8 -*-
"""
O2 — БАЙТОВЫЙ ЭТАЛОН выхода пайплайна.

Контракт сессии O2: поведение не меняется НИ НА БАЙТ. Этот скрипт снимает
эталон ДО оптимизаций и сверяет с ним ПОСЛЕ каждого отдельного шага.
Расхождение хоть в один байт = откат шага (в том числе «стало лучше»:
улучшение поведения в O2 = изменение логики = провал).

Что хешируется (три независимых среза, чтобы порча не спряталась):
  1. anon      — sha256 анонимизированного текста (то же, что снимает
                 experiments/stage_eprime_determinism/corpus_anon_sha.py;
                 агрегат сопоставим с задокументированным в STATE);
  2. ents      — sha256 ПОЛНОГО дампа сущностей В ПОРЯДКЕ их выдачи
                 (segment_id/start/end/spans/тип/токен/детектор/group_key/
                 canonical/original_text). Порядок НЕ сортируется намеренно:
                 он попадает в сессию, значит он — часть поведения;
  3. plain     — sha256 build_plain_text(doc): страж слоя extract/tokenizer
                 (если сломается чтение docx, anon может случайно совпасть).
  Поле Entity.id — uuid4, НЕ детерминировано по построению; из дампа исключено.

ЗАПУСК:
    venv/Scripts/python.exe experiments/stage_o2/etalon.py --label before
    venv/Scripts/python.exe experiments/stage_o2/etalon.py --label after
    venv/Scripts/python.exe experiments/stage_o2/etalon.py --compare before after
Опции:
    --corpus-only / --fixtures-only  — снять часть (для быстрых итераций)
    --repeat N                       — N прогонов подряд (детерминизм)
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

CFG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

FIXTURE_FILES = [
    "synthetic_corporate_large.docx",
    "synthetic_corporate_large.txt",
    "synthetic_many_styles.docx",
]


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _ent_dump(entities):
    """Каноническая текстовая проекция списка сущностей — БЕЗ сортировки."""
    out = []
    for e in entities:
        spans = "" if e.spans is None else ";".join(f"{a}:{b}" for a, b in e.spans)
        out.append("\t".join([
            e.segment_id, str(e.start), str(e.end), spans,
            e.entity_type, str(e.token), e.detector,
            str(e.group_key), str(e.canonical),
            repr(e.original_text),
        ]))
    return "\n".join(out)


def process(path):
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize, build_plain_text

    t0 = time.perf_counter()
    doc = extract(path)
    t1 = time.perf_counter()
    ents = run_detection(doc, CFG)
    t2 = time.perf_counter()
    anon, kept = tokenize(doc, ents, CFG)
    t3 = time.perf_counter()
    plain = build_plain_text(doc)
    return {
        "anon": _sha(anon),
        "ents": _sha(_ent_dump(kept)),
        "plain": _sha(plain),
        "n_seg": len(doc.segments),
        "n_ent": len(kept),
        "t_extract": round(t1 - t0, 4),
        "t_detect": round(t2 - t1, 4),
        "t_tokenize": round(t3 - t2, 4),
    }


HASH_KEYS = ("anon", "ents", "plain")


def snapshot(do_corpus=True, do_fixtures=True, verbose=True, corpus_limit=None):
    rows = {}
    if do_corpus:
        names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
        # Подвыборка для ПРОМЕЖУТОЧНЫХ сверок: полный корпус — 5 минут, а между
        # шагами нужен только ответ «не сломалось ли». Сверка идёт подокументно,
        # поэтому снимок по части корпуса корректно сравнивается с полным
        # (--compare печатает пересечение и явно сообщает о разнице наборов).
        # ФИНАЛЬНАЯ сверка — всегда без лимита.
        if corpus_limit:
            names = names[:corpus_limit]
        t0 = time.time()
        for i, fn in enumerate(names):
            rows["corpus/" + fn] = process(os.path.join(DOCS, fn))
            if verbose and (i + 1) % 40 == 0:
                print("... corpus %d/%d  %.0fs" % (i + 1, len(names), time.time() - t0),
                      flush=True)
        if verbose:
            print("corpus done: %d docs, %.1fs" % (len(names), time.time() - t0), flush=True)
    if do_fixtures:
        for fn in FIXTURE_FILES:
            p = os.path.join(FIXTURES, fn)
            if not os.path.exists(p):
                continue
            t0 = time.time()
            rows["fixture/" + fn] = process(p)
            if verbose:
                print("fixture %-38s %.1fs" % (fn, time.time() - t0), flush=True)
    return rows


def aggregates(rows):
    """Агрегаты по каждому срезу + один общий."""
    agg = {}
    for k in HASH_KEYS:
        payload = {name: r[k] for name, r in rows.items()}
        agg[k] = _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    corpus_anon = {name.split("/", 1)[1]: r["anon"]
                   for name, r in rows.items() if name.startswith("corpus/")}
    if corpus_anon:
        # Побитово тот же агрегат, что печатает corpus_anon_sha.py (E''),
        # чтобы можно было сверить с числом из STATE, а не только с собой.
        agg["corpus_anon_sha_compat"] = _sha(
            json.dumps(corpus_anon, sort_keys=True, ensure_ascii=False))
    agg["ALL"] = _sha(json.dumps([agg[k] for k in HASH_KEYS], ensure_ascii=False))
    return agg


def path_for(label):
    return os.path.join(HERE, "_etalon_%s.json" % label)


def cmd_snapshot(args):
    reps = max(1, args.repeat)
    prev_agg = None
    for r in range(reps):
        t0 = time.time()
        rows = snapshot(not args.fixtures_only, not args.corpus_only,
                        corpus_limit=args.corpus_limit)
        agg = aggregates(rows)
        wall = time.time() - t0
        total_detect = sum(v["t_detect"] for v in rows.values())
        total_extract = sum(v["t_extract"] for v in rows.values())
        total_token = sum(v["t_tokenize"] for v in rows.values())
        print("\n=== прогон %d/%d  wall %.1fs ===" % (r + 1, reps, wall))
        print("  extract %.1fs | detect %.1fs | tokenize %.1fs" %
              (total_extract, total_detect, total_token))
        for k, v in sorted(agg.items()):
            print("  %-24s %s" % (k, v))
        if prev_agg is not None:
            same = (prev_agg == agg["ALL"])
            print("  повтор идентичен предыдущему: %s" % ("ДА" if same else "НЕТ !!!"))
        prev_agg = agg["ALL"]
        if r == 0 and args.label:
            with open(path_for(args.label), "w", encoding="utf-8") as f:
                json.dump({"rows": rows, "agg": agg, "wall": round(wall, 1)},
                          f, ensure_ascii=False, indent=0, sort_keys=True)
            print("  записано: %s" % path_for(args.label))


def cmd_compare(a, b):
    A = json.load(open(path_for(a), encoding="utf-8"))
    B = json.load(open(path_for(b), encoding="utf-8"))
    ra, rb = A["rows"], B["rows"]
    print("=== СВЕРКА %s -> %s ===" % (a, b))
    only_a = sorted(set(ra) - set(rb))
    only_b = sorted(set(rb) - set(ra))
    common = sorted(set(ra) & set(rb))
    partial = bool(only_a or only_b)
    if partial:
        print("ПОДВЫБОРКА: наборы документов разные — сравнивается пересечение "
              "(%d док.); только в %s: %d, только в %s: %d"
              % (len(common), a, len(only_a), b, len(only_b)))
    bad = []
    for name in common:
        diffs = [k for k in HASH_KEYS if ra[name][k] != rb[name][k]]
        if diffs:
            bad.append((name, diffs))
    if bad:
        print("!!! РАСХОЖДЕНИЙ: %d из %d документов" % (len(bad), len(common)))
        for name, diffs in bad[:40]:
            print("    %-60s %s" % (name, ",".join(diffs)))
        if len(bad) > 40:
            print("    ... ещё %d" % (len(bad) - 40))
    else:
        print("ВЫХОД ИДЕНТИЧЕН побайтно на %d документах (anon+ents+plain)" % len(common))
    if partial:
        # Агрегат считается ПО ВСЕМУ набору снимка, поэтому при разных наборах он
        # обязан различаться — сравнивать его здесь бессмысленно и опасно (выглядит
        # как провал там, где провала нет). Агрегаты сверяются только на полных снимках.
        print("  агрегаты НЕ сверяются: снимки сняты по разным наборам документов")
    else:
        for k in sorted(A["agg"]):
            mark = "OK " if A["agg"][k] == B["agg"].get(k) else "!!!"
            print("  %s %-24s %s -> %s" % (mark, k, A["agg"][k][:16], B["agg"].get(k, "-")[:16]))
    ta = sum(v["t_extract"] + v["t_detect"] + v["t_tokenize"] for v in ra.values())
    tb = sum(v["t_extract"] + v["t_detect"] + v["t_tokenize"] for v in rb.values())
    print("  время (сумма по документам): %.1fs -> %.1fs  (%+.1f%%)"
          % (ta, tb, (tb - ta) / ta * 100 if ta else 0))
    for layer in ("t_extract", "t_detect", "t_tokenize"):
        la = sum(v[layer] for v in ra.values())
        lb = sum(v[layer] for v in rb.values())
        print("    %-12s %8.1fs -> %8.1fs  (%+.1f%%)"
              % (layer, la, lb, (lb - la) / la * 100 if la else 0))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--corpus-only", action="store_true")
    ap.add_argument("--fixtures-only", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--corpus-limit", type=int,
                    help="взять только первые N документов корпуса (промежуточные сверки)")
    args = ap.parse_args()
    if args.compare:
        sys.exit(cmd_compare(*args.compare))
    cmd_snapshot(args)


if __name__ == "__main__":
    main()
