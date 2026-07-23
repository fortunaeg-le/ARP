# -*- coding: utf-8 -*-
"""
Этап E'' — репро-скрипт ДЛЯ ВЛАДЕЛЬЦА (запускается на реальном документе на
своей машине; никакого текста документа в вывод не идёт — только числа).

Проверяет находку Eprime-A: детекция на одном документе в ОДНОМ процессе даёт
разные результаты между прогонами (239/226/226 масок, "первый ≠ остальные,
остальные равны"). Гипотеза (не подтверждена на синтетике, см.
docs/archive/reports/HANDOFF_STAGE_EPRIME_DETERMINISM.md): непрогретый пул
потоков BLAS/numpy на первом вызове NER даёт другой порядок float-редукций,
что на порогах уверенности модели может перекинуть решение по паре сущностей.

ИСПОЛЬЗОВАНИЕ (2 строки):

    venv\\Scripts\\python.exe experiments\\stage_eprime_determinism\\owner_repro.py <путь_к_файлу> 6
    venv\\Scripts\\python.exe experiments\\stage_eprime_determinism\\owner_repro.py <путь_к_файлу> 6 --pin-threads

Первая строка — БЕЗ фиксации потоков (воспроизводит старое поведение, если
дефект жив несмотря на фикс в src/ner_detector.py — фикс живёт в src/, поэтому
--pin-threads тут ничего не меняет: он лишь дублирует то же в чистом виде для
диагностики, если владелец гоняет НЕ HEAD-код). Вторая строка форсирует
однопоточный BLAS — если недетерминизм исчезает именно с этим флагом, гипотеза
подтверждена численно. Печатает ТОЛЬКО число масок по типам и sha256 anon-текста
на каждом прогоне -- содержимое документа никогда не попадает в вывод.
"""
import os
import sys

if "--pin-threads" in sys.argv:
    for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_var] = "1"
    sys.argv.remove("--pin-threads")
    PINNED = True
else:
    PINNED = False

ROOT = r"C:\Jesus\ARP"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
CFG = os.path.join(ROOT, "entity_types.yaml")

import hashlib
from collections import Counter

from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize

if len(sys.argv) < 2:
    print("usage: owner_repro.py <путь_к_docx_или_txt> [N=6] [--pin-threads]")
    sys.exit(1)

DOC_PATH = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6

print("pin-threads:", PINNED)
print("N runs:", N)
print("---")

results = []
for i in range(N):
    doc = extract(DOC_PATH)
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    by_type = Counter(e.entity_type for e in kept)
    h = hashlib.sha256(anon.encode("utf-8")).hexdigest()
    results.append((len(kept), h, dict(by_type)))
    print("run %2d: total=%d sha256=%s" % (i, len(kept), h))
    for t in sorted(by_type):
        print("        %-12s %d" % (t, by_type[t]))

counts = [r[0] for r in results]
hashes = [r[1] for r in results]
print("\n=== ИТОГ ===")
print("counts:", counts)
print("unique sha256:", len(set(hashes)))
if len(set(hashes)) == 1:
    print("ДЕТЕРМИНИЗМ: OK (все %d прогонов идентичны)" % N)
else:
    print("НЕДЕТЕРМИНИЗМ ПОДТВЕРЖДЁН")
    if N > 2:
        rest_same = len(set(hashes[1:])) == 1
        first_differs = hashes[0] != hashes[1]
        print("первый ≠ остальные, остальные совпадают:", first_differs and rest_same)
        # какие типы разошлись между прогоном 0 и прогоном 1
        c0, c1 = results[0][2], results[1][2]
        all_types = set(c0) | set(c1)
        diffs = {t: (c0.get(t, 0), c1.get(t, 0)) for t in all_types if c0.get(t, 0) != c1.get(t, 0)}
        print("разошедшиеся типы (run0 vs run1):", diffs)
