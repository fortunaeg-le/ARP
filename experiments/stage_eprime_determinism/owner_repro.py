# -*- coding: utf-8 -*-
"""
Этап E'' — РЕПРО+ДИАГНОСТИКА ДЛЯ ВЛАДЕЛЬЦА (на реальном документе, у себя).
Никакой текст документа в вывод НЕ идёт — только числа масок по типам, sha256
и признак детерминизма по слоям.

Фиксация потоков BLAS стоит ПЕРВОЙ строкой — ДО любого импорта, который может
подтянуть numpy. Это важнее, чем setdefault внутри src/: если в цепочке импортов
numpy инициализируется раньше src/ner_detector, переменные, выставленные позже,
на число потоков BLAS уже не влияют. Здесь они выставлены до всего.

ИСПОЛЬЗОВАНИЕ (одна строка достаточно):

    venv\\Scripts\\python.exe experiments\\stage_eprime_determinism\\owner_repro.py <путь_к_файлу> 6

Сравнить с многопоточным BLAS (проверить, потоки ли виноваты):

    venv\\Scripts\\python.exe experiments\\stage_eprime_determinism\\owner_repro.py <путь_к_файлу> 6 --threads 0

--threads N: 0 = не трогать (системный BLAS, многопоточный), N>0 = зафиксировать N.
По умолчанию 1 (детерминированный однопоточный BLAS).

ЧТО ПОКАЖЕТ ВЫВОД:
  Раздел «слои» — уникальных хешей по каждому слою за N прогонов (1 = детерминирован):
    L1 regex      — detect_regex + multispan (чистый Python/regex, БЕЗ Natasha)
    L2 structural — detect_structural ORG/PER (pymorphy+regex, БЕЗ Natasha-NER)
    L3 ner_addr   — detect_ner ADDRESS (natasha AddrExtractor + NewsNERTagger LOC)
    L4 compound   — merge_compound_entities (natasha NewsSyntaxParser)
  Если недетерминирован ТОЛЬКО L3/L4 — причина в инференсе Natasha (numpy/BLAS),
    лечится однопоточным BLAS (--threads 1, по умолчанию) ИЛИ, если и с ним плывёт,
    численной нестабильностью модели на этой сборке numpy.
  Если недетерминирован L1 или L2 — причина в ЧИСТОМ Python (порядок/id/uuid),
    и это меняет план: тогда нужен детерминированный порядок в разрешении спанов.
  Пришлите ЭТОТ вывод (числа, без текста) — он однозначно локализует причину.
"""
import os
import sys

# --- фиксация потоков BLAS ДО любого импорта, тянущего numpy ---
_threads = 1
if "--threads" in sys.argv:
    k = sys.argv.index("--threads")
    _threads = int(sys.argv[k + 1])
    del sys.argv[k:k + 2]
if _threads > 0:
    for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_v] = str(_threads)

import hashlib
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
CFG = os.path.join(ROOT, "entity_types.yaml")

from extractor import extract
from regex_detector import detect_regex
from multispan import collect_multispan
from anchor_registry import detect_structural, suppress_conflicts
from ner_detector import detect_ner
from syntax_compound import merge_compound_entities
from tokenizer import tokenize

if len(sys.argv) < 2:
    print("usage: owner_repro.py <путь_к_docx_или_txt> [N=6] [--threads N]")
    sys.exit(1)
DOC = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6

print("threads pinned to:", _threads if _threads > 0 else "SYSTEM (multi)")
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    print("  env %s=%s" % (_v, os.environ.get(_v)))
try:
    from threadpoolctl import threadpool_info
    for p in threadpool_info():
        print("  BLAS %s: %s threads" % (p.get("internal_api"), p.get("num_threads")))
except Exception:
    print("  (threadpoolctl не установлен — число потоков BLAS не показано; "
          "опционально: pip install threadpoolctl)")
print("N runs:", N, "\n")


def _spanhash(ents):
    rows = sorted((e.entity_type, e.segment_id, e.start, e.end) for e in ents)
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:12]


layer = {k: [] for k in ("L1", "L2", "L3", "L4", "FINAL")}
final_counts = []
for i in range(N):
    doc = extract(DOC)
    regex_e = detect_regex(doc, CFG) + collect_multispan(doc, CFG)
    struct_e = detect_structural(doc, regex_entities=regex_e)
    org_e = [e for e in struct_e if e.entity_type == "ORG"]
    per_e = [e for e in struct_e if e.entity_type == "PERSON"]
    ner_e = suppress_conflicts(
        struct_e,
        detect_ner(doc, CFG, regex_entities=regex_e, org_entities=org_e, per_entities=per_e),
    )
    entities = regex_e + ner_e + struct_e
    compound = merge_compound_entities(doc, entities)
    anon, kept = tokenize(doc, compound, CFG)

    layer["L1"].append(_spanhash(regex_e))
    layer["L2"].append(_spanhash(struct_e))
    layer["L3"].append(_spanhash(ner_e))
    layer["L4"].append(_spanhash(compound))
    fsha = hashlib.sha256(anon.encode()).hexdigest()
    layer["FINAL"].append(fsha)
    bt = Counter(e.entity_type for e in kept)
    final_counts.append((len(kept), dict(bt)))
    print("run %d: total=%d  L1=%s L2=%s L3=%s L4=%s  FINAL_sha=%s" % (
        i, len(kept), layer["L1"][-1], layer["L2"][-1], layer["L3"][-1],
        layer["L4"][-1], fsha[:12]))
    print("        by_type:", dict(sorted(bt.items())))

print("\n=== ДЕТЕРМИНИЗМ ПО СЛОЯМ (1 уникальный = OK) ===")
for k in ("L1", "L2", "L3", "L4", "FINAL"):
    u = len(set(layer[k]))
    tag = {"L1": "regex (чистый Python)", "L2": "structural ORG/PER (pymorphy, без Natasha)",
           "L3": "ner ADDRESS (Natasha)", "L4": "compound (Natasha syntax)",
           "FINAL": "итоговый anon-текст"}[k]
    print("  %-6s %-42s: %d уникальных  %s" % (k, tag, u, "OK" if u == 1 else "<<< НЕДЕТЕРМИНИЗМ"))

hashes = layer["FINAL"]
print("\ncounts:", [c[0] for c in final_counts])
if len(set(hashes)) == 1:
    print("ИТОГ: ДЕТЕРМИНИЗМ OK (все %d прогонов идентичны)" % N)
else:
    print("ИТОГ: НЕДЕТЕРМИНИЗМ. Смотри, какой слой выше отмечен '<<<' — он и есть причина.")
    # какие типы разошлись между двумя первыми различными прогонами
    base = final_counts[0][1]
    for j in range(1, N):
        if final_counts[j][1] != base:
            other = final_counts[j][1]
            allt = set(base) | set(other)
            diff = {t: (base.get(t, 0), other.get(t, 0)) for t in allt if base.get(t, 0) != other.get(t, 0)}
            print("  разошедшиеся типы (run0 vs run%d):" % j, diff)
            break
