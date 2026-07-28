# -*- coding: utf-8 -*-
"""
reclassify_sum.py — этап T-GOLD-A: суммы перестают быть негативами и становятся
эталонными сущностями типа SUM.

ЗАЧЕМ
-----
Корпус этапа 1 строился по схеме из 13 типов ПДн; денежная сумма в неё не
входила и была размечена как НЕГАТИВ — «текст, похожий на данные, но данными не
являющийся». С тех пор владелец решил, что суммы маскируются: `SUM` есть в
`entity_types.yaml`, включён, у него есть работающий regex-детектор и место в
приоритете токенизатора. Разметка осталась прежней, и получилось прямо обратное
правде: каждое ПРАВИЛЬНОЕ срабатывание системы на сумме харнесс засчитывал как
ЛОЖНОЕ (635 из 648 негативов «сумма» на HEAD).

ЧТО ДЕЛАЕТ СКРИПТ
-----------------
Переносит записи `negatives` с `why == "сумма — не ПДн"` в `entities` с
`type: "SUM"`. ГРАНИЦЫ НЕ МЕНЯЮТСЯ вообще: `start`/`end`/`text` копируются
побайтно (прошлая проверка установила, что все 648 размечены с валютой внутри
границ, поэтому подгонка спанов не нужна и была бы отсебятиной).

ИМЯ ТИПА — `SUM`, ровно как в `entity_types.yaml` и в
`measure_lib.TYPE_MAP["SUM"] == "SUM"`. Любое другое имя увело бы расхождение
маски и эталона в раздел, который гейт не роняет.

КАТЕГОРИЯ И TRICK — НЕ ВЫДУМАНЫ, А ВОССТАНОВЛЕНЫ
------------------------------------------------
У каждой эталонной сущности схема требует `category`. Пометить все 648
`canonical` было бы неправдой: 47 сумм физически испорчены мутацией
(омоглиф в цифрах, невидимый символ, перенос строки внутри числа) — ровно тот
класс, ради которого корпус и существует.

Правило восстановления — то же самое, по которому `mutate.py` метит сущности:
чанк, который мутация ИЗМЕНИЛА, становится `adversarial` с `trick = "mut:<правило>"`.
Негативы `mutate.py` не метил (у негатива нет полей cat/trick), поэтому признак
берётся сравнением с базовым документом: n-я сумма мутанта против n-й суммы его
базы. Отличается текст — значит мутация её задела.

ЗАПУСК
------
    venv/Scripts/python.exe tests/corpus/reclassify_sum.py --dry-run   # только дамп
    venv/Scripts/python.exe tests/corpus/reclassify_sum.py             # запись gold.json

После записи ОБЯЗАТЕЛЬНО, в этом порядке:
    venv/Scripts/python.exe tests/corpus/validate.py          # спаны и непересечения
    venv/Scripts/python.exe tests/corpus/run_measurement.py   # -> новый results_baseline.json
    venv/Scripts/python.exe tests/corpus/run_iter_baseline.py # -> baseline среза
    venv/Scripts/python.exe tests/corpus/update_manifest.py   # -> MANIFEST.sha256

ПОВТОРНЫЙ ЗАПУСК безопасен: скрипт идемпотентен (если негативов «сумма» уже
нет — он ничего не делает и говорит об этом).
"""
import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "gold.json")

# Точная формулировка причины в разметке этапа 1. Сравнение ПОЛНОЕ, не по
# префиксу: префикс «сумма» пришлось бы объяснять, а полное совпадение —
# самодостаточно и не заденет причину, которую допишут позже.
SUM_WHY = "сумма — не ПДн"

# Тип в эталоне == тип в entity_types.yaml == measure_lib.TYPE_MAP["SUM"].
SUM_TYPE = "SUM"

NOTE = "переклассифицирована из негатива (этап T-GOLD-A): суммы маскируются"

# Зерно дампа «20 до / 20 после». Зафиксировано, чтобы владелец мог посмотреть
# ровно ту же выборку повторно, а не новую случайную.
DUMP_SEED = 20260728
DUMP_N = 20


