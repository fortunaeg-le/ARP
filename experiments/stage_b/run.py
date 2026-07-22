# -*- coding: utf-8 -*-
"""Полный прогон замера в experiments/ (НЕ в tests/corpus, граница ТЗ этапа B).
Пишет results в переданный путь. Импортирует run_measurement.run_all — единственный
источник истины по конвейеру (то же, что gate.py)."""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.abspath(os.path.join(HERE, "..", "..", "tests", "corpus"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "src")))
sys.path.insert(0, CORPUS)
import run_measurement as RM  # noqa

def main():
    out = sys.argv[1]
    gold = RM.load_gold()
    t0 = time.time()
    results = RM.run_all(gold, verbose=True)
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print("DONE %d docs in %.0fs -> %s" % (len(results), time.time()-t0, out), flush=True)

if __name__ == "__main__":
    main()
