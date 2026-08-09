# ARCH-PROBE, проба 3: куда уходит время детекции сегодня.
# cProfile run_detection на 12 документах среза subset_iter (txt+docx),
# агрегирование по модулям. Плюс отдельно: время detect_regex по типам
# (для экстраполяции на 50 типов, часть 5).
import cProfile
import io
import json
import pstats
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = str(ROOT / "entity_types.yaml")

from extractor import extract
from pipeline import run_detection

subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))
docs_meta = subset["doc_ids"][:12]
print("документов в пробе:", len(docs_meta), docs_meta[:3])


def load_doc(item):
    # doc_ids: "name|txt" или "name|docx"? посмотрим по факту
    if "|" in item:
        doc_id, fmt = item.split("|")
    else:
        doc_id, fmt = item, "txt"
    ext = ".txt" if fmt == "txt" else ".docx"
    p = C / "docs" / f"{doc_id}{ext}"
    if not p.exists():
        p = C / "docs" / f"{doc_id}.docx"
    return extract(str(p))


docs = [load_doc(it) for it in docs_meta]

# прогрев (ленивая инициализация Natasha)
run_detection(docs[0], CONFIG)

pr = cProfile.Profile()
t0 = time.perf_counter()
pr.enable()
for d in docs:
    run_detection(d, CONFIG)
pr.disable()
wall = time.perf_counter() - t0
print(f"\nСтенка: {wall:.1f} c на {len(docs)} документов")

st = pstats.Stats(pr)
st.sort_stats("cumulative")
# агрегируем tottime по файлу
by_file = defaultdict(float)
total_tt = 0.0
for (fn, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
    total_tt += tt
    f = fn.replace("\\", "/")
    if "site-packages" in f:
        pkg = f.split("site-packages/")[1].split("/")[0]
        by_file[f"pkg:{pkg}"] += tt
    elif "/src/" in f:
        by_file["src:" + f.split("/src/")[1]] += tt
    else:
        by_file["other:" + f.rsplit("/", 1)[-1]] += tt
print(f"\ntottime по группам (всего {total_tt:.1f} c):")
for k, v in sorted(by_file.items(), key=lambda kv: -kv[1])[:20]:
    print(f"  {v:8.2f} c  {v/total_tt:5.1%}  {k}")

# ключевые функции
buf = io.StringIO()
st.stream = buf
st.print_stats(r"yargy|addr|natasha|pymorphy|load_yaml|detect_regex|repair", 25)
print(buf.getvalue()[:4000])
