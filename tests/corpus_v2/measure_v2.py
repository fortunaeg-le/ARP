# -*- coding: utf-8 -*-
"""
measure_v2.py — ЗАМЕРНАЯ ДОРОЖКА ДЛЯ КОРПУСА V2 (этап T2, шаг 1).

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ПРАВКА tests/corpus/run_measurement.py.
Старый харнесс прибит к своему эталону: путь к `gold.json`, свой набор типов,
свои пороги leak, свой baseline и семь линий гейта поверх. Корпус v2 — ДРУГОЙ
эталон (другие документы, другие типы: MONEY/PERCENT/TERM/TRANCHE, типизированные
негативы DOCNUM/DATE) и НЕ является точкой отсчёта регресс-гейта. Слить их в один
файл значило бы либо сделать старый гейт зависимым от нового корпуса, либо
завести в нём ветвления «если корпус v2» — то есть ровно тот рассинхрон, против
которого заведён src/pipeline.py.

ЧТО ПЕРЕИСПОЛЬЗУЕТСЯ, А НЕ ДУБЛИРУЕТСЯ:
  * ДЕТЕКЦИЯ — `pipeline.run_detection`, продуктовый путь, тот же, что у CLI, UI
    и гейта старого корпуса. Отдельной копии порядка слоёв здесь нет;
  * ПРИБОРЫ — `tests/corpus/measure_lib.py` целиком (pt1_text, глобализация
    координат, masking A/B/C, precision_by_type, boundary_by_gold — линия «е»
    этапа GATE-2). Записи прогона пишутся В ТОМ ЖЕ ФОРМАТЕ, что у
    run_measurement.process_doc, поэтому агрегаты measure_lib работают над ними
    без единой ветки «а это v2».

СООТВЕТСТВИЕ ИМЁН (шаг 0б задания). В коде денежный тип называется SUM
(entity_types.yaml, token_prefix SUM), в разметке v2 — MONEY. Ни тип, ни разметку
не переименовываем: соответствие живёт ЗДЕСЬ, одной записью `_GOLD_ALIAS`, и
применяется к ЭТАЛОНУ на входе замера. Дальше всё говорит на языке типов кода
(SUM), как и measure_lib.TYPE_MAP, который переводит PERSON->PER,
INN_PERSON->INN_PER и т.д.

КРАСНАЯ ЛИНИЯ КОРПУСА (README корпуса v2): цифры, полученные на ПОРОЖДЁННОМ
корпусе, — ВЕРХНЯЯ ГРАНИЦА, а не измерение. Печатается в шапке каждой таблицы.

Запуск:
    venv/Scripts/python.exe tests/corpus_v2/measure_v2.py            # полный прогон
    venv/Scripts/python.exe tests/corpus_v2/measure_v2.py --limit 20 # первые 20 док.
    venv/Scripts/python.exe tests/corpus_v2/measure_v2.py --results <файл>  # отчёт по готовому дампу
"""
import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
from detokenizer import detokenize  # noqa: E402
from extractor import extract  # noqa: E402
from pipeline import run_detection  # noqa: E402
from session_store import save_session  # noqa: E402
from tokenizer import build_plain_text, tokenize  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
GOLD = os.path.join(HERE, "gold_v2.json")
DOCS = os.path.join(HERE, "docs")
OUT = os.path.join(HERE, "results_v2_current.json")

#: Соответствие «имя в разметке v2» -> «имя типа в коде» (шаг 0б задания).
#: Одна запись: денежная сумма. Разметка v2 зовёт её MONEY, entity_types.yaml —
#: SUM. Переименование ни там, ни там НЕ делается: имя SUM уже несёт на себе
#: историю чисел старого корпуса (этап T-GOLD-A), а имя MONEY — историю разметки
#: v2; переименовав любое, мы порвали бы сравнимость с прошлым.
_GOLD_ALIAS = {"MONEY": "SUM"}

