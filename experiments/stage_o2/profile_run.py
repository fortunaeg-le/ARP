# -*- coding: utf-8 -*-
"""
O2 — ПРОФИЛИРОВАНИЕ пайплайна. Сначала измерить, потом чинить.

Три нагрузки (п.1 задания):
    --load large    tests/fixtures/synthetic_corporate_large.docx  (~5000 сегментов)
    --load styles   tests/fixtures/synthetic_many_styles.docx      (284 стиля)
    --load txt      tests/fixtures/synthetic_corporate_large.txt   (cross-segment путь)
    --load corpus   весь корпус (162 .docx)
    --load corpus:N первые N документов корпуса

Что печатает:
  * стоимость загрузки моделей (одноразовая) против обработки;
  * фазы по стенным часам (extract / detect по шагам / tokenize) — БЕЗ профайлера,
    поэтому числа не искажены;
  * cProfile: топ-30 по кумулятивному и по собственному времени;
  * распределение по СЛОЯМ (сумма tottime по модулю/пакету — аддитивно, сумма
    всех слоёв = полное время);
  * число вызовов горячих функций и среднее на вызов.

ЗАПУСК:
    venv/Scripts/python.exe experiments/stage_o2/profile_run.py --load large
"""
import argparse
import cProfile
import io
import os
import pstats
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

CFG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
FIX = os.path.join(ROOT, "tests", "fixtures")

# --------------------------------------------------------------------------- #
#  Слои: имя -> предикат по (файл, имя функции). Первый подошедший выигрывает,
#  поэтому порядок значим (частные правила выше общих).
# --------------------------------------------------------------------------- #
LAYERS = [
    ("1 docx/чтение",      lambda f, n: ("docx" in f and "site-packages" in f)
                                        or f.endswith("lxml") or "\\lxml" in f
                                        or "oxml" in f),
    ("2 extract+Caps",     lambda f, n: f.endswith("extractor.py")),
    ("3 нормализация/виды", lambda f, n: f.endswith("normalizer.py")),
    ("4 regex L1a",        lambda f, n: f.endswith("regex_detector.py")),
    ("5 multispan L1b",    lambda f, n: f.endswith("multispan.py")),
    ("6 structural L2",    lambda f, n: f.endswith("anchor_registry.py")),
    ("7 ner_detector L3",  lambda f, n: f.endswith("ner_detector.py")),
    ("8 syntax L4",        lambda f, n: f.endswith("syntax_compound.py")),
    ("9 tokenizer",        lambda f, n: f.endswith("tokenizer.py")),
    ("A natasha/slovnet",  lambda f, n: "natasha" in f or "slovnet" in f or "navec" in f),
    ("B yargy",            lambda f, n: "yargy" in f),
    ("C pymorphy",         lambda f, n: "pymorphy" in f or "dawg" in f),
    ("D numpy",            lambda f, n: "numpy" in f),
    ("E re (движок)",      lambda f, n: f.endswith("re\\_compiler.py") or f.endswith("re\\__init__.py")
                                        or f.endswith("sre_compile.py") or "\\re\\" in f),
    ("F yaml/конфиг",      lambda f, n: "yaml" in f),
    ("G прочее python",    lambda f, n: True),
]


def layer_of(filename, funcname):
    for name, pred in LAYERS:
        try:
            if pred(filename, funcname):
                return name
        except Exception:
            continue
    return "G прочее python"


def load_paths(spec):
    if spec == "large":
        return [os.path.join(FIX, "synthetic_corporate_large.docx")]
    if spec == "styles":
        return [os.path.join(FIX, "synthetic_many_styles.docx")]
    if spec == "txt":
        return [os.path.join(FIX, "synthetic_corporate_large.txt")]
    names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
    if spec.startswith("corpus:"):
        names = names[:int(spec.split(":", 1)[1])]
    return [os.path.join(DOCS, f) for f in names]


