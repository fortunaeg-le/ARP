# ARCH-PROBE, проба 4b: цена распространения фамилии по документу.
#
# Механизм-кандидат: для каждого ЗАКРЫТОГО (full) PER-вхождения берём лемму
# фамилии и ищем по документу все токены с той же леммой. Меряем:
#   TP: токены внутри спанов ОТКРЫТЫХ gold-PER того же человека (то, что механизм
#       реально закроет);
#   FP: токены с той же леммой ВНЕ всех gold-PER спанов (новые ложные маски);
#   отдельно — сколько открытых PER-вхождений «фамилия+имя/инициалы» закроется
#       лишь частично (маскируется фамилия, инициалы остаются).
#
# Только txt-документы корпуса (162): для них проверяем, что gold.start/end —
# точные срезы файла; docx-сборку не воспроизводим, чтобы не спорить о сборке.
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"

from anchor_registry import _lemma_first

_WORD = re.compile(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z-]+")


def main():
    gold_all = json.load(open(C / "gold.json", encoding="utf-8"))
    results = {(d["doc_id"], d["format"]): d
               for d in json.load(open(C / "results_baseline.json", encoding="utf-8"))}

    slice_ok = slice_bad = 0
    tp_tokens = fp_tokens = 0
    fp_lower = 0
    fp_examples = []
    open_multiword = open_1word = 0
    docs_seen = 0

    for g in gold_all:
        if g["format"] != "txt":
            continue
        res = results.get((g["doc_id"], "txt"))
        if res is None or res["outcome"] != "processed":
            continue
        text = (C / "docs" / (g["doc_id"] + ".txt")).read_text(encoding="utf-8")
        pers_g = [e for e in g["entities"] if e["type"] == "PER"]
        # сверка координат
        ok = all(text[e["start"]:e["end"]] == e["text"] for e in pers_g)
        if not ok:
            slice_bad += 1
            continue
        slice_ok += 1
        docs_seen += 1

        # статус по результатам: сопоставляем gold-PER c results по text (в
        # results у каждой gold-сущности есть text и full)
        status = {}
        for e in res["entities"]:
            if e["type"] == "PER":
                status.setdefault(e["text"], []).append(e["full"])
        def is_full(t):
            v = status.get(t)
            return bool(v) and all(v)

        prot_lemmas = set()
        for e in pers_g:
            m = _WORD.search(e["text"])
            if m and is_full(e["text"]):
                prot_lemmas.add(_lemma_first(m.group(0)))
        if not prot_lemmas:
            continue

        open_spans = []
        for e in pers_g:
            if not is_full(e["text"]):
                open_spans.append((e["start"], e["end"], e["text"]))
                if _WORD.findall(e["text"]) and (len(e["text"].split()) > 1 or "." in e["text"]):
                    open_multiword += 1
                else:
                    open_1word += 1
        all_per_spans = [(e["start"], e["end"]) for e in pers_g]

        for m in _WORD.finditer(text):
            w = m.group(0)
            lem = _lemma_first(w)
            if lem not in prot_lemmas:
                continue
            inside_per = any(s <= m.start() < e for s, e in all_per_spans)
            inside_open = any(s <= m.start() < e for s, e, _ in open_spans)
            if inside_open:
                tp_tokens += 1
            elif not inside_per:
                if w[0].isupper():
                    fp_tokens += 1
                    if len(fp_examples) < 25:
                        ctx = text[max(0, m.start()-30):m.end()+30].replace("\n", " ")
                        fp_examples.append((g["doc_id"], w, ctx))
                else:
                    fp_lower += 1

    print(f"txt-доков со сверенными координатами: {slice_ok}, с расхождением: {slice_bad}")
    print(f"TP-токенов (закроют открытые PER): {tp_tokens}")
    print(f"FP-токенов (капитализированные, вне gold-PER): {fp_tokens}")
    print(f"строчных совпадений леммы вне PER (не маскируем по правилу): {fp_lower}")
    print(f"открытые вхождения: многословных {open_multiword}, однословных {open_1word}")
    print("\nПримеры FP:")
    for d, w, ctx in fp_examples:
        print(f"  {d}: «{w}» …{ctx}…")


if __name__ == "__main__":
    main()
