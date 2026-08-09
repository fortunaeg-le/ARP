# ARCH-PROBE, проба 5: (а) детерминизм run_detection — два прогона байт-в-байт;
# (б) дубли эмиссии: сколько сущностей с одинаковым (segment,start,end,type)
# приходит в tokenize от разных проходов (штатный / ремонтный / расплетение).
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = str(ROOT / "entity_types.yaml")

from extractor import extract
from pipeline import run_detection

subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))["doc_ids"][:12]

def key(e):
    return (e.segment_id, e.start, e.end, e.entity_type, e.original_text)

diff_docs = 0
dup_total = 0
dup_examples = []
for d in subset:
    p = C / "docs" / f"{d}.txt"
    if not p.exists():
        p = C / "docs" / f"{d}.docx"
    doc = extract(str(p))
    r1 = sorted(key(e) for e in run_detection(doc, CONFIG))
    r2 = sorted(key(e) for e in run_detection(doc, CONFIG))
    if r1 != r2:
        diff_docs += 1
        s1, s2 = set(r1), set(r2)
        print(f"НЕДЕТЕРМИНИЗМ {d}: только в r1 {sorted(s1-s2)[:5]}, только в r2 {sorted(s2-s1)[:5]}")
    cnt = Counter(r1)
    for k, n in cnt.items():
        if n > 1:
            dup_total += n - 1
            if len(dup_examples) < 12:
                dup_examples.append((d, n, k[3], k[4][:50]))

print(f"\nдокументов с расхождением двух прогонов: {diff_docs} из {len(subset)}")
print(f"дублей (одинаковый спан+тип эмитирован повторно): {dup_total}")
for d, n, t, txt in dup_examples:
    print(f"  {d}: ×{n} {t} «{txt}»")
