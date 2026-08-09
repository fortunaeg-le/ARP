# ARCH-PROBE, проба 4c: почему открытые-но-достижимые PER-вхождения не взяты
# существующим проходом-2 реестра. Разбивка по miss_reason/флагам + примеры.
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"

from anchor_registry import _lemma_first

_WORD = re.compile(r"[А-ЯЁа-яё][А-ЯЁа-яё-]+")


def main():
    results = json.load(open(C / "results_baseline.json", encoding="utf-8"))
    reach_reason = Counter()
    unreach_reason = Counter()
    reach_flags = Counter()
    examples = defaultdict(list)

    for doc in results:
        if doc["outcome"] != "processed":
            continue
        per = [e for e in doc["entities"] if e["type"] == "PER"]
        groups = defaultdict(list)
        for e in per:
            m = _WORD.search(e["text"])
            if m:
                groups[_lemma_first(m.group(0))].append(e)
        for lem, es in groups.items():
            has_full = any(e["full"] for e in es)
            for e in es:
                if e["full"]:
                    continue
                reason = e.get("miss_reason") or (
                    "found_not_full(bnd under=%s)" % e["bnd"]["under"] if e["found"] else "not_found")
                flags = (e["found"], e["exact"], e["full"])
                if has_full:
                    reach_reason[reason] += 1
                    reach_flags[flags] += 1
                    if len(examples[reason]) < 6:
                        examples[reason].append((doc["doc_id"], e["text"], e.get("category")))
                else:
                    unreach_reason[reason] += 1

    print("ДОСТИЖИМЫЕ распространением (501): причины незакрытости")
    for r, n in reach_reason.most_common():
        print(f"  {n:5d}  {r}")
    print("\nфлаги (found, exact, full):")
    for f, n in reach_flags.most_common():
        print(f"  {n:5d}  {f}")
    print("\nНЕДОСТИЖИМЫЕ (у человека нет ни одного закрытого): причины")
    for r, n in unreach_reason.most_common():
        print(f"  {n:5d}  {r}")
    print("\nПримеры по причинам (достижимые):")
    for r, ex in examples.items():
        print(f"  {r}:")
        for d, t, c in ex:
            print(f"    {d} [{c}]: «{t}»")


if __name__ == "__main__":
    main()
