# -*- coding: utf-8 -*-
"""by_entity.py — метрика ПО СУЩНОСТЯМ, а не по вхождениям (этап REG).

    venv/Scripts/python.exe tests/corpus/by_entity.py [дамп.json]

ЗАЧЕМ. Все прежние числа продукта (recall, leak) считаются ПО ВХОЖДЕНИЯМ: одно
вхождение — одна единица знаменателя. Для защиты человека это неверный счёт.
Человек защищён, только если замаскированы ВСЕ упоминания его данных: одно
открытое «Петров И. С.» раскрывает [PERSON_7] по всему договору, сколько бы
вхождений ни было закрыто. Восемь закрытых вхождений из девяти — это не 89 %
защиты, это НОЛЬ защиты для этого человека.

ЧТО ЗДЕСЬ СЧИТАЕТСЯ СУЩНОСТЬЮ. Группа эталонных вхождений ОДНОГО документа с
одним типом и одним НОРМАЛИЗОВАННЫМ значением. Нормализация типовая:
  * числовые реквизиты — «ядро» из одних цифр (`v2_core_digits`): вхождение,
    разбитое пробелами или испорченное омоглифами, — то же самое значение;
  * прочие — `v2_norm_text` (регистр вниз, омоглифы к латинице, пробелы
    схлопнуты): «ПЕТРОВ И.С.» и «Петров И.С.» — один человек.
Группировка ВНУТРИ документа, а не по корпусу: защита оценивается по одному
документу, который пользователь отправляет в LLM.

ЧЕГО ЭТОТ СЧЁТ НЕ УМЕЕТ И ЧЕМ ЭТО ЧЕСТНО ОГРАНИЧЕНО. Одинаковые значения — один
человек; РАЗНЫЕ формы одного имени («Петров», «Петрова И. С.», «И. С. Петров»)
считаются разными сущностями, потому что связать их по одной поверхности
нечем. Следствие названо явно: счёт сущностей ЗАВЫШЕН (сущностей больше, чем
людей), а значит доля защищённых сущностей — ОПТИМИСТИЧНАЯ оценка снизу по
строгости: у настоящего человека вхождений больше, чем у его отдельной формы,
и шанс, что все они закрыты, только меньше.

ДВА КРИТЕРИЯ, И ОНИ РАЗНЫЕ:
  «маска»  — все вхождения сущности найдены и замаскированы (`found`);
  «утечка» — ни один фрагмент значения не дожил до анонимного текста
             (`leak_v2`, мягкий порог). Это продуктовый критерий: он про то,
             что реально уходит в LLM. `leak_v2` ищет по ВСЕМУ анонимному
             тексту документа, поэтому по своей природе уже посущностный —
             и именно поэтому разрыв «вхождения против сущностей» на нём мал,
             а на маске велик. Обе цифры печатаются рядом, чтобы разницу было
             видно, а не выбирать из них удобную.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402

#: ПРЯМЫЕ идентификаторы — называют человека сами по себе.
DIRECT = ("PER", "PASSPORT", "INN_PER", "SNILS", "PHONE", "EMAIL", "BIRTHDATE")
#: КОСВЕННЫЕ — сужают круг в связке с другими данными (организация, адрес,
#: суммы, реквизиты юрлица).
INDIRECT = ("ORG", "ADDRESS", "SUM", "INN", "OGRN", "KPP", "ACCOUNT", "BIK")

#: Типы, у которых значение — число (нормализуем в цифровое ядро).
_NUMERIC = {"INN", "INN_PER", "OGRN", "KPP", "ACCOUNT", "BIK", "PHONE",
            "PASSPORT", "SNILS", "BIRTHDATE", "SUM"}


def value_key(etype, text):
    """Ключ ТОЖДЕСТВА значения внутри документа (см. докстринг про нормализацию)."""
    if etype in _NUMERIC:
        core = ML.v2_core_digits(text)
        if core:
            return core
    return ML.v2_norm_text(text)


def person_key(text):
    """ЛЮДИ ПО ФАМИЛИИ — более строгая (и более честная) группировка для PER.

    `value_key` считает «Петров И. С.», «Петрова И.С.» и «И. С. Петров» тремя
    разными сущностями, потому что поверхности разные. Для защиты человека это
    неверно: это один человек, и любое его открытое упоминание раскрывает
    остальные. Здесь ключ — САМЫЙ ДЛИННЫЙ буквенный токен значения: у русского
    ФИО с инициалами это фамилия («петров» из «Петров И. С.»), у полного ФИО —
    как правило тоже фамилия либо отчество, что для группировки внутри одного
    договора почти всегда одно и то же лицо.

    ЧЕСТНАЯ ЦЕНА ПРИЁМА, названа явно: (1) однофамильцы в одном договоре
    сольются в одну сущность — счёт защищённых от этого падает, то есть ошибка
    в СТРОГУЮ сторону; (2) склонение фамилии («петрова» / «петровым») даёт
    разные ключи, то есть часть форм всё-таки разъедется — в этом месте оценка
    остаётся оптимистичной. Поэтому вид печатается ОТДЕЛЬНО и рядом с основным,
    а не подменяет его.
    """
    toks = [t for t in ML.v2_norm_text(text).replace(".", " ").split()
            if t.isalpha() and len(t) >= 3]
    return max(toks, key=len) if toks else ML.v2_norm_text(text)


def group_entities(results, key_fn=None):
    """Дамп прогона -> список сущностей.

    Каждая: {doc_id, type, key, n_occ, masked_all, leak_any, protected, ...}.
    `key_fn(type, text)` — правило тождества (по умолчанию `value_key`).
    Документы с outcome != processed пропускаются (их вхождения не измерены)."""
    key_fn = key_fn or value_key
    groups = {}
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            k = (r["doc_id"], e["type"], key_fn(e["type"], e["text"]))
            g = groups.setdefault(k, {"doc_id": r["doc_id"], "type": e["type"],
                                      "key": k[2], "n_occ": 0, "found_occ": 0,
                                      "leak_occ": 0, "prot_occ": 0})
            g["n_occ"] += 1
            g["found_occ"] += int(e["found"])
            hit6, _hit8 = ML._leak_v2_hits(e["type"], e["leak_v2"])
            g["leak_occ"] += int(hit6)
            g["prot_occ"] += int(bool(e["found"]) and not hit6)
    out = []
    for g in groups.values():
        g["masked_all"] = (g["found_occ"] == g["n_occ"])
        g["leak_any"] = (g["leak_occ"] > 0)
        # ЗАЩИЩЕНА — совокупный критерий: все вхождения закрыты И ни один
        # фрагмент значения не дожил до анонимного текста. Одной маски мало
        # (маска может быть короче значения), одного «не утекло» тоже мало
        # (метрика утечки не видит всех классов) — требуется и то, и другое.
        g["protected"] = g["masked_all"] and not g["leak_any"]
        out.append(g)
    return out


def _pct(a, b):
    return (100.0 * a / b) if b else None


def summarize(results, key_fn=None):
    """Сводка «по вхождениям против по сущностям», по типам и по группам типов."""
    ents = group_entities(results, key_fn)
    per_type = {}
    for g in ents:
        s = per_type.setdefault(g["type"], {
            "ent": 0, "ent_masked": 0, "ent_clean": 0, "ent_prot": 0,
            "occ": 0, "occ_found": 0, "occ_clean": 0, "occ_prot": 0, "multi": 0})
        s["ent"] += 1
        s["ent_masked"] += int(g["masked_all"])
        s["ent_clean"] += int(not g["leak_any"])
        s["ent_prot"] += int(g["protected"])
        s["occ"] += g["n_occ"]
        s["occ_found"] += g["found_occ"]
        s["occ_clean"] += g["n_occ"] - g["leak_occ"]
        s["occ_prot"] += g["prot_occ"]
        s["multi"] += int(g["n_occ"] > 1)
    for s in per_type.values():
        s["occ_recall"] = _pct(s["occ_found"], s["occ"])
        s["ent_recall"] = _pct(s["ent_masked"], s["ent"])
        s["occ_clean_pct"] = _pct(s["occ_clean"], s["occ"])
        s["ent_clean_pct"] = _pct(s["ent_clean"], s["ent"])
        s["occ_prot_pct"] = _pct(s["occ_prot"], s["occ"])
        s["ent_prot_pct"] = _pct(s["ent_prot"], s["ent"])
    return {"per_type": per_type, "n_entities": len(ents),
            "groups": {"ПРЯМЫЕ идентификаторы": DIRECT,
                       "КОСВЕННЫЕ идентификаторы": INDIRECT}}


def _roll(per_type, types):
    agg = {"ent": 0, "ent_masked": 0, "ent_clean": 0, "ent_prot": 0,
           "occ": 0, "occ_found": 0, "occ_clean": 0, "occ_prot": 0, "multi": 0}
    for t in types:
        s = per_type.get(t)
        if not s:
            continue
        for k in agg:
            agg[k] += s[k]
    return agg


def _p(a, b):
    return "—" if not b else "%.2f%%" % _pct(a, b)


def _line(name, s):
    """Строка таблицы: слева счёт по ВХОЖДЕНИЯМ, справа по СУЩНОСТЯМ.
    «защищ» — совокупный критерий (всё закрыто И ничего не утекло)."""
    return ("%-14s %6d %8s %8s %6d %8s %8s"
            % (name,
               s["occ"], _p(s["occ_found"], s["occ"]), _p(s["occ_prot"], s["occ"]),
               s["ent"], _p(s["ent_masked"], s["ent"]), _p(s["ent_prot"], s["ent"])))


def report(results, key_fn=None, title=None):
    sm = summarize(results, key_fn)
    per = sm["per_type"]
    print(title or "МЕТРИКА ПО СУЩНОСТЯМ ПРОТИВ МЕТРИКИ ПО ВХОЖДЕНИЯМ")
    print("Сущность = значение внутри документа; защищена, только если ВСЕ её "
          "вхождения закрыты И ничего не утекло.")
    print()
    print("%-14s %6s %8s %8s %6s %8s %8s"
          % ("тип", "вхожд", "найдено", "защищ", "сущн", "все закр", "защищ"))
    print("-" * 66)
    for grp, types in sm["groups"].items():
        print("[%s]" % grp)
        for t in types:
            if t in per and per[t]["ent"]:
                print(_line("  " + t, per[t]))
        print(_line("  ИТОГО", _roll(per, types)))
        print()
    rest = [t for t in sorted(per) if t not in DIRECT and t not in INDIRECT
            and per[t]["ent"]]
    if rest:
        print("[Прочее (не идентификаторы человека)]")
        for t in rest:
            print(_line("  " + t, per[t]))
        print()
    allt = sorted(per)
    tot = _roll(per, allt)
    print(_line("ВСЕГО", tot))
    print()
    d, i = _roll(per, DIRECT), _roll(per, INDIRECT)
    print("РАЗНИЦА МЕТРИК, процентные пункты (вхождения -> сущности):")
    for name, s in (("прямые", d), ("косвенные", i), ("всего", tot)):
        if not s["occ"] or not s["ent"]:
            continue
        print("  %-10s найдено %.2f%% -> %.2f%% (%+.2f пп);  защищено "
              "%.2f%% -> %.2f%% (%+.2f пп)"
              % (name,
                 _pct(s["occ_found"], s["occ"]), _pct(s["ent_masked"], s["ent"]),
                 _pct(s["ent_masked"], s["ent"]) - _pct(s["occ_found"], s["occ"]),
                 _pct(s["occ_prot"], s["occ"]), _pct(s["ent_prot"], s["ent"]),
                 _pct(s["ent_prot"], s["ent"]) - _pct(s["occ_prot"], s["occ"])))
    print()
    print("  сущностей %d, вхождений %d; более одного вхождения у %d сущностей "
          "— только на них две метрики и могут разойтись."
          % (tot["ent"], tot["occ"], tot["multi"]))
    print("  ВНИМАНИЕ, читать со знаком: разница может выйти И В ПЛЮС. Сущность, "
          "все шесть вхождений которой утекли, в счёте по вхождениям весит 6, "
          "а в счёте по сущностям — 1. Это не улучшение защиты, а смена веса; "
          "ухудшение видно там, где закрыта ЧАСТЬ вхождений.")
    return sm


def report_persons_by_surname(results):
    """Отдельный, БОЛЕЕ СТРОГИЙ вид только по людям: сущность = фамилия.

    Основной вид считает разные поверхности одного человека («Петров И. С.» и
    «И. С. Петров») разными сущностями и потому оптимистичен. Здесь они
    сводятся (`person_key`), и видно, сколько ЛЮДЕЙ защищено целиком.
    """
    def key_fn(etype, text):
        return person_key(text) if etype == "PER" else value_key(etype, text)

    ents = [g for g in group_entities(results, key_fn) if g["type"] == "PER"]
    base = [g for g in group_entities(results) if g["type"] == "PER"]
    n, prot = len(ents), sum(1 for g in ents if g["protected"])
    bn, bprot = len(base), sum(1 for g in base if g["protected"])
    multi = sum(1 for g in ents if g["n_occ"] > 1)
    print("ЛЮДИ, СВЕДЁННЫЕ ПО ФАМИЛИИ (более строгий вид, см. person_key)")
    print("  сущностей: %d по поверхности -> %d по фамилии (более одного "
          "вхождения у %d)" % (bn, n, multi))
    print("  защищено целиком: %s (по поверхности) -> %s (по фамилии)"
          % (_p(bprot, bn), _p(prot, n)))
    print("  то есть у %d человек(а) из %d в документе осталось открытым хотя "
          "бы одно упоминание." % (n - prot, n))
    return {"n": n, "protected": prot, "n_surface": bn, "protected_surface": bprot}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "results_baseline.json")
    print("дамп: %s\n" % path)
    results = json.load(open(path, encoding="utf-8"))
    report(results)
    print()
    report_persons_by_surname(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
