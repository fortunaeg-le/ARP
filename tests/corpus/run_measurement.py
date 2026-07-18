# -*- coding: utf-8 -*-
"""
run_measurement.py — харнесс замера покрытия SHIFRATOR по замороженному корпусу.

Прогон одной командой:
    venv/Scripts/python.exe tests/corpus/run_measurement.py

Что делает (см. ЗАДАНИЕ этап 2, §3-4):
  * гоняет ПОЛНЫЙ конвейер encrypt (extract -> detect_regex -> detect_ner ->
    merge_compound_entities -> tokenize) в ИЗОЛИРОВАННОМ хранилище сессий;
  * detokenize(anon) для проверки round-trip;
  * отображает найденные сущности в координаты PT-1 (gold) и сопоставляет с gold;
  * считает recall / precision / span coverage / residual leak / round-trip /
    классификацию ложных срабатываний — РАЗДЕЛЬНО по типам, категориям, приёмам;
  * пишет машиночитаемый results_<commit>.json и печатает сводку.

КОД СИСТЕМЫ НЕ ИМПОРТИРУЕТ НИЧЕГО НА ЗАПИСЬ; ~/.shifrator не трогается —
хранилище подменяется на временную директорию (storage_dir=... во всех вызовах).
"""
import os
import re
import sys
import json
import time
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402
from extractor import extract  # noqa: E402
from unread_zones import scan_unread_zones  # noqa: E402
from regex_detector import detect_regex  # noqa: E402
from ner_detector import detect_ner  # noqa: E402
from syntax_compound import merge_compound_entities  # noqa: E402
from tokenizer import tokenize, build_plain_text  # noqa: E402
from session_store import save_session  # noqa: E402
from detokenizer import detokenize  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
GOLD = os.path.join(HERE, "gold.json")
DOCS = os.path.join(HERE, "docs")
COMMIT = "stage2"  # этап 2 (нормализация); НЕ перезатираем results_baseline.json (гейт)
OUT = os.path.join(HERE, f"results_{COMMIT}.json")

NUMERIC_TYPES = {"INN", "OGRN", "KPP", "ACCOUNT", "BIK", "PHONE",
                 "PASSPORT", "SNILS", "BIRTHDATE"}

# Числовые типы для метрики leak_v2 (по ЗАДАНИЮ этапа 0b): реквизиты, у которых
# «ядро» — непрерывная цифровая последовательность.  BIRTHDATE/DATE сюда НЕ
# входят (это даты, окно-метрика к ним не применяется).
V2_NUMERIC_TYPES = {"INN", "OGRN", "KPP", "ACCOUNT", "BIK", "PHONE",
                    "PASSPORT", "SNILS"}

# храним сессии во временной директории — ~/.shifrator НЕ трогаем
STORAGE = tempfile.mkdtemp(prefix="shifrator_measure_")

_GLUE_RE = re.compile(r"(?<=\d)[\s\-]+(?=\d)")


def _overlap(a0, a1, b0, b1):
    return max(a0, b0) < min(a1, b1)


def leak_pieces(gtype, gtext, anon_norm, anon_glue):
    """Какие части эталонного значения ДОЖИЛИ до анонимного текста (нормализованно).
    Пусто -> не утекло."""
    pieces = []
    if gtype in NUMERIC_TYPES:
        for c in ML.digit_cores(gtext, min_len=6):
            if c in anon_glue:
                pieces.append(c)
    if gtype in ("PER", "ORG"):
        for t in ML.alpha_tokens(gtext, 4):
            if t in anon_norm:
                pieces.append(t)
    if gtype == "ADDRESS":
        for c in ML.address_components(gtext):
            hay = anon_glue if c.isdigit() else anon_norm
            if c in hay:
                pieces.append(c)
    if gtype == "EMAIL":
        n = ML.norm_nospace(gtext)
        if n and n in anon_norm.replace(" ", ""):
            pieces.append(n)
    # универсальный точный запас (ловит буквенные типы целиком)
    gx = ML.norm_text(gtext)
    if gx and gx in anon_norm and gx not in pieces:
        pieces.append(gx)
    return sorted(set(pieces))


