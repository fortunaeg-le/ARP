# -*- coding: utf-8 -*-
"""ЭТАП DEFAULT-GATE — СКОЛЬКО ЕЩЁ ТАКОГО КЛАССА (решение владельца, п. 3).

Гейт меряет ТОЛЬКО на полном наборе типов (`type_policy.MAXIMUM`), а
пользователь работает на наборе ПО УМОЛЧАНИЮ («Только персональные данные»).
Значит целый класс дыр невидим по построению: значение, которое на максимуме
закрывает маска ДРУГОГО типа, при настройках по умолчанию уходит открытым.
Ровно так найден `MFIX-PROFILE-IP-PER` — ФИО внутри «ИП Фамилия Имя Отчество»
закрывает маска ORG, а ORG в наборе по умолчанию выключен.

Скрипт НИЧЕГО не чинит. Он гоняет ОДНУ детекцию на документ и раскладывает
маски ДВАЖДЫ — набором MAXIMUM и набором по умолчанию, — после чего считает
эталонные сущности, у которых:

  * тип ВХОДИТ в набор по умолчанию (то есть пользователь ждёт маску),
  * на максимуме спан закрыт,
  * при настройках по умолчанию — открыт хотя бы частично.

Это и есть класс. Сущности типов ВНЕ набора по умолчанию (ORG, SUM, реквизиты
юрлица) в счёт не идут: их открытость — работа по правилам, а не дыра.

    venv/Scripts/python.exe experiments/stage_default_gate/probe_class.py [--limit N]
"""
import os
import sys
import json
import time
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
OUT = os.path.join(HERE, "class_probe.json")

# Типы РАЗМЕТКИ (gold), соответствующие набору по умолчанию: карта конфиг->gold
# живёт в measure_lib.TYPE_MAP, здесь берём её обратную сторону, чтобы не
# заводить второго списка имён (разъехались бы молча).
DEFAULT_GOLD_TYPES = {ML.TYPE_MAP.get(t, t) for t in type_policy.PERSONAL}


def _uncovered(gs, ge, spans):
    """Сколько символов [gs,ge) не закрыто ни одним интервалом spans."""
    cover = [False] * (ge - gs)
    for s, e in spans:
        for i in range(max(gs, s), min(ge, e)):
            cover[i - gs] = True
    return cover.count(False)


def worker(d):
    path = os.path.join(DOCS, d["doc_id"] + "." + d["format"])
    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)
    doc = extract(path)
    ents = run_detection(doc, CONFIG)
    offs, _ = ML.build_segment_offsets(doc, G, body_start,
                                       regions=ML.part_regions(path, G))
    out = []
    spans = {}
    for label, types in (("max", type_policy.MAXIMUM),
                         ("default", type_policy.PERSONAL)):
        _anon, kept = tokenize(doc, ents, CONFIG, enabled_types=types)
        det = ML.map_entities_to_pt1(kept, offs, G)
        spans[label] = [(x["start"], x["end"], x["gtype"])
                        for x in det if x["start"] is not None]
    for e in d["entities"]:
        gs, ge, gt = e["start"], e["end"], e["type"]
        if gt not in DEFAULT_GOLD_TYPES:
            continue                      # тип вне набора — открытость законна
        u_max = _uncovered(gs, ge, [(s, en) for s, en, _ in spans["max"]])
        u_def = _uncovered(gs, ge, [(s, en) for s, en, _ in spans["default"]])
        if u_def <= u_max:
            continue                      # набор по умолчанию не хуже — не класс
        # чем спан был закрыт на максимуме: тип-«крыша»
        roof = sorted({t for s, en, t in spans["max"]
                       if max(gs, s) < min(ge, en)})
        out.append({
            "doc_id": d["doc_id"], "type": gt, "len": ge - gs,
            "uncovered_max": u_max, "uncovered_default": u_def,
            "roof": roof, "text_len": len(e["text"]),
        })
    return out


def main():
    gold = RM.load_gold()
    if "--limit" in sys.argv:
        gold = gold[:int(sys.argv[sys.argv.index("--limit") + 1])]
    t0 = time.time()
    import multiprocessing
    recs = []
    with multiprocessing.Pool(RM._default_workers()) as pool:
        for i, part in enumerate(pool.imap(worker, gold)):
            recs.extend(part)
            if (i + 1) % 100 == 0:
                print(f"[{i+1}/{len(gold)}] {time.time()-t0:.0f}s", flush=True)
    json.dump(recs, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

    print(f"\nКЛАСС «видно только на дефолтном наборе»: {len(recs)} эталонных вхождений")
    by_type = collections.Counter(r["type"] for r in recs)
    by_roof = collections.Counter(",".join(r["roof"]) for r in recs)
    print("  по типу эталона:", dict(by_type))
    print("  чем спан закрыт на МАКСИМУМЕ (тип-крыша):")
    for k, v in by_roof.most_common():
        print(f"    {k or '(ничем)'}: {v}")
    full = [r for r in recs if r["uncovered_default"] == r["len"]]
    print(f"  из них открыты ЦЕЛИКОМ: {len(full)}; частично: {len(recs) - len(full)}")
    print(f"  документов затронуто: {len({r['doc_id'] for r in recs})}")
    print(f"-> {OUT}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