#: Типы, которые ВВЁЛ этап T2. Оставлено ИСТОРИЧЕСКОЙ меткой (на неё ссылаются
#: отчёты) и запасным составом, если конфиг вдруг не прочитался. Составом
#: форменной таблицы БОЛЬШЕ НЕ ЯВЛЯЕТСЯ — см. measured_types().
T2_TYPES = ("SUM", "PERCENT", "TERM")

#: Типы, у которых утечка меряется ТОЛЬКО ПО ПОЗИЦИИ (текстовая метрика
#: нечестна: короткое ядро или словесное значение совпадёт в анонимном
#: тексте случайно). ЭТАП TYPE-FACTORY-2 добавил SHARE_PCT («40 %» — то же
#: короткое ядро, что у PERCENT) и TRANCHE (именной транш — слова без цифр).
_POSITIONAL_TYPES = ("PERCENT", "TERM", "SHARE_PCT", "TRANCHE")

#: Типизированные негативы v2 — конструкции, которые НЕ маскируются по решению
#: владельца (решение по номерам договоров и датам документа не принято). Маска
#: на них — ложное срабатывание новых детекторов, считается отдельно.
TYPED_NEGATIVES = ("DOCNUM", "DATE")

# Хранилище сессий — временное; ~/.shifrator не трогаем (как в run_measurement).
STORAGE = tempfile.mkdtemp(prefix="shifrator_measure_v2_")


def gold_type(raw: str) -> str:
    return _GOLD_ALIAS.get(raw, raw)


# --------------------------------------------------------------------------- #
#   СОСТАВ ФОРМЕННОЙ ТАБЛИЦЫ — ИЗ СОБРАННОГО КОНФИГА, А НЕ ИЗ СПИСКА ЗДЕСЬ      #
# --------------------------------------------------------------------------- #
# ЭТАП DEBT-1, часть 2 (находка MEASURE-NOW). Состав таблицы был константой
# T2_TYPES = (SUM, PERCENT, TERM) — тремя типами, которые ввёл этап T2. Типы,
# заведённые ФАБРИКОЙ после него (CONTRACT_NO, REG_ID, REG_NUMBER, SHARE_PCT,
# TRANCHE — пять штук на дату этапа), в таблицу не попадали: они детектировались,
# считались в дампе, но в форменный отчёт не выводились. Это МОЛЧАЛИВОЕ слепое
# пятно: новый тип заводится одним файлом-описанием, а его полнота/границы/утечка
# в отчёте не появляются, и никакая линия об этом не краснеет.
#
# Теперь состав ВЫЧИСЛЯЕТСЯ:
#   * берём типы собранного entity_types.yaml, у которых есть блок `measure:` —
#     это ровно отпечаток фабрики (assemble_types.py переносит его из
#     typedefs/<ТИП>.yaml; ручные PERSON/ORG/ADDRESS/CLAUSE_REF его не имеют);
#   * имя типа в РАЗМЕТКЕ v2 может отличаться от ключа конфига (конфиг
#     BANK_ACCOUNT — разметка ACCOUNT), поэтому кандидатами берём И ключ, И
#     token_prefix и оставляем тот, что реально встречается в gold_v2;
#   * типизированные негативы (DOCNUM/DATE) исключаем: у них СВОЯ таблица, где
#     маска считается ложным срабатыванием, а не полнотой.
# Тип, которого в разметке нет вовсе (INN_PERSON), молча выпадает — считать
# нечего; появится в разметке — появится в таблице сам, без правки этого файла.
def measured_types(config_path=None, gold=None):
    """Состав форменной таблицы: фабричные типы собранного конфига ∩ разметка v2.
    Порядок — как в конфиге (он же порядок арбитража)."""
    from config_cache import load_yaml_cached
    spec = load_yaml_cached(config_path or CONFIG)["entity_types"]
    marked = set()
    for d in (gold if gold is not None else load_gold()):
        for e in d.get("entities", ()):
            marked.add(gold_type(e["type"]))
    out = []
    for t, s in spec.items():
        if not isinstance(s, dict) or not isinstance(s.get("measure"), dict):
            continue          # не фабричный тип — своей таблицы не просит
        if t in TYPED_NEGATIVES:
            continue          # у негативов отдельная таблица (print_typed_negatives)
        for name in (t, s.get("token_prefix")):
            if name in marked and name not in TYPED_NEGATIVES:
                out.append(name)
                break
    return tuple(out)


