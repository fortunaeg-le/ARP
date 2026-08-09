# ARCH-PROBE, проба 3b: что реально отдаётся yargy.
# Обёртка вокруг ner_detector._addr_parse: длина текста, время, дубли (кеш),
# доля символов документа, прошедших через парсер. Плюс: содержат ли
# найденные yargy спаны хоть один адресный маркер/домовой паттерн —
# оценка заменимости Earley-парсера дешёвым маркерным конечным автоматом.
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = str(ROOT / "entity_types.yaml")

from extractor import extract
import ner_detector
from pipeline import run_detection

subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))["doc_ids"][:12]

calls = []          # (len, dur, n_spans)
span_texts = []     # текст каждого yargy-спана
orig = ner_detector._glue_address_matches
orig_parse = ner_detector._addr_parse
ner_detector._ADDR_PARSE_CACHE.clear()
cache_hits = 0

def patched(text):
    global cache_hits
    if text in ner_detector._ADDR_PARSE_CACHE:
        cache_hits += 1
        return orig_parse(text)
    t0 = time.perf_counter()
    res = orig_parse(text)
    calls.append((len(text), time.perf_counter() - t0, len(res)))
    for s, e in res:
        span_texts.append(text[s:e])
    return res

ner_detector._addr_parse = patched
# также в модулях, которые импортировали имя напрямую
import layout_repair
for mod in (layout_repair,):
    if hasattr(mod, "_addr_parse"):
        mod._addr_parse = patched

def load_doc(doc_id):
    p = C / "docs" / f"{doc_id}.txt"
    if not p.exists():
        p = C / "docs" / f"{doc_id}.docx"
    return extract(str(p))

docs = [load_doc(d) for d in subset]
total_doc_chars = sum(len(s.text) for d in docs for s in d.segments)

t0 = time.perf_counter()
for d in docs:
    run_detection(d, CONFIG)
wall = time.perf_counter() - t0

n = len(calls)
chars = sum(c[0] for c in calls)
dur = sum(c[1] for c in calls)
lens = sorted(c[0] for c in calls)
print(f"стенка {wall:.1f} c; вызовов yargy (без кеша): {n}, кеш-хитов: {cache_hits}")
print(f"символов через yargy: {chars} ({chars/total_doc_chars:.1%} от {total_doc_chars} симв. документов)")
print(f"время в yargy: {dur:.1f} c ({dur/wall:.1%} стенки); {dur/chars*1000:.3f} мс/симв.")
if n:
    print(f"длины окон: min {lens[0]}, p50 {lens[n//2]}, p90 {lens[int(n*0.9)]}, max {lens[-1]}")
empty = sum(1 for c in calls if c[2] == 0)
print(f"вызовов с НУЛЁМ найденных спанов: {empty} из {n} ({empty/max(n,1):.0%}); "
      f"их время: {sum(c[1] for c in calls if c[2]==0):.1f} c")

# маркерный анализ найденных спанов
MARKER = re.compile(
    r"\b(г|гор|город|обл|область|р-н|район|ул|улица|пр-кт|просп|проспект|пер|переулок|"
    r"наб|бульвар|б-р|ш|шоссе|д|дом|к|корп|корпус|стр|строение|кв|квартира|офис|оф|"
    r"пом|помещение|литера?|мкр|микрорайон|пос|посёлок|поселок|с|село|дер|деревня|"
    r"респ|республика|край|тер|линия|проезд|пл|площадь|аллея|тупик)\b\.?", re.IGNORECASE)
HOUSE = re.compile(r"\b\d{1,4}\s*[а-яА-Я]?\b")
INDEX = re.compile(r"\b\d{6}\b")
no_marker = [t for t in span_texts if not (MARKER.search(t) or INDEX.search(t))]
print(f"\nyargy-спанов: {len(span_texts)}; БЕЗ адресного маркера и индекса: {len(no_marker)} "
      f"({len(no_marker)/max(len(span_texts),1):.1%})")
print("примеры безмаркерных:", [t[:60] for t in no_marker[:15]])