def base_id(doc_id):
    return doc_id.split("__", 1)[0]


def mutation_rule(doc):
    """Правило мутации документа ('homoglyph', 'combo', …) или None для базы.
    source имеет вид 'mutated:<маркер>:<правило>'."""
    src = doc.get("source", "")
    if not src.startswith("mutated:"):
        return None
    return src.rsplit(":", 1)[-1]


def sum_negatives(doc):
    return [n for n in doc.get("negatives", []) if n.get("why") == SUM_WHY]


def build_entity(neg, category, trick):
    """Эталонная сущность из негатива. Границы КОПИРУЮТСЯ, не пересчитываются."""
    e = {
        "type": SUM_TYPE,
        "start": neg["start"],
        "end": neg["end"],
        "text": neg["text"],
        "category": category,
        "note": NOTE,
    }
    if trick:
        e["trick"] = trick
    return e


def plan(gold):
    """[(doc_id, индекс негатива, негатив, категория, trick)] — что будет перенесено."""
    by_id = {d["doc_id"]: d for d in gold}
    items = []
    for d in gold:
        negs = sum_negatives(d)
        if not negs:
            continue
        rule = mutation_rule(d)
        base = by_id.get(base_id(d["doc_id"]))
        base_negs = sum_negatives(base) if (rule and base is not None) else []
        if rule and len(base_negs) != len(negs):
            # Порядок и число сумм в мутанте обязаны совпадать с базой: мутации
            # сумм не добавляют и не удаляют. Не совпало — сопоставление n-й с
            # n-й недействительно, и молча ставить canonical нельзя.
            raise SystemExit(
                "%s: сумм в мутанте %d, в базе %s — %d. Сопоставление с базой "
                "невозможно, разметка категорий была бы догадкой."
                % (d["doc_id"], len(negs), base_id(d["doc_id"]), len(base_negs))
            )
        for i, n in enumerate(negs):
            touched = bool(rule) and n["text"] != base_negs[i]["text"]
            category = "adversarial" if touched else "canonical"
            trick = ("mut:" + rule) if touched else None
            items.append((d["doc_id"], i, n, category, trick))
    return items


def apply_to_gold(gold):
    """Изменяет gold на месте. Возвращает число перенесённых записей."""
    marks = {}
    for doc_id, i, n, category, trick in plan(gold):
        marks[(doc_id, n["start"], n["end"])] = (category, trick)
    moved = 0
    for d in gold:
        negs = d.get("negatives", [])
        keep, move = [], []
        for n in negs:
            if n.get("why") == SUM_WHY:
                move.append(n)
            else:
                keep.append(n)
        if not move:
            continue
        d["negatives"] = keep
        for n in move:
            category, trick = marks[(d["doc_id"], n["start"], n["end"])]
            d["entities"].append(build_entity(n, category, trick))
            moved += 1
        # Эталон хранится в порядке появления в тексте — сохраняем инвариант.
        d["entities"].sort(key=lambda e: (e["start"], e["end"]))
    return moved