# --------------------------------------------------------------------------- #
#     УТЕЧКА ПО ПОЗИЦИИ — для типов, у которых текстовая метрика неприменима    #
# --------------------------------------------------------------------------- #
# leak_v2 старого харнесса ищет ВЫЖИВШУЮ ПОДСТРОКУ значения в анонимном тексте.
# Для реквизита это верно: 20-значный счёт не встречается в тексте случайно.
# Для процента и срока — НЕТ: ядро «18» из «18%» или «5» из «в течение 5 дней»
# встретится в анонимном документе десятки раз по совпадению (в номере пункта,
# в дате, внутри чужого числа), и текстовая метрика показала бы утечку там, где
# значение закрыто маской. Тот же класс ошибки, что уже описан у
# leak_v2_address («пер» ⊂ «передать»).
#
# Поэтому для PERCENT/TERM утечка считается ПО ПОЗИЦИИ: сколько ЦИФР эталонного
# спана осталось не закрыто НИ ОДНОЙ маской (любого типа — соседняя маска тоже
# прячет значение). Это тот же прибор, что линия «е» (measure_lib.uncovered_pieces),
# и он не может ошибиться совпадением: он смотрит на конкретные символы
# конкретного документа.
#
# Считаем ЦИФРЫ, а не все символы: слова «в течение … банковских дней» —
# формула договора, она не является защищаемым значением; защищается ЧИСЛО
# (и дата в форме «не позднее ДД.ММ.ГГГГ»).
def leak_by_position(text_slice: str, gs: int, uncovered):
    """{status, open_digits, core_digits} — утечка значения по позиции.

    text_slice — срез PT-1 [gs:ge) (сам эталонный текст);
    gs         — начало спана в PT-1 (для перевода координат uncovered);
    uncovered  — куски [s,e) спана, НЕ закрытые ни одной маской.
    """
    core = sum(1 for ch in text_slice if ch.isdigit())
    open_digits = 0
    for s, e in uncovered:
        open_digits += sum(1 for ch in text_slice[s - gs:e - gs] if ch.isdigit())
    if core == 0:
        # У значения нет цифр вовсе (в v2 таких среди PERCENT/TERM нет).
        # Консервативно: открытый хоть на символ спан считаем утечкой.
        status = "full" if uncovered else "none"
        return {"status": status, "open_digits": 0, "core_digits": 0}
    if open_digits == 0:
        status = "none"
    elif open_digits >= core:
        status = "full"
    else:
        status = "partial"
    return {"status": status, "open_digits": open_digits, "core_digits": core}


