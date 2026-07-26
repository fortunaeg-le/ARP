# -*- coding: utf-8 -*-
"""
O2 — сколько РАЗНЫХ текстов приходит на вход дорогим движкам.

Мемоизация «текст -> результат» окупается ровно настолько, насколько входы
повторяются. Скрипт считает: всего вызовов / уникальных текстов / доля
секунд, которую сняла бы идеальная мемоизация.
"""
import argparse
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
CFG = os.path.join(ROOT, "entity_types.yaml")
FIX = os.path.join(ROOT, "tests", "fixtures")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

CALLS = defaultdict(list)     # engine -> [(text, seconds)]


def install():
    import ner_detector as ND
    import tokenizer as TK
    from natasha import Doc

    _addr = ND._addr_extractor

    def addr_wrap(text):
        t = time.perf_counter()
        r = list(_addr(text))
        CALLS["yargy"].append((text, time.perf_counter() - t))
        return r
    ND._addr_extractor = addr_wrap
    TK._addr_extractor = addr_wrap

    _doc_tag = Doc.tag_ner

    def tag_wrap(self, tagger, *a, **k):
        t = time.perf_counter()
        r = _doc_tag(self, tagger, *a, **k)
        CALLS["ner_tag"].append((self.text, time.perf_counter() - t))
        return r
    Doc.tag_ner = tag_wrap

    _doc_seg = Doc.segment

    def seg_wrap(self, segmenter, *a, **k):
        t = time.perf_counter()
        r = _doc_seg(self, segmenter, *a, **k)
        CALLS["segment"].append((self.text, time.perf_counter() - t))
        return r
    Doc.segment = seg_wrap

    import normalizer as NM
    import anchor_registry as AR
    _nfd = NM.normalize_for_detection

    def nfd_wrap(base):
        t = time.perf_counter()
        r = _nfd(base)
        CALLS["normalize"].append((base, time.perf_counter() - t))
        return r
    NM.normalize_for_detection = nfd_wrap
    AR.normalize_for_detection = nfd_wrap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", default="large")
    args = ap.parse_args()

    import extractor
    import ner_detector  # noqa
    import anchor_registry  # noqa
    install()
    from pipeline import run_detection
    from tokenizer import tokenize

    if args.load == "large":
        paths = [os.path.join(FIX, "synthetic_corporate_large.docx")]
    elif args.load == "styles":
        paths = [os.path.join(FIX, "synthetic_many_styles.docx")]
    elif args.load == "txt":
        paths = [os.path.join(FIX, "synthetic_corporate_large.txt")]
    else:
        names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
        if ":" in args.load:
            names = names[:int(args.load.split(":")[1])]
        paths = [os.path.join(DOCS, f) for f in names]

    t0 = time.perf_counter()
    for p in paths:
        doc = extractor.extract(p)
        ents = run_detection(doc, CFG)
        tokenize(doc, ents, CFG)
        # мемоизация «на документ» — считаем в пределах одного документа отдельно
    total = time.perf_counter() - t0

    print("нагрузка %s (%d файлов): полное %.2f s\n" % (args.load, len(paths), total))
    print("%-10s %9s %9s %9s %10s %10s %9s"
          % ("движок", "вызовов", "уник.", "дубли%", "секунд", "спасено", "% полного"))
    for eng, rows in CALLS.items():
        seen = {}
        saved = 0.0
        tot = 0.0
        for text, dt in rows:
            tot += dt
            if text in seen:
                saved += dt
            else:
                seen[text] = dt
        print("%-10s %9d %9d %8.1f%% %10.2f %10.2f %8.1f%%"
              % (eng, len(rows), len(seen),
                 (1 - len(seen) / len(rows)) * 100 if rows else 0,
                 tot, saved, saved / total * 100))

    # распределение стоимости yargy по длине текста
    rows = CALLS.get("yargy") or []
    if rows:
        rows_sorted = sorted(rows, key=lambda r: -r[1])
        print("\nyargy: 10 самых дорогих вызовов")
        for text, dt in rows_sorted[:10]:
            print("   %7.1f ms  len=%4d  %r" % (dt * 1000, len(text), text[:70]))
        n0 = sum(1 for t, d in rows if len(t) == 0)
        print("   пустых текстов: %d" % n0)


if __name__ == "__main__":
    main()