# --------------------------------------------------------------------------- #
#  Пофазный замер по стенным часам (без профайлера)
# --------------------------------------------------------------------------- #
PHASES = ["extract", "regex", "multispan", "structural", "ner", "suppress",
          "compound", "boundary(B3)", "resolve+assemble"]


def run_phases(paths):
    from extractor import extract
    from regex_detector import detect_regex
    from multispan import collect_multispan
    from anchor_registry import detect_structural, suppress_conflicts
    from ner_detector import detect_ner
    from syntax_compound import merge_compound_entities
    import tokenizer as TK

    acc = {p: 0.0 for p in PHASES}
    n_seg = n_ent = 0
    t_all = time.perf_counter()
    for path in paths:
        t = time.perf_counter()
        doc = extract(path)
        acc["extract"] += time.perf_counter() - t

        t = time.perf_counter()
        regex_e = detect_regex(doc, CFG)
        acc["regex"] += time.perf_counter() - t

        t = time.perf_counter()
        regex_e = regex_e + collect_multispan(doc, CFG)
        acc["multispan"] += time.perf_counter() - t

        t = time.perf_counter()
        struct_e = detect_structural(doc, regex_entities=regex_e)
        acc["structural"] += time.perf_counter() - t
        org_e = [e for e in struct_e if e.entity_type == "ORG"]
        per_e = [e for e in struct_e if e.entity_type == "PERSON"]

        t = time.perf_counter()
        ner_e = detect_ner(doc, CFG, regex_entities=regex_e,
                           org_entities=org_e, per_entities=per_e)
        acc["ner"] += time.perf_counter() - t

        t = time.perf_counter()
        ner_e = suppress_conflicts(struct_e, ner_e)
        acc["suppress"] += time.perf_counter() - t

        entities = regex_e + ner_e + struct_e
        t = time.perf_counter()
        entities = merge_compound_entities(doc, entities)
        acc["compound"] += time.perf_counter() - t

        # tokenize разложен на B3 и остальное
        t = time.perf_counter()
        bnd = TK._detect_boundary_entities(doc, CFG, entities)
        acc["boundary(B3)"] += time.perf_counter() - t

        t = time.perf_counter()
        _orig = TK._detect_boundary_entities
        TK._detect_boundary_entities = lambda *a, **k: bnd
        try:
            anon, kept = TK.tokenize(doc, entities, CFG)
        finally:
            TK._detect_boundary_entities = _orig
        acc["resolve+assemble"] += time.perf_counter() - t

        n_seg += len(doc.segments)
        n_ent += len(kept)
    total = time.perf_counter() - t_all
    return acc, total, n_seg, n_ent