def process_doc(d, config=None):
    """`config` — путь к entity_types.yaml. Параметр нужен ровно для одного
    сценария: снять ТОЧКУ ОТСЧЁТА (шаг 0в задания T2) — что находит система БЕЗ
    новых типов, — не откатывая рабочее дерево. Копия конфига с
    `enabled: false` у новых типов даёт ту же детекцию, что была до этапа."""
    config = config or CONFIG
    doc_id = d["doc_id"]
    path = os.path.join(DOCS, doc_id + "." + d["format"])

    G = ML.pt1_text(path)
    body_start = ML.body_offset(path, G)

    doc = extract(path)
    entities = run_detection(doc, config)
    # Замер ВСЕГДА на «Максимуме» — как и старый харнесс (этап T1, шаг 3):
    # мерить надо работу программы, а не настройку пользователя.
    anon_text, kept = tokenize(doc, entities, config, enabled_types=type_policy.MAXIMUM)

    sid = save_session(kept, session_id=None, ttl_hours=24, storage_dir=STORAGE)
    restored, unresolved = detokenize(anon_text, sid, storage_dir=STORAGE, mode="exact")
    plain = build_plain_text(doc)
    roundtrip_ok = (restored == plain)
    # Классификация расхождения — та же, что у run_measurement: расхождение
    # ТОЛЬКО в пробельных символах — это артефакт схлопывания '\n'->' ' при
    # сборке plain из ячейки таблицы, само значение при этом цело (инвариант
    # проекта меряет masking A, а не побайтное равенство сборки).
    ws_only = None
    if not roundtrip_ok:
        ws = {" ", "\n", "\t", "\r"}
        if len(restored) == len(plain):
            ws_only = all(a in ws and b in ws
                          for a, b in zip(restored, plain) if a != b)
        else:
            ws_only = False

    plain2, plain_offs = ML.plain_segment_offsets(doc)
    assert plain2 == plain, f"{doc_id}: plain_segment_offsets разошлась с build_plain_text"
    aligned = (len(restored) == len(plain))

    offs, loc_misses = ML.build_segment_offsets(doc, G, body_start)
    det = ML.map_entities_to_pt1(kept, offs, G)
    det_located = [x for x in det if x["start"] is not None]

    golds = [dict(e, type=gold_type(e["type"])) for e in d["entities"]]
    negs = [dict(e, type=gold_type(e.get("type") or "")) for e in d.get("negatives", [])]
    igns = d.get("ignore", [])

    anon_norm_v2 = ML.v2_norm_text(anon_text)
    anon_digit_field = ML.v2_digit_runs(anon_text)
    anon_date_field = ML.v2_date_field(anon_text)

    # ---- границы по направлению ошибки (тот же прибор, что линия «е») ----
    all_mask_spans = [(x["start"], x["end"]) for x in det_located]
    bnd_by_pos = {}
    for t in sorted({g["type"] for g in golds}):
        pos_t = [i for i, g in enumerate(golds) if g["type"] == t]
        golds_t = [golds[i] for i in pos_t]
        masks_t = [(x["start"], x["end"]) for x in det_located if x["gtype"] == t]
        for i, b in zip(pos_t, ML.boundary_by_gold(golds_t, masks_t, all_mask_spans)):
            bnd_by_pos[i] = b

    ent_records = []
    for gi, e in enumerate(golds):
        gs, ge, gt = e["start"], e["end"], e["type"]
        same = [x for x in det_located
                if x["gtype"] == gt and ML._iv_overlap(gs, ge, x["start"], x["end"])]
        anyt = [x for x in det_located if ML._iv_overlap(gs, ge, x["start"], x["end"])]
        found = len(same) > 0
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
        if found:
            reason = None
        elif anyt:
            reason = "wrong_type:" + ",".join(sorted(set(x["gtype"] for x in anyt)))
        else:
            reason = "nothing"

        # утечка: текстовая (как в старом харнессе) — для типов, где она честна;
        # позиционная — для всех, она же единственная для PERCENT/TERM.
        if gt in _POSITIONAL_TYPES:
            v2 = {"status": "none", "fragments": []}
        else:
            v2 = RM.leak_v2(gt, e["text"], anon_norm_v2, anon_digit_field, anon_date_field)
        uncovered = ML.uncovered_pieces(gs, ge, all_mask_spans)
        pos = leak_by_position(G[gs:ge], gs, uncovered)
        if gt in _POSITIONAL_TYPES:
            v2 = dict(pos)   # для этих типов позиционная метрика И ЕСТЬ утечка

        ent_records.append({
            "type": gt, "raw_type": e["type"], "category": e.get("category", ""),
            "text": e["text"], "found": found, "exact": exact, "full": full,
            "left_trim": left_trim, "right_trim": right_trim,
            "miss_reason": reason,
            "leaked": v2["status"] != "none", "leak_pieces": [],
            "leak_v1": v2["status"] != "none",
            "leak_v2": v2,
            "leak_pos": pos,
            ML.BND_FIELD: bnd_by_pos[gi],
        })

    # ---- ложные срабатывания ----
    fp_records = []
    for x in det_located:
        tp = any(x["gtype"] == g["type"]
                 and ML._iv_overlap(x["start"], x["end"], g["start"], g["end"])
                 for g in golds)
        if tp:
            continue
        on_ign = any(ML._iv_overlap(x["start"], x["end"], g["start"], g["end"]) for g in igns)
        if on_ign:
            continue
        on_neg = next((g for g in negs
                       if ML._iv_overlap(x["start"], x["end"], g["start"], g["end"])), None)
        cross = next((g for g in golds
                      if ML._iv_overlap(x["start"], x["end"], g["start"], g["end"])), None)
        fp_records.append({
            "raw_type": x["raw_type"], "gtype": x["gtype"], "text": x["text"],
            "on_negative": (on_neg["why"] if on_neg else None),
            "neg_type": (on_neg["type"] if on_neg else None),
            "cross_type": (cross["type"] if cross else None),
        })

    masks_pt1 = [{"start": x["start"], "end": x["end"], "gtype": x["gtype"]}
                 for x in det_located]
    mask_records = []
    for e, x in zip(kept, det):
        base = plain_offs.get(e.segment_id)
        if base is None:
            a = {"a_ok": False, "a_channel_ok": False, "reason": "no_plain_offset"}
        else:
            a = ML.mc_check_a(base + e.start, e.original_text, restored, plain, aligned=aligned)
        if x["start"] is None:
            bc = {"scored": False, "b_ok": None, "c_ok": None, "gold_type": None}
        else:
            bc = ML.mc_check_bc({"start": x["start"], "end": x["end"], "gtype": x["gtype"]},
                                golds, masks_pt1)
        mask_records.append({
            "gtype": x["gtype"], "raw_type": x["raw_type"], "token": e.token,
            "text": e.original_text, "start": x["start"], "end": x["end"],
            "a_ok": a["a_ok"], "a_channel_ok": a["a_channel_ok"], "a_reason": a["reason"],
            "scored": bc["scored"], "b_ok": bc["b_ok"], "c_ok": bc["c_ok"],
            "gold_type": bc["gold_type"],
        })

    return {
        "outcome": "processed", "doc_id": doc_id, "format": d["format"],
        "n_entities": len(golds), "roundtrip_ok": roundtrip_ok, "rt_ws_only": ws_only,
        "unresolved": unresolved, "loc_misses": loc_misses,
        "n_detected": len(kept), "n_detected_located": len(det_located),
        "entities": ent_records, "masks": mask_records,
        "false_positives": fp_records,
    }


