# -*- coding: utf-8 -*-
"""
E'' — per-doc sha256 анонимизированного текста по ВСЕМУ корпусу.

Назначение: проверить, изменился ли ВЫХОД на корпусе от фикса id()-кеша в
_CapsResolver. Файлы корпуса НЕ трогаются (они заморожены MANIFEST.sha256) —
меряется только результат обработки.

ВАЖНО ПРО СРАВНЕНИЕ. Режим `--simulate-old` монкипатчит _style_chain обратно на
СТАРУЮ (id-ключ) реализацию, поэтому A/B делается НА ОДНОМ И ТОМ ЖЕ коде ветки и
изолирует ровно фикс (сравнивать с агрегатом, снятым на ДРУГОЙ ветке, некорректно —
там другой код).

ЗАПУСК:
    python corpus_anon_sha.py                 # текущий код (с фиксом)
    python corpus_anon_sha.py --simulate-old  # тот же код, но старый id()-кеш
"""
import os
import sys
import json
import hashlib

SIM_OLD = "--simulate-old" in sys.argv
if SIM_OLD:
    sys.argv.remove("--simulate-old")

ROOT = r"C:\Jesus\ARP"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
CFG = os.path.join(ROOT, "entity_types.yaml")

import extractor as EX
from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize

if SIM_OLD:
    def old_style_chain(self, style, attr):
        if style is None:
            return None
        key = (id(style._element), attr)
        cached = self._chain_cache.get(key, EX._MISSING)
        if cached is not EX._MISSING:
            return cached
        result = None
        seen_ids = set()
        cur = style
        while cur is not None:
            el_id = id(cur._element)
            if el_id in seen_ids:
                break
            seen_ids.add(el_id)
            value = getattr(cur.font, attr)
            if value is not None:
                result = value
                break
            cur = cur.base_style
        self._chain_cache[key] = result
        return result
    EX._CapsResolver._style_chain = old_style_chain
    print("РЕЖИМ: СТАРАЯ реализация (id-ключ)")
else:
    print("РЕЖИМ: текущий код (фикс, ключ по объекту)")

DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
OUT = os.path.join(os.path.dirname(__file__),
                   "_corpus_sha_old.json" if SIM_OLD else "_corpus_sha_new.json")

rows = {}
names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
for i, fn in enumerate(names):
    doc = extract(os.path.join(DOCS, fn))
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    rows[fn] = hashlib.sha256(anon.encode("utf-8")).hexdigest()
    if i % 40 == 0:
        print("... %d/%d" % (i, len(names)))

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=0, sort_keys=True)

agg = hashlib.sha256(
    json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
).hexdigest()
print("docs:", len(rows))
print("AGGREGATE sha256:", agg)
print("written:", OUT)