def run_plain(paths):
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize
    for path in paths:
        doc = extract(path)
        ents = run_detection(doc, CFG)
        tokenize(doc, ents, CFG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", default="large")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--no-profile", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    paths = load_paths(args.load)

    # --- 1. Стоимость загрузки моделей (одноразовая) ---
    t = time.perf_counter()
    import extractor  # noqa: F401
    t_extractor = time.perf_counter() - t
    t = time.perf_counter()
    import ner_detector  # noqa: F401  (тянет natasha + прогрев)
    t_ner_import = time.perf_counter() - t
    t = time.perf_counter()
    import anchor_registry  # noqa: F401  (тянет pymorphy)
    t_ar_import = time.perf_counter() - t
    t = time.perf_counter()
    import syntax_compound  # noqa: F401  (тянет NewsSyntaxParser)
    t_sc_import = time.perf_counter() - t
    print("=== ЗАГРУЗКА МОДЕЛЕЙ (одноразовая, при импорте) ===")
    print("  extractor         %6.2f s" % t_extractor)
    print("  ner_detector      %6.2f s  (natasha NER + прогрев)" % t_ner_import)
    print("  anchor_registry   %6.2f s  (pymorphy)" % t_ar_import)
    print("  syntax_compound   %6.2f s  (NewsSyntaxParser)" % t_sc_import)
    print("  ИТОГО импорт      %6.2f s" % (t_extractor + t_ner_import + t_ar_import + t_sc_import))

    # --- 2. Фазы по стенным часам (без профайлера) ---
    print("\n=== ФАЗЫ ПО СТЕННЫМ ЧАСАМ (без профайлера), нагрузка %s, %d файл(ов) ==="
          % (args.load, len(paths)))
    acc, total, n_seg, n_ent = run_phases(paths)
    print("  сегментов %d, сущностей (kept) %d, ПОЛНОЕ ВРЕМЯ %.2f s" % (n_seg, n_ent, total))
    ssum = sum(acc.values())
    for p in PHASES:
        print("  %-18s %8.2f s  %5.1f%%" % (p, acc[p], acc[p] / total * 100))
    print("  %-18s %8.2f s  %5.1f%%" % ("(сумма фаз)", ssum, ssum / total * 100))

    if args.no_profile:
        return

    # --- 3. cProfile ---
    print("\n=== cProfile (нагрузка %s) ===" % args.load)
    pr = cProfile.Profile()
    pr.enable()
    run_plain(paths)
    pr.disable()
    out = os.path.join(HERE, "_prof_%s%s.pstats" % (args.load.replace(":", "_"),
                                                    ("_" + args.tag) if args.tag else ""))
    pr.dump_stats(out)

    st = pstats.Stats(pr)
    total_tt = sum(v[2] for v in st.stats.values())
    print("  профилированное время (сумма tottime): %.2f s" % total_tt)

    buf = io.StringIO()
    st.stream = buf
    st.sort_stats("cumulative").print_stats(args.top)
    print("\n--- ТОП-%d ПО КУМУЛЯТИВНОМУ ---" % args.top)
    print(buf.getvalue().split("Ordered by")[-1][:6000])

    buf = io.StringIO()
    st.stream = buf
    st.sort_stats("tottime").print_stats(args.top)
    print("\n--- ТОП-%d ПО СОБСТВЕННОМУ ---" % args.top)
    print(buf.getvalue().split("Ordered by")[-1][:6000])

    # --- 4. Слои (аддитивно по tottime) ---
    by_layer = {}
    for (fn, ln, name), (cc, nc, tt, ct, callers) in st.stats.items():
        L = layer_of(fn, name)
        a = by_layer.setdefault(L, [0.0, 0])
        a[0] += tt
        a[1] += nc
    print("\n--- РАСПРЕДЕЛЕНИЕ ПО СЛОЯМ (собственное время) ---")
    for L, (tt, nc) in sorted(by_layer.items(), key=lambda kv: -kv[1][0]):
        print("  %-22s %8.2f s  %5.1f%%   вызовов %d" % (L, tt, tt / total_tt * 100, nc))

    # --- 5. Горячие функции src/: вызовы и среднее на вызов ---
    print("\n--- ФУНКЦИИ src/: вызовы и среднее на вызов (топ-25 по tottime) ---")
    rows = []
    for (fn, ln, name), (cc, nc, tt, ct, callers) in st.stats.items():
        if os.path.sep + "src" + os.path.sep in fn or fn.endswith("pipeline.py"):
            rows.append((tt, ct, nc, os.path.basename(fn), name, ln))
    rows.sort(reverse=True)
    print("  %10s %10s %10s  %-22s %s" % ("tottime", "cumtime", "вызовов", "модуль", "функция"))
    for tt, ct, nc, base, name, ln in rows[:25]:
        per = (tt / nc * 1e6) if nc else 0
        print("  %10.3f %10.3f %10d  %-22s %s:%d  (%.1f мкс/вызов)"
              % (tt, ct, nc, base, name, ln, per))
    print("\nстатистика записана: %s" % out)


if __name__ == "__main__":
    main()
