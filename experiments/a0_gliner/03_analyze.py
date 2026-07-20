"""A0 bake-off, шаг 3 — ЗЕРКАЛА и таблицы (проектный venv, только stdlib).

Читает честный вход, дамп Natasha и сырьё GLiNER (все score). Применяет сетку
порогов, пишет пер-точечные дампы и считает ОДНИМИ И ТЕМИ ЖЕ матчерами для обоих
движков три зеркала ТЗ:
  (a) мусор из известного списка Natasha — сколько КАТЕГОРИЙ помечено;
  (b) истинные сущности — Восход (recall по вхождениям + сколько ТИПОВ), ФИО, адреса;
  (c) новый мусор GLiNER — распечатка distinct спанов не-ПДн для ручной сверки.

Запуск: venv/Scripts/python.exe experiments/a0_gliner/03_analyze.py
"""
import json
import os
import re
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
GRID = [0.1, 0.3, 0.5, 0.7, 0.9]


def load(p):
    with open(os.path.join(HERE, p), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# ГРАУНД-ТРУС (из ручного чтения dogovor.docx, зафиксирован ДО прогона GLiNER)
# ---------------------------------------------------------------------------
VOSKHOD_RE = re.compile(r"Восход[а-яё]*")

# 3 реальных человека — матчеры по всем формам (полные + «Иванов Р.Э.»)
PEOPLE = {
    "Иванов Роман Эдуардович": re.compile(r"Иванов"),
    "Петрова Ольга Николаевна": re.compile(r"Петров[аеу]"),
    "Ким Ен Су": re.compile(r"Ким Ен Су|Ким Е\.\s*С\.|\bКим\b"),
}

# Мусорные КАТЕГОРИИ известного списка Natasha (матч по тексту спана, ci)
GARBAGE = {
    "Большинство": re.compile(r"больш", re.I),
    "Прибыль": re.compile(r"прибыл", re.I),
    "Доля": re.compile(r"\bдол(я|ю|е|и|ей|ям|ями|ях|ах)\b", re.I),
    "ПОДПИСИ СТОРОН": re.compile(r"подпис", re.I),
    "Российская Федерация": re.compile(r"россий|федерац", re.I),
    # заголовки разделов — обрабатываются отдельно, по сегментам (см. ниже)
}


def build_ground_truth(segments):
    """Строит: вхождения Восхода (seg,start,end); адресные сегменты; заголовочные
    сегменты. Всё по норм-тексту (координаты спанов GLiNER/Natasha — в нём же)."""
    voskhod_occ = []          # [(seg_id, s, e)]
    addr_segs = {}            # seg_id -> метка адреса
    heading_segs = set()
    for seg in segments:
        sid, t = seg["seg_id"], seg["norm_text"]
        for m in VOSKHOD_RE.finditer(t):
            voskhod_occ.append((sid, m.start(), m.end()))
        if re.search(r"Адрес регистрации", t):
            addr_segs[sid] = t.strip()[:70]
        # заголовок: короткий нумерованный раздел или титул, без ФИО/реквизитов
        st = t.strip()
        if (re.match(r"^\d+\.\s+[А-ЯЁ]", st) and len(st) < 70) or \
           st in ("(КОРПОРАТИВНЫЙ ДОГОВОР)",) or \
           re.match(r"^Договор ОБ Осуществлении", st):
            heading_segs.add(sid)
    return voskhod_occ, addr_segs, heading_segs


def overlaps(a_s, a_e, b_s, b_e):
    return max(a_s, b_s) < min(a_e, b_e)


# ---------------------------------------------------------------------------
# ЗЕРКАЛА для одного набора спанов (движко-независимо)
# ---------------------------------------------------------------------------
def mirror(spans, gt):
    """spans: [{seg_id,text,type,start,end}]. Возвращает dict метрик зеркал."""
    voskhod_occ, addr_segs, heading_segs = gt
    by_seg = {}
    for sp in spans:
        by_seg.setdefault(sp["seg_id"], []).append(sp)

    # (a) мусор: сколько категорий помечено (любой тип)
    garbage_hits = {}
    for name, rx in GARBAGE.items():
        cnt = sum(1 for sp in spans if rx.search(sp["text"]))
        garbage_hits[name] = cnt
    # заголовки: спан пересекает заголовочный сегмент
    head_cnt = sum(1 for sp in spans if sp["seg_id"] in heading_segs)
    garbage_hits["заголовки разделов"] = head_cnt
    n_garbage_categories = sum(1 for v in garbage_hits.values() if v > 0)

    # (b) Восход: recall по вхождениям + множество типов
    caught_occ = 0
    voskhod_types = {}
    for (sid, s, e) in voskhod_occ:
        covering = [sp for sp in by_seg.get(sid, []) if overlaps(s, e, sp["start"], sp["end"])]
        if covering:
            caught_occ += 1
            for sp in covering:
                voskhod_types[sp["type"]] = voskhod_types.get(sp["type"], 0) + 1
    voskhod_recall = caught_occ / len(voskhod_occ) if voskhod_occ else 0.0

    # (b) ФИО: пойман ли каждый как PERSON
    people_caught = {}
    for name, rx in PEOPLE.items():
        ok = False
        wrong = set()
        for sp in spans:
            if rx.search(sp["text"]):
                if sp["type"] == "PERSON":
                    ok = True
                else:
                    wrong.add(sp["type"])
        people_caught[name] = {"as_person": ok, "wrong_types": sorted(wrong)}

    # (b) адреса: пойман ли каждый адресный сегмент как ADDRESS
    addr_caught = {}
    for sid, label in addr_segs.items():
        ok = any(sp["type"] == "ADDRESS" for sp in by_seg.get(sid, []))
        addr_caught[sid] = {"label": label, "as_address": ok}

    return {
        "n_spans": len(spans),
        "by_type": _counts([sp["type"] for sp in spans]),
        "garbage_categories_marked": n_garbage_categories,
        "garbage_detail": garbage_hits,
        "voskhod_occ_total": len(voskhod_occ),
        "voskhod_occ_caught": caught_occ,
        "voskhod_recall": round(voskhod_recall, 3),
        "voskhod_types": dict(sorted(voskhod_types.items())),
        "voskhod_single_type": len(voskhod_types) == 1,
        "people_caught": people_caught,
        "addr_caught": addr_caught,
    }


def _counts(items):
    c = {}
    for x in items:
        c[x] = c.get(x, 0) + 1
    return dict(sorted(c.items()))


# (c) новый мусор GLiNER: distinct спаны, не пересекающие ни один истинный тип
def new_garbage(spans, gt):
    voskhod_occ, addr_segs, heading_segs = gt
    voskhod_by_seg = {}
    for (sid, s, e) in voskhod_occ:
        voskhod_by_seg.setdefault(sid, []).append((s, e))
    people_rx = list(PEOPLE.values())
    freq = {}
    for sp in spans:
        txt = sp["text"].strip()
        # реальная сущность? — Восход / ФИО / адресный сегмент → не «новый мусор»
        if any(overlaps(sp["start"], sp["end"], s, e)
               for s, e in voskhod_by_seg.get(sp["seg_id"], [])):
            continue
        if any(rx.search(txt) for rx in people_rx):
            continue
        if sp["seg_id"] in addr_segs and sp["type"] == "ADDRESS":
            continue
        key = (txt.lower(), sp["type"])
        freq[key] = freq.get(key, 0) + 1
    # топ по частоте
    top = sorted(freq.items(), key=lambda kv: -kv[1])
    return [(t, ty, n) for (t, ty), n in top]


def main():
    segments = load("input/normalized_segments.json")
    gt = build_ground_truth(segments)
    voskhod_occ, addr_segs, heading_segs = gt
    print(f"ГРАУНД-ТРУС: Восход вхождений={len(voskhod_occ)}, "
          f"адресных сегментов={len(addr_segs)} {list(addr_segs)}, "
          f"заголовков={len(heading_segs)}")

    # --- Natasha (одна точка, порога нет) ---
    nat = load("dumps/natasha_dogovor.json")
    nat_spans = [{"seg_id": e["seg_id"], "text": e["text"], "type": e["type"],
                  "start": e["start"], "end": e["end"]} for e in nat["entities"]]
    # ВНИМАНИЕ: Natasha-спаны в ИСХОДНЫХ координатах, Восход-вхождения — в норм.
    # Для dogovor норм==исходный на этих сегментах (регистр Восхода не менялся,
    # омоглифов нет), но зафиксируем это как допущение в отчёте.
    nat_mirror = mirror(nat_spans, gt)
    results = {"natasha": {"latency_full_doc_s": nat["latency_ner_s"], "mirror": nat_mirror,
                           "new_garbage_top": new_garbage(nat_spans, gt)[:15]}}

    # --- GLiNER: обе модели, оба набора label, сетка порогов ---
    raw_files = [f for f in os.listdir(os.path.join(HERE, "dumps"))
                 if f.startswith("raw_") and f.endswith(".json")]
    for rf in sorted(raw_files):
        raw = load(f"dumps/{rf}")
        key = f"{raw['model']}|{raw['label_set']}"
        results[key] = {"latency_full_doc_s": raw["latency_full_doc_s"],
                        "load_s": raw["load_s"], "grid": {}}
        for th in GRID:
            spans = [sp for sp in raw["predictions"] if sp["score"] >= th]
            # пер-точечный дамп
            dump_name = (f"gliner_{rf[4:-5]}_th{int(th*100):02d}.json")
            with open(os.path.join(HERE, "dumps", dump_name), "w", encoding="utf-8") as f:
                json.dump({"model": raw["model"], "label_set": raw["label_set"],
                           "threshold": th, "n_spans": len(spans), "spans": spans},
                          f, ensure_ascii=False, indent=1)
            m = mirror(spans, gt)
            m["new_garbage_top"] = new_garbage(spans, gt)[:12]
            results[key]["grid"][str(th)] = m

    with open(os.path.join(HERE, "tables", "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)

    _print_summary(results)


def _print_summary(results):
    nat = results["natasha"]["mirror"]
    print("\n" + "=" * 78)
    print("NATASHA (baseline, порога нет):")
    print(f"  спанов={nat['n_spans']} {nat['by_type']}")
    print(f"  (a) мусор-категорий помечено: {nat['garbage_categories_marked']}/6  "
          f"{nat['garbage_detail']}")
    print(f"  (b) Восход recall={nat['voskhod_recall']} "
          f"({nat['voskhod_occ_caught']}/{nat['voskhod_occ_total']}), "
          f"типов={len(nat['voskhod_types'])} {nat['voskhod_types']} "
          f"single_type={nat['voskhod_single_type']}")
    print(f"      ФИО: " + ", ".join(
        f"{n.split()[0]}={'PER' if v['as_person'] else v['wrong_types'] or 'MISS'}"
        for n, v in nat['people_caught'].items()))
    print(f"      адреса: " + ", ".join(
        f"{sid}={'OK' if v['as_address'] else 'MISS'}"
        for sid, v in nat['addr_caught'].items()))

    for key, r in results.items():
        if key == "natasha" or "grid" not in r:
            continue
        print("\n" + "=" * 78)
        print(f"{key}  (load {r['load_s']}c, полный док {r['latency_full_doc_s']}c)")
        hdr = ("  th | spans | (a)мус | Восх recall | типов | 1тип | ФИО(PER) | адр(OK)")
        print(hdr)
        for th in GRID:
            m = r["grid"][str(th)]
            n_per = sum(1 for v in m["people_caught"].values() if v["as_person"])
            n_addr = sum(1 for v in m["addr_caught"].values() if v["as_address"])
            print(f"  {th:.1f}| {m['n_spans']:5d} | {m['garbage_categories_marked']:6d} | "
                  f"{m['voskhod_recall']:.2f} ({m['voskhod_occ_caught']:>3d}/{m['voskhod_occ_total']}) | "
                  f"{len(m['voskhod_types']):5d} | {str(m['voskhod_single_type'])[:1]:4s} | "
                  f"{n_per}/3      | {n_addr}/{len(m['addr_caught'])}")


if __name__ == "__main__":
    main()