def load_gold():
    g = json.load(open(GOLD, encoding="utf-8"))
    g.sort(key=lambda d: d["doc_id"])
    return g


def run_all(gold, verbose=True, config=None):
    results = []
    t0 = time.time()
    for i, d in enumerate(gold):
        try:
            rec = process_doc(d, config=config)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            rec = {"outcome": "crashed", "doc_id": d["doc_id"], "error": repr(exc)}
        results.append(rec)
        if verbose and ((i + 1) % 10 == 0 or i == 0):
            dt = time.time() - t0
            print(f"[{i+1}/{len(gold)}] {d['doc_id']}  {dt:.0f}s", flush=True)
    return results


# --------------------------------------------------------------------------- #
#                                   ОТЧЁТ                                      #
# --------------------------------------------------------------------------- #
CEILING = ("  цифры на порождённом корпусе — верхняя граница, не измерение "
           "(см. tests/corpus_v2/README.md, «КРАСНАЯ ЛИНИЯ»)")


def _cat(rec):
    """Обычные / испорченные. В разметке v2 испорченность значения записана
    категорией `adversarial` (мутации: невидимые, омоглифы, разрывы, пробелы
    внутри числа); всё остальное — обычные."""
    return "испорч." if rec.get("category") == "adversarial" else "обычные"