def leak_v2(gtype, gtext, anon_norm_v2, anon_digit_field, anon_date_field):
    """Метрика ЧАСТИЧНОЙ утечки (этап 0b).  Диспетчер по типу сущности.
    Возвращает dict {status: none|partial|full, fragments: [...], ...}.
    Считается ПАРАЛЛЕЛЬНО со старой (v1); v1 не удаляется — нужна для тренда.

    Отличие от v1: v1 требует, чтобы ЦЕЛОЕ ядро дожило подстрокой (заниженная
    оценка); v2 ловит выживший ФРАГМЕНТ ядра >= порога, короткие ФИО-токены и
    покомпонентную утечку адреса — то, чего v1 не видит.

    ИНВАРИАНТ (этап 0b-fix): у сущности, физически присутствующей в документе, НЕ
    существует статуса «неприменимо».  Каждая сущность получает none/partial/full.
    Тип, который метрика не умеет разобрать, — это НЕ повод её пропустить: пропуск
    молча вычитает сущность из знаменателя утечки и делает метрику слепой к целому
    типу (так этап 0c потерял 228 BIRTHDATE).  Неразобранный тип считаем full
    КОНСЕРВАТИВНО (сущность есть, защиту доказать не смогли) и помечаем
    unclassified=True, чтобы такие случаи были видны, а не растворялись в цифре."""
    if gtype == "BIRTHDATE":
        return ML.leak_v2_birthdate(gtext, anon_date_field)
    if gtype in V2_NUMERIC_TYPES:
        return ML.leak_v2_numeric(gtext, anon_digit_field, strict=8, soft=6)
    if gtype in ("PER", "ORG"):
        return ML.leak_v2_per(gtext, anon_norm_v2)
    if gtype == "ADDRESS":
        return ML.leak_v2_address(gtext, anon_norm_v2, anon_digit_field)
    if gtype == "EMAIL":
        return ML.leak_v2_email(gtext, anon_norm_v2)
    # КОНСЕРВАТИВНЫЙ fallback — не пропуск (см. инвариант выше)
    return {"status": "full", "fragments": [], "unclassified": True}


# Замер ВСЕГДА гоняет систему в lossy-режиме (этап 1b, §4 ЗАДАНИЯ).  Это не
# косметика, а условие существования замера: после включения strict почти все docx
# корпуса (135 из 162 — все с колонтитулом/сноской/надписью/вложенной таблицей)
# начали бы отказываться, и цифры recall/leak обнулились бы на пустом месте.
# Сопоставимость с baseline при этом сохраняется: зоны не читались и ДО этапа 1
# (recall по ним ровно 0%), поэтому lossy-прогон меряет ровно то же тело
# документа, что и baseline — политика отказа ничего не меняет в детекции.
ALLOW_LOSSY = True


