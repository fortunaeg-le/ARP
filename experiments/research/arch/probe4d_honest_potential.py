# ARCH-PROBE, проба 4d: честный потенциал распространения.
# Интерпретация Б: вхождение, замаскированное ЧУЖИМ типом (wrong_type:ORG —
# ФИО внутри ИП-склейки), в тексте НЕ открыто (набор «максимум»). Считаем
# защиту людей и потенциал распространения при обеих интерпретациях, плюс
# структуру подлинно голых (nothing) вхождений.
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


def run(protected_fn, label):
    results = json.load(open(C / "results_baseline.json", encoding="utf-8"))
    people = defaultdict(lambda: {"prot": 0, "open": 0})
    open_m = reach_m = 0
    for doc in results:
        if doc["outcome"] != "processed":
            continue
        groups = defaultdict(list)
        for e in doc["entities"]:
            if e["type"] != "PER":
                continue
            m = _WORD.search(e["text"])
            if m:
                groups[_lemma_first(m.group(0))].append(e)
        for lem, es in groups.items():
            key = (doc["doc_id"], doc["format"], lem)
            has_prot = any(protected_fn(e) for e in es)
            for e in es:
                if protected_fn(e):
                    people[key]["prot"] += 1
                else:
                    people[key]["open"] += 1
                    open_m += 1
                    if has_prot:
                        reach_m += 1
    n = len(people)
    p_full = sum(1 for v in people.values() if v["open"] == 0)
    p_reach = sum(1 for v in people.values() if v["open"] > 0 and v["prot"] > 0)
    print(f"[{label}] людей: {n}; целиком защищено: {p_full} ({p_full/n:.1%}); "
          f"распространение добавляет: {p_reach} -> {(p_full+p_reach)/n:.1%}; "
          f"открытых вхождений {open_m}, достижимо {reach_m}")


def naked_structure():
    results = json.load(open(C / "results_baseline.json", encoding="utf-8"))
    cat = Counter()
    for doc in results:
        if doc["outcome"] != "processed":
            continue
        for e in doc["entities"]:
            if e["type"] != "PER" or e["found"] or (e.get("miss_reason") or "").startswith("wrong_type"):
                continue
            t = e["text"]
            if "." in t:
                kind = "фамилия+инициалы"
            elif t == t.lower():
                kind = "строчные"
            else:
                kind = "полное ФИО/иное"
            cat[kind] += 1
    print("Структура полностью пропущенных (nothing) PER-вхождений:", dict(cat))


run(lambda e: e["full"], "А: защищено = full (PER-маска целиком)")
run(lambda e: e["full"] or (e.get("miss_reason") or "").startswith("wrong_type"),
    "Б: защищено = full ИЛИ маска чужого типа")
naked_structure()