def type_table(results, types):
    """{тип: {категория: {n, found, exact, leak, under_n, under_ch, over_n, over_ch}}}"""
    out = {}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if e["type"] not in types:
                continue
            d = out.setdefault(e["type"], {}).setdefault(_cat(e), {
                "n": 0, "found": 0, "exact": 0, "leak": 0, "leak_full": 0,
                "under_n": 0, "under_ch": 0, "over_n": 0, "over_ch": 0,
                "not_found_bnd": 0,
            })
            d["n"] += 1
            d["found"] += int(e["found"])
            d["exact"] += int(e["exact"])
            st = e["leak_pos"]["status"]
            d["leak"] += int(st != "none")
            d["leak_full"] += int(st == "full")
            b = e[ML.BND_FIELD]
            if b["direction"] == "not_found":
                d["not_found_bnd"] += 1
                continue
            if b["direction"] in ("shorter", "both"):
                d["under_n"] += 1
                d["under_ch"] += b["under"]
            if b["direction"] in ("longer", "both"):
                d["over_n"] += 1
                d["over_ch"] += b["over"]
    return out


def print_types(results, types):
    tab = type_table(results, types)
    prec = ML.precision_by_type(results)
    print("\n=== ПОЛНОТА / ТОЧНОСТЬ / ГРАНИЦЫ ПО НАПРАВЛЕНИЮ / УТЕЧКА ===")
    print(CEILING)
    head = ("  %-8s %-8s %6s %8s %8s %8s | %11s %11s | %8s" %
            ("тип", "класс", "n", "полнота", "точн.гр.", "утечка", "недобор", "перебор", "точность"))
    print(head)
    print("  " + "-" * (len(head) - 2))
    for t in types:
        cats = tab.get(t, {})
        p = (prec["per_type"].get(t) or {}).get("precision")
        for cat in ("обычные", "испорч."):
            d = cats.get(cat)
            if not d:
                continue
            print("  %-8s %-8s %6d %7.1f%% %7.1f%% %7.1f%% | %5d %5dс %5d %5dс | %8s"
                  % (t, cat, d["n"],
                     100.0 * d["found"] / d["n"], 100.0 * d["exact"] / d["n"],
                     100.0 * d["leak"] / d["n"],
                     d["under_n"], d["under_ch"], d["over_n"], d["over_ch"],
                     ("%.2f%%" % p) if p is not None else "n/a"))
        tot = {k: sum(c[k] for c in cats.values()) for k in
               ("n", "found", "exact", "leak", "under_n", "under_ch", "over_n", "over_ch")} if cats else None
        if tot and tot["n"]:
            print("  %-8s %-8s %6d %7.1f%% %7.1f%% %7.1f%% | %5d %5dс %5d %5dс | %8s"
                  % (t, "ВСЕГО", tot["n"],
                     100.0 * tot["found"] / tot["n"], 100.0 * tot["exact"] / tot["n"],
                     100.0 * tot["leak"] / tot["n"],
                     tot["under_n"], tot["under_ch"], tot["over_n"], tot["over_ch"],
                     ("%.2f%%" % p) if p is not None else "n/a"))
    print("  полнота = найдена маска своего типа; точн.гр. = спан ровно по эталону;")
    print("  утечка = цифры значения, не закрытые НИ ОДНОЙ маской (позиционная метрика);")
    print("  точность = tp/(tp+fp на ОБЪЯВЛЕННОМ негативе), та же ось, что линия «а» гейта.")


def print_typed_negatives(results):
    """Маски, легшие на ТИПИЗИРОВАННЫЕ негативы v2 (DOCNUM/DATE) — ложные
    срабатывания по определению задания."""
    print("\n=== ЛОЖНЫЕ СРАБАТЫВАНИЯ НА ТИПИЗИРОВАННЫХ НЕГАТИВАХ (DOCNUM / DATE) ===")
    print(CEILING)
    by = {}
    examples = {}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for f in r["false_positives"]:
            nt = f.get("neg_type")
            if nt not in TYPED_NEGATIVES:
                continue
            key = (nt, f["gtype"])
            by[key] = by.get(key, 0) + 1
            examples.setdefault(key, []).append((r["doc_id"], f["text"]))
    if not by:
        print("  0 — ни одна маска не легла на DOCNUM/DATE.")
        return
    for (nt, gt), n in sorted(by.items(), key=lambda kv: -kv[1]):
        print("  негатив %-7s <- маска %-9s : %4d   напр. %s"
              % (nt, gt, n, examples[(nt, gt)][0]))


