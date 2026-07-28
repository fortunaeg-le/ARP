# -*- coding: utf-8 -*-
"""ЭТАП T1 — приёмка 1 и 2 на ПОЛНОМ корпусе, один прогон детекции.

Что считает:
  * вариант «Максимум» (enabled_types=None) — sha256 обезличенного текста и
    полная карта масок каждого документа. Приёмка 1: сверяется с тем же дампом,
    снятым ДО этапа (`--out` на прошлом коммите) — расхождений быть не должно,
    этап не вводит ни одного нового типа;
  * 14 вариантов «все типы кроме одного». Приёмка 2: маски выключенного типа
    исчезли, а карта масок ВСЕХ остальных типов совпадает с максимумом
    посимвольно (segment_id, start, end, тип, токен).

Почему один прогон. Дорогая часть маскирования — `tokenizer.resolve_for_masking`
(детекция границ B3 + разрешение пересечений); она НЕ зависит от набора типов
(в этом и состоит контракт этапа), поэтому считается один раз на документ, а
15 вариантов — это 15 дешёвых `apply_masking` поверх готового результата. Гонять
детекцию 15 раз было бы не только дорого, но и неверно методически: совпадение
границ доказывалось бы на 15 разных прогонах недетерминированной по своей
природе Natasha (находка Eprime-A).

Запуск:
    venv/Scripts/python.exe experiments/stage_t1/sweep.py --out experiments/stage_t1/after.json
    venv/Scripts/python.exe experiments/stage_t1/sweep.py --baseline --out .../before.json
`--baseline` — режим для ПРОШЛОГО коммита: зовёт только `tokenize(...)` без
аргумента набора (функций T1 там ещё нет) и пишет тот же формат для варианта
«Максимум».
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract          # noqa: E402
from pipeline import run_detection     # noqa: E402
import tokenizer as TK                 # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

#: 14 типов корпуса в терминах entity_types.yaml (`ACCOUNT`/`PER` замера — это
#: словарь measure_lib, другой слой).
CORPUS_TYPES = (
    "PERSON", "ORG", "ADDRESS", "INN", "OGRN", "KPP", "BANK_ACCOUNT", "BIK",
    "PASSPORT", "PHONE", "EMAIL", "SNILS", "BIRTHDATE", "SUM",
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mask_map(kept):
    return [[e.segment_id, e.start, e.end, e.entity_type, e.token] for e in kept]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    names = sorted(os.listdir(DOCS))
    if args.limit:
        names = names[:args.limit]

    known = None
    if not args.baseline:
        import type_policy
        known = set(type_policy.known_types(CONFIG))

    out = {"baseline": args.baseline, "documents": {}}
    t0 = time.time()
    for i, name in enumerate(names, 1):
        path = os.path.join(DOCS, name)
        doc = extract(path)
        entities = run_detection(doc, CONFIG)

        rec = {}
        if args.baseline:
            anon, kept = TK.tokenize(doc, entities, CONFIG)
            rec["maximum"] = {"sha": _sha(anon), "masks": _mask_map(kept)}
        else:
            resolved = TK.resolve_for_masking(doc, entities, CONFIG)
            anon, kept = TK.apply_masking(doc, resolved, CONFIG, None)
            rec["maximum"] = {"sha": _sha(anon), "masks": _mask_map(kept)}
            for victim in CORPUS_TYPES:
                a_v, kept_v = TK.apply_masking(
                    doc, resolved, CONFIG, frozenset(known - {victim}))
                rec[victim] = {"sha": _sha(a_v), "masks": _mask_map(kept_v)}
            # состояние объектов вернуть к «Максимуму»: apply_masking проставляет
            # token на общих Entity, а дамп максимума снят ДО вариантов
            TK.apply_masking(doc, resolved, CONFIG, None)

        out["documents"][name] = rec
        if i % 25 == 0:
            print(f"  {i}/{len(names)}  {time.time() - t0:.0f} c", flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"готово: {len(names)} док., {time.time() - t0:.0f} c -> {args.out}")


if __name__ == "__main__":
    main()