def process_doc(d, allow_lossy=ALLOW_LOSSY):
    doc_id = d["doc_id"]
    path = os.path.join(DOCS, doc_id + "." + d["format"])

    # --- политика непрочитанных зон (этап 1b) ---
    # Сканируем ВСЕГДА, в обоих режимах: в lossy зоны не мешают обработке, но
    # знать, сколько текста молча выброшено, замер обязан — это и есть та цифра,
    # ради которой поле refused_zones заводится.
    zones = scan_unread_zones(path) if d["format"] == "docx" else []
    refused_zones = [
        {"kind": z.kind, "part": z.part, "char_count": z.char_count}
        for z in zones
    ]
    if zones and not allow_lossy:
        # Третий исход документа наравне с processed/crashed. В штатном прогоне
        # (ALLOW_LOSSY=True) сюда не попадаем; ветка существует, чтобы «отказано»
        # было настоящим исходом харнесса, а не декоративным полем.
        return {
            "outcome": "refused", "doc_id": doc_id, "format": d["format"],
            "source": d["source"], "n_entities": len(d["entities"]),
            "refused_zones": refused_zones,
        }

    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)
    _, body_text = (None, G) if d["format"] == "txt" else ML._docx_header_and_body_text(path)
    body_end = body_start + len(body_text)

    doc = extract(path)
    regex_e = detect_regex(doc, CONFIG)
    ner_e = detect_ner(doc, CONFIG, regex_entities=regex_e)
    entities = regex_e + ner_e
    entities = merge_compound_entities(doc, entities)
    anon_text, kept = tokenize(doc, entities, CONFIG)

    # round-trip
    sid = save_session(kept, session_id=None, ttl_hours=24, storage_dir=STORAGE)
    restored, unresolved = detokenize(anon_text, sid, storage_dir=STORAGE)
    plain = build_plain_text(doc)
    roundtrip_ok = (restored == plain)
    rt = {"ok": roundtrip_ok}
    if not roundtrip_ok:
        # классификация расхождения: только пробел/перевод строки (низкая тяжесть,
        # артефакт схлопывания '\n'->' ' в ячейках) ИЛИ порча значащих символов.
        n = min(len(restored), len(plain))
        diffs = [(i, restored[i], plain[i]) for i in range(n)
                 if restored[i] != plain[i]]
        len_mismatch = len(restored) != len(plain)
        ws = {" ", "\n", "\t", "\r"}
        ws_only = (not len_mismatch) and all(a in ws and b in ws for _, a, b in diffs)
        sample = None
        if diffs:
            i0 = diffs[0][0]
            sample = {"restored": restored[max(0, i0 - 30):i0 + 30],
                      "plain": plain[max(0, i0 - 30):i0 + 30]}
        rt.update({"n_diff": len(diffs) + abs(len(restored) - len(plain)),
                   "len_mismatch": len_mismatch, "ws_only": ws_only,
                   "sample": sample})

    # ---- masking_correctness: координаты масок в plain/restored ----
    # Отдельная система координат от PT-1: A сравнивает restored с plain, а plain —
    # это сборка tokenizer.build_plain_text, а не PT-1-текст корпуса.  Смещения
    # сегментов внутри plain считаем повторной сборкой и СВЕРЯЕМ её с эталонной:
    # разъезд реализаций сделал бы координаты A враньём, поэтому падаем громко.
    plain2, plain_offs = ML.plain_segment_offsets(doc)
    assert plain2 == plain, (
        f"{doc_id}: ML.plain_segment_offsets разошлась с tokenizer.build_plain_text — "
        "координаты метрики A недействительны"
    )
    aligned = (len(restored) == len(plain))

    # глобализация найденных
    offs, loc_misses = ML.build_segment_offsets(doc, G, body_start)
    det = ML.map_entities_to_pt1(kept, offs, G)
    det_located = [x for x in det if x["start"] is not None]

    anon_norm = ML.norm_text(anon_text)
    anon_glue = _GLUE_RE.sub("", anon_norm)
    # независимая v2-нормализация анонимного текста (см. measure_lib, блок leak_v2)
    anon_norm_v2 = ML.v2_norm_text(anon_text)
    anon_digit_field = ML.v2_digit_runs(anon_text)
    # поле для ДАТ: точка/слэш/перенос между цифрами склеены (см. v2_date_field)
    anon_date_field = ML.v2_date_field(anon_text)

    # ---- сопоставление эталонных сущностей ----
    ent_records = []
    used_det = set()  # индексы det, попавшие в TP хоть одной gold
    for gi, e in enumerate(d["entities"]):
        gs, ge, gt = e["start"], e["end"], e["type"]
        same = [x for x in det_located if x["gtype"] == gt and _overlap(gs, ge, x["start"], x["end"])]
        anyt = [x for x in det_located if _overlap(gs, ge, x["start"], x["end"])]
        found = len(same) > 0
        # span coverage
        exact = full = False
        left_trim = right_trim = False
        if found:
            umin = min(x["start"] for x in same)
            umax = max(x["end"] for x in same)
            exact = (umin == gs and umax == ge)
            full = (umin <= gs and umax >= ge)
            if not full:
                left_trim = umin > gs
                right_trim = umax < ge
        for x in same:
            used_det.add(id(x))
        # причина промаха
        if found:
            reason = None
        elif anyt:
            reason = "wrong_type:" + ",".join(sorted(set(x["gtype"] for x in anyt)))
        else:
            reason = "nothing"
        # зона (для docx): в теле или нет
        in_body = (gs >= body_start and ge <= body_end)
        # residual leak — ДВЕ метрики параллельно (v1 бинарная + v2 частичная)
        pieces = leak_pieces(gt, e["text"], anon_norm, anon_glue)
        v2 = leak_v2(gt, e["text"], anon_norm_v2, anon_digit_field, anon_date_field)
        ent_records.append({
            "type": gt, "category": e.get("category", ""), "trick": e.get("trick"),
            "checksum": e.get("checksum"), "text": e["text"],
            "found": found, "exact": exact, "full": full,
            "left_trim": left_trim, "right_trim": right_trim,
            "miss_reason": reason, "in_body": in_body,
            # v1 (как было): бинарная утечка целого ядра
            "leaked": len(pieces) > 0, "leak_pieces": pieces,
            "leak_v1": len(pieces) > 0,
            # v2 (этап 0b): частичная утечка {none|partial|full} + фрагменты
            "leak_v2": v2,
        })

    # ---- ложные срабатывания (по найденным, не легшим ни в одну gold нужного типа) ----
    negs = d.get("negatives", [])
    igns = d.get("ignore", [])
    fp_records = []
    for x in det_located:
        # попал ли в gold-сущность своего типа?
        tp = any(x["gtype"] == e["type"] and _overlap(x["start"], x["end"], e["start"], e["end"])
                 for e in d["entities"])
        if tp:
            continue
        on_ignore = any(_overlap(x["start"], x["end"], g["start"], g["end"]) for g in igns)
        if on_ignore:
            continue
        on_neg = next((g for g in negs if _overlap(x["start"], x["end"], g["start"], g["end"])), None)
        # пересечение с gold ДРУГОГО типа?
        cross = next((e for e in d["entities"]
                      if _overlap(x["start"], x["end"], e["start"], e["end"])), None)
        fp_records.append({
            "raw_type": x["raw_type"], "gtype": x["gtype"], "text": x["text"],
            "on_negative": (on_neg["why"] if on_neg else None),
            "cross_type": (cross["type"] if cross else None),
        })

    # ---- masking_correctness (A/B/C) по КАЖДОЙ маске системы ----
    # Знаменатель — маски (kept), а НЕ gold: меряем «из пойманного, сколько
    # корректно».  det[i] соответствует kept[i] (map_entities_to_pt1 сохраняет
    # порядок), поэтому одну и ту же маску смотрим в двух системах координат:
    # plain (для A) и PT-1 (для B/C, где размечен gold).
    masks_pt1 = [
        {"start": x["start"], "end": x["end"], "gtype": x["gtype"]}
        for x in det_located
    ]
    mask_records = []
    for e, x in zip(kept, det):
        base = plain_offs.get(e.segment_id)
        if base is None:
            # сегмент не попал в plain-сборку — координаты A недействительны,
            # считаем нарушением консервативно (см. mc_check_a, reason unaligned).
            a = {"a_ok": False, "a_channel_ok": False, "reason": "no_plain_offset"}
        else:
            a = ML.mc_check_a(base + e.start, e.original_text, restored, plain,
                              aligned=aligned)
        if x["start"] is None:
            bc = {"scored": False, "b_ok": None, "c_ok": None,
                  "gold_type": None, "mask_type": x["gtype"]}
        else:
            bc = ML.mc_check_bc(
                {"start": x["start"], "end": x["end"], "gtype": x["gtype"]},
                d["entities"], masks_pt1,
            )
        mask_records.append({
            "gtype": x["gtype"], "raw_type": x["raw_type"], "token": e.token,
            "text": e.original_text,
            "a_ok": a["a_ok"], "a_channel_ok": a["a_channel_ok"],
            "a_reason": a["reason"],
            "scored": bc["scored"], "b_ok": bc["b_ok"], "c_ok": bc["c_ok"],
            "gold_type": bc["gold_type"],
        })

    doc_leaked = any(r["leaked"] for r in ent_records)
    doc_leaked_v2 = any(r["leak_v2"]["status"] != "none" for r in ent_records)
    return {
        # исход документа: processed | refused | crashed (этап 1b — все три
        # исхода теперь настоящие; refused возникает при allow_lossy=False).
        "outcome": "processed",
        # Зоны, из-за которых документ БЫЛ БЫ отклонён в strict-режиме. В
        # lossy-прогоне документ обработан, но их текст в анонимный результат не
        # попал — список показывает, сколько именно молча выброшено.  Сырой текст
        # зон здесь НЕ храним (в отличие от sidecar'а блока 7): results_*.json
        # коммитится в git, и класть в него ПДн ради метрики незачем — для
        # метрики хватает kind/part/char_count.
        "refused_zones": refused_zones,
        "doc_id": doc_id, "format": d["format"], "source": d["source"],
        "contract_type": d.get("contract_type", ""),
        "n_entities": len(d["entities"]),
        "roundtrip_ok": roundtrip_ok, "rt": rt,
        "roundtrip_lenG": len(plain), "unresolved": unresolved,
        "loc_misses": loc_misses,
        "n_detected": len(kept), "n_detected_located": len(det_located),
        "doc_leaked": doc_leaked,
        "doc_leaked_v2": doc_leaked_v2,
        "entities": ent_records,
        # masking_correctness: по одной записи на КАЖДУЮ маску системы (см.
        # measure_lib, блок masking_correctness). Знаменатель метрики — этот
        # список, а не "entities" (gold).
        "masks": mask_records,
        "false_positives": fp_records,
    }