def print_roundtrip(results):
    ok = sum(1 for r in results if r.get("outcome") == "processed" and r["roundtrip_ok"])
    n = sum(1 for r in results if r.get("outcome") == "processed")
    ws = [r["doc_id"] for r in results if r.get("outcome") == "processed"
          and not r["roundtrip_ok"] and r.get("rt_ws_only")]
    hard = [r["doc_id"] for r in results if r.get("outcome") == "processed"
            and not r["roundtrip_ok"] and not r.get("rt_ws_only")]
    mc = ML.mc_aggregate([m for r in results if r.get("outcome") == "processed"
                          for m in r["masks"]])
    a, b, c = ML.mc_rates(mc["total"])
    print("\n=== ВОССТАНОВЛЕНИЕ И КОРРЕКТНОСТЬ МАСКИРОВКИ ===")
    print("  документов обработано: %d, круговое восстановление побайтно: %d (%.1f%%)"
          % (n, ok, 100.0 * ok / n if n else 0.0))
    print("    расхождения ТОЛЬКО в пробельных (артефакт сборки plain, значение цело): %d %s"
          % (len(ws), ws[:5]))
    print("    расхождения В ЗНАЧАЩИХ символах (порча): %d %s" % (len(hard), hard[:5]))
    print("  масок: %d;  A round-trip %.2f%%   B границы %.2f%%   C тип %.2f%%"
          % (mc["total"]["n_masks"], a, b, c))
    for t in T2_TYPES:
        s = mc["per_type"].get(t)
        if not s:
            continue
        a2, b2, c2 = ML.mc_rates(s)
        print("    %-8s масок %5d  A %.2f%%  B %.2f%%  C %.2f%%"
              % (t, s["n_masks"], a2, b2, c2))
    crashed = [r["doc_id"] for r in results if r.get("outcome") != "processed"]
    if crashed:
        print("  КРЕШИ: %d — %s" % (len(crashed), ", ".join(crashed[:10])))


def print_overmask(results):
    prec = ML.precision_by_type(results)
    print("\n=== МАСКИ, НЕ ЛЁГШИЕ НИ НА ЧТО (порча документа) — по типам ===")
    for t, d in sorted(prec["per_type"].items(), key=lambda kv: -kv[1]["nothing"]):
        if not d["nothing"] and not d["cross"] and not d["fp_neg"]:
            continue
        print("  %-9s nothing %5d  cross %5d  fp_neg %5d  tp %6d"
              % (t, d["nothing"], d["cross"], d["fp_neg"], d["tp"]))
    print("  ВСЕГО nothing %d, cross %d, fp_neg %d"
          % (prec["nothing_total"], prec["cross_total"], prec["fp_neg_total"]))


def report(results, types=T2_TYPES):
    print_types(results, types)
    print_typed_negatives(results)
    print_roundtrip(results)
    print_overmask(results)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--results", default=None, help="отчёт по готовому дампу, без прогона")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--types", default=None,
                    help="состав форменной таблицы; по умолчанию — ИЗ СОБРАННОГО "
                         "КОНФИГА (фабричные типы ∩ разметка v2), см. measured_types()")
    ap.add_argument("--config", default=CONFIG, help="иной entity_types.yaml (точка отсчёта шага 0в)")
    args = ap.parse_args(argv)

    if args.results:
        results = json.load(open(args.results, encoding="utf-8"))
    else:
        gold = load_gold()
        if args.limit:
            gold = gold[:args.limit]
        print("Прогон корпуса v2: %d документов…" % len(gold))
        t0 = time.time()
        results = run_all(gold, config=args.config)
        json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
        print("DONE %d docs in %.0fs -> %s" % (len(results), time.time() - t0, args.out))

    types = (tuple(t for t in args.types.split(",") if t) if args.types
             else measured_types(args.config))
    print("\nсостав таблицы (%d типов, из собранного конфига%s): %s"
          % (len(types), "" if not args.types else " — ПЕРЕОПРЕДЕЛЁН --types",
             ", ".join(types)))
    report(results, types)
    return 0


if __name__ == "__main__":
    sys.exit(main())