def dump(items, n_show=DUMP_N, seed=DUMP_SEED):
    """Дамп «до/после» по одной и той же случайной выборке (зерно фиксировано)."""
    rnd = random.Random(seed)
    idx = sorted(rnd.sample(range(len(items)), min(n_show, len(items))))
    print("Случайная выборка %d из %d записей (зерно %d):"
          % (len(idx), len(items), seed))
    print()
    print("ДО — роль «негатив» (ложным считается ЛЮБОЕ срабатывание внутри спана):")
    print("  %-34s %-13s %-22s %s" % ("документ", "границы", "текст", "why"))
    for k in idx:
        doc_id, _, n, _, _ = items[k]
        print("  %-34s %6d-%-6d %-22r %s"
              % (doc_id, n["start"], n["end"], n["text"], n["why"]))
    print()
    print("ПОСЛЕ — роль «эталонная сущность» (границы те же, побайтно):")
    print("  %-34s %-13s %-22s %-11s %s"
          % ("документ", "границы", "текст", "категория", "trick"))
    for k in idx:
        doc_id, _, n, category, trick = items[k]
        print("  %-34s %6d-%-6d %-22r %-11s %s"
              % (doc_id, n["start"], n["end"], n["text"], category, trick or "—"))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="показать дамп и сводку, gold.json не трогать")
    args = ap.parse_args()

    with open(GOLD, encoding="utf-8") as f:
        gold = json.load(f)

    items = plan(gold)
    if not items:
        print("Негативов «%s» в эталоне нет — переклассифицировать нечего "
              "(скрипт уже отработал?)." % SUM_WHY)
        return 0

    # --- контрольные величины ДО ---
    before = {
        "entities": sum(len(d["entities"]) for d in gold),
        "negatives": sum(len(d.get("negatives", [])) for d in gold),
        "ignore": sum(len(d.get("ignore", [])) for d in gold),
    }
    before_spans = sorted(
        (d["doc_id"], n["start"], n["end"], n["text"]) for d in gold
        for n in sum_negatives(d)
    )

    dump(items)

    cats = {}
    for _, _, _, category, trick in items:
        key = (category, trick or "—")
        cats[key] = cats.get(key, 0) + 1
    print("Состав переносимого (%d записей):" % len(items))
    for key in sorted(cats):
        print("  %-11s %-16s %d" % (key[0], key[1], cats[key]))
    print()

    if args.dry_run:
        print("--dry-run: gold.json не изменён.")
        return 0

    moved = apply_to_gold(gold)

    # --- контроль ПОСЛЕ (шаг 2 задания) ---
    after = {
        "entities": sum(len(d["entities"]) for d in gold),
        "negatives": sum(len(d.get("negatives", [])) for d in gold),
        "ignore": sum(len(d.get("ignore", [])) for d in gold),
    }
    after_spans = sorted(
        (d["doc_id"], e["start"], e["end"], e["text"]) for d in gold
        for e in d["entities"] if e["type"] == SUM_TYPE
    )
    problems = []
    if moved != len(items):
        problems.append("перенесено %d, планировалось %d" % (moved, len(items)))
    if before["entities"] + moved != after["entities"]:
        problems.append("сущностей %d -> %d, ожидалось +%d"
                        % (before["entities"], after["entities"], moved))
    if before["negatives"] - moved != after["negatives"]:
        problems.append("негативов %d -> %d, ожидалось -%d"
                        % (before["negatives"], after["negatives"], moved))
    if before["ignore"] != after["ignore"]:
        problems.append("серые зоны изменились: %d -> %d"
                        % (before["ignore"], after["ignore"]))
    if before_spans != after_spans:
        problems.append("границы/текст 648 записей изменились — это запрещено")
    if problems:
        print("!!! ПРОВЕРКА ПОСЛЕ ПЕРЕНОСА НЕ ПРОШЛА, файл НЕ записан:")
        for p in problems:
            print("    " + p)
        return 1

    # Формат записи ТОТ ЖЕ, что у файла этапа 1 (json.dumps indent=1,
    # ensure_ascii=False, БЕЗ завершающего перевода строки) — проверено
    # побайтным round-trip. Иначе diff показал бы переформатирование всего
    # файла вместо 648 содержательных правок.
    with open(GOLD, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(gold, ensure_ascii=False, indent=1))

    print("gold.json записан.")
    print("  записей всего:   %d -> %d (не изменилось: %s)"
          % (before["entities"] + before["negatives"] + before["ignore"],
             after["entities"] + after["negatives"] + after["ignore"],
             before["entities"] + before["negatives"] + before["ignore"]
             == after["entities"] + after["negatives"] + after["ignore"]))
    print("  сущностей:       %d -> %d (+%d)"
          % (before["entities"], after["entities"], moved))
    print("  негативов:       %d -> %d (-%d)"
          % (before["negatives"], after["negatives"], moved))
    print("  серых зон:       %d -> %d" % (before["ignore"], after["ignore"]))
    print("  границы 648 записей совпали с прежними побайтно: да")
    print()
    print("Дальше: validate.py (спаны и непересечения), затем пересборка "
          "результатов и MANIFEST.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
