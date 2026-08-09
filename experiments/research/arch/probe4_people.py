# ARCH-PROBE, проба 4 (главная): что даёт связность документа для защиты людей.
#
# Вопрос: если ХОТЯ БЫ ОДНО упоминание человека найдено и закрыто, можно ли
# закрыть остальные его упоминания дешёвым распространением по документу
# (поиск токенов с той же леммой фамилии)? Меряем на замороженном корпусе v1
# по дампу точки отсчёта гейта (results_baseline.json — те же цифры, что
# держат зелёный гейт сейчас).
#
# Считаем:
#   1. PER-вхождения: всего / закрыто целиком (full) / открыто.
#   2. Люди (doc_id, лемма фамилии): всего / целиком защищены / есть открытое.
#   3. Из открытых вхождений — у скольких в ТОМ ЖЕ документе есть закрытое
#      вхождение того же человека => распространение способно закрыть.
#   4. Цена: сколько ЛОЖНЫХ срабатываний даст поиск лемм найденных фамилий по
#      тексту документов (токены с той же леммой вне всех gold-PER спанов).
#
# ТОЛЬКО ЧТЕНИЕ корпуса. Ничего не пишем в tests/.
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"

from anchor_registry import _lemma_first  # тот же pymorphy, что в проде

_WORD = re.compile(r"[А-ЯЁа-яё][А-ЯЁа-яё-]+")


def surname_lemma(text):
    """Лемма первого слова PER-текста (фамилия в корпусе стоит первой)."""
    m = _WORD.search(text)
    if not m:
        return None
    return _lemma_first(m.group(0))


def doc_text(doc_id, fmt):
    if fmt == "txt":
        return (C / "docs" / f"{doc_id}.txt").read_text(encoding="utf-8")
    # .docx — через штатный extractor (читает и колонтитулы)
    from extractor import extract
    doc = extract(str(C / "docs" / f"{doc_id}.docx"))
    return "\n".join(s.text for s in doc.segments)


def main():
    results = json.load(open(C / "results_baseline.json", encoding="utf-8"))
    gold = {d["doc_id"] + "|" + d["format"]: d for d in json.load(open(C / "gold.json", encoding="utf-8"))}

    tot = full = 0
    open_m = 0
    open_reachable = 0          # открытое вхождение, у человека есть закрытое в том же доке
    people = defaultdict(lambda: {"full": 0, "open": 0})
    surname_first_token_mismatch = 0
    lemma_errors = 0

    for doc in results:
        if doc["outcome"] != "processed":
            continue
        per = [e for e in doc["entities"] if e["type"] == "PER"]
        groups = defaultdict(list)
        for e in per:
            lem = surname_lemma(e["text"])
            if lem is None:
                lemma_errors += 1
                continue
            groups[lem].append(e)
        for lem, es in groups.items():
            key = (doc["doc_id"], doc["format"], lem)
            has_full = any(e["full"] for e in es)
            for e in es:
                tot += 1
                if e["full"]:
                    full += 1
                    people[key]["full"] += 1
                else:
                    open_m += 1
                    people[key]["open"] += 1
                    if has_full:
                        open_reachable += 1

    n_people = len(people)
    p_full = sum(1 for v in people.values() if v["open"] == 0)
    p_exposed = n_people - p_full
    p_exposed_reachable = sum(1 for v in people.values() if v["open"] > 0 and v["full"] > 0)

    print(f"PER-вхождений: {tot}; закрыто целиком: {full} ({full/tot:.1%}); открыто: {open_m}")
    print(f"Открытых вхождений, достижимых распространением (есть закрытое того же человека в доке): "
          f"{open_reachable} из {open_m} ({open_reachable/max(open_m,1):.1%})")
    print(f"Людей (док×фамилия-лемма): {n_people}; целиком защищено: {p_full} ({p_full/n_people:.1%}); "
          f"с открытым: {p_exposed}")
    print(f"Из них станут целиком защищены распространением: {p_exposed_reachable} "
          f"({p_exposed_reachable/max(p_exposed,1):.1%} открытых людей)")
    new_p_full = p_full + p_exposed_reachable
    print(f"Потенциал: защита людей {p_full/n_people:.1%} -> {new_p_full/n_people:.1%}")
    print(f"lemma_errors: {lemma_errors}")


if __name__ == "__main__":
    main()
