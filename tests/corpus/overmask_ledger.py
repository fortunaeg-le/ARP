# -*- coding: utf-8 -*-
"""
overmask_ledger.py — ЖУРНАЛ ОБОСНОВАНИЙ линии «д» (порча документа).

ЗАЧЕМ. Линия «д» блокирующая по решению владельца: красная линия на ожидаемом
расширении маскирования полезнее молчания — видно, что надо посмотреть. Но у
блокирующей линии с нулевым допуском есть известный способ выродиться: «она
опять красная, поднимем планку и поедем». Один раз подняли молча — и линия
превращается в счётчик, который догоняет любое ухудшение.

ПОЭТОМУ УРОВЕНЬ ЛИНИИ «д» ЖИВЁТ НЕ ТОЛЬКО В ДАМПЕ. Рядом с числом лежит строка
обоснования: кем, когда, почему поднято и чем подтверждена законность роста.
Три правила, каждое проверяется кодом, а не договорённостью:

  1. ДВИЖЕНИЕ ТРЕБУЕТ ОБОСНОВАНИЯ. Пересобрать точку отсчёта, если число
     выросло, можно только через `promote_baseline.py` с явным `--reason` и
     `--author`; без них инструмент отказывается работать.
  2. ИСТОРИЯ НАКАПЛИВАЕТСЯ. Записи только ДОПИСЫВАЮТСЯ. Прошлые уровни и их
     обоснования остаются видны — движение планки читается как история, а не
     как текущее число неизвестного происхождения.
  3. ОБХОД ВИДЕН. Гейт сверяет числа ТОЧКИ ОТСЧЁТА с последней записью журнала.
     Правка `results_baseline.json` руками (мимо промотера) даёт РАСХОЖДЕНИЕ и
     красный гейт с прямой формулировкой «планка поднята без обоснования».
     Сам журнал — в `MANIFEST.sha256`, поэтому переписать заодно и его тоже не
     получится незаметно.

Журнал — `overmask_ledger.json`, читается гейтом и промотером; руками его
править не нужно (и видно, если правили).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "overmask_ledger.json")

#: Минимальная длина обоснования. Не «защита от дурака», а защита от «ок»:
#: обоснование, которое нельзя прочитать через полгода, не является
#: обоснованием. Порог намеренно низкий — он отсекает пустышку, а не заставляет
#: писать сочинение.
MIN_REASON_LEN = 40

#: Что считаем пустышкой независимо от длины.
_PLACEHOLDERS = ("ок", "ok", "fix", "надо", "потом", "тест", "test", "-", "—", "...")


def load(path=LEDGER):
    if not os.path.isfile(path):
        return {"entries": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def entries(path=LEDGER):
    return load(path).get("entries", [])


def latest(path=LEDGER):
    """Последняя запись журнала или None (журнала нет / он пуст)."""
    e = entries(path)
    return e[-1] if e else None


def overmask_numbers(prec):
    """Числа линии «д» из measure_lib.precision_by_type(): {"total", "per_type"}.

    per_type содержит ТОЛЬКО ненулевые типы: нулевые в журнале — шум, из-за
    которого запись выглядела бы «изменившейся» при появлении нового типа."""
    per = {t: d.get("nothing", 0) for t, d in prec["per_type"].items() if d.get("nothing", 0)}
    return {"total": prec["nothing_total"], "per_type": per}


def validate_reason(reason, author):
    """Возвращает список претензий (пустой = обоснование принято)."""
    problems = []
    if not author or not author.strip():
        problems.append("не указан автор (--author): решение обязано быть чьим-то")
    r = (reason or "").strip()
    if not r:
        problems.append("не указано обоснование (--reason)")
    else:
        if len(r) < MIN_REASON_LEN:
            problems.append(
                "обоснование короче %d символов (%d) — оно должно объяснять причину "
                "роста тому, кто прочитает его через полгода" % (MIN_REASON_LEN, len(r)))
        if r.lower().strip(" .!") in _PLACEHOLDERS:
            problems.append("обоснование — заглушка «%s», а не причина" % r)
    return problems


def append(numbers, *, date, author, reason, evidence, kind, path=LEDGER):
    """ДОПИСЫВАЕТ запись. Существующие записи не трогает и не переписывает —
    единственная операция записи, которая есть у этого модуля вообще."""
    data = load(path)
    data.setdefault("entries", []).append({
        "date": date,
        "author": author,
        "kind": kind,
        "total": numbers["total"],
        "per_type": dict(sorted(numbers["per_type"].items())),
        "reason": reason,
        "evidence": evidence,
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return data["entries"][-1]


def check_against_baseline(baseline_numbers, path=LEDGER, _latest=None):
    """Сверка ТОЧКИ ОТСЧЁТА с последней записью журнала.

    Возвращает список претензий (пустой = сходится). Это ловушка на обход:
    поднять планку правкой results_baseline.json руками можно, но молча —
    нельзя, потому что журнал останется на прежнем числе."""
    last = _latest if _latest is not None else latest(path)
    if last is None:
        return ["журнал обоснований линии «д» пуст или отсутствует (%s): уровень "
                "порчи документа ничем не объяснён" % os.path.basename(path)]
    problems = []
    if last["total"] != baseline_numbers["total"]:
        problems.append(
            "уровень в точке отсчёта (%d) не совпадает с последней записью журнала "
            "(%d, %s, %s). Планка поднята мимо promote_baseline.py, то есть БЕЗ "
            "обоснования" % (baseline_numbers["total"], last["total"],
                             last["date"], last["author"]))
    if dict(last["per_type"]) != dict(baseline_numbers["per_type"]):
        problems.append(
            "разбивка по типам в точке отсчёта %s не совпадает с журналом %s — "
            "состав порчи изменён без записи"
            % (dict(sorted(baseline_numbers["per_type"].items())),
               dict(sorted(last["per_type"].items()))))
    return problems


def grown_types(base_numbers, cur_numbers):
    """{тип: (было, стало)} по типам, где порча ВЫРОСЛА, плюс ключ '(всего)'."""
    out = {}
    types = set(base_numbers["per_type"]) | set(cur_numbers["per_type"])
    for t in sorted(types):
        b = base_numbers["per_type"].get(t, 0)
        c = cur_numbers["per_type"].get(t, 0)
        if c > b:
            out[t] = (b, c)
    if cur_numbers["total"] > base_numbers["total"]:
        out["(всего)"] = (base_numbers["total"], cur_numbers["total"])
    return out