def load_gold():
    gold = json.load(open(GOLD, encoding="utf-8"))
    gold.sort(key=lambda d: d["doc_id"])
    return gold


def run_all(gold, verbose=True, on_checkpoint=None, checkpoint_every=50):
    """Гоняет process_doc по всему gold, ловя исключения per-документ (креш
    одного документа не должен обрывать замер остальных 323 — см. outcome
    processed|crashed).  on_checkpoint(results), если задан, вызывается каждые
    checkpoint_every документов — используется main() для инкрементальной
    записи results_<commit>.json на длинных прогонах.  Переиспользуется
    gate.py (этап 0d), поэтому вынесена из main() отдельно."""
    results = []
    t0 = time.time()
    for i, d in enumerate(gold):
        try:
            rec = process_doc(d)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            rec = {"outcome": "crashed", "doc_id": d["doc_id"], "error": repr(exc)}
        results.append(rec)
        if verbose and ((i + 1) % 10 == 0 or i == 0):
            dt = time.time() - t0
            print(f"[{i+1}/{len(gold)}] {d['doc_id']}  {dt:.0f}s  "
                  f"({(i+1)/dt:.1f} doc/s)", flush=True)
        if on_checkpoint and (i + 1) % checkpoint_every == 0:
            on_checkpoint(results)
    return results


def main():
    gold = load_gold()
    t0 = time.time()
    results = run_all(
        gold, verbose=True,
        on_checkpoint=lambda res: json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False),
    )
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {len(results)} docs in {time.time()-t0:.0f}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
