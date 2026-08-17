# -*- coding: utf-8 -*-
"""noise_lib.py — ОСЬ ИСКАЖЕНИЙ РАСПОЗНАВАНИЯ (сессия NOISE-CORPUS).

ЗАЧЕМ. Живой пользователь принёс договор, вытащенный из PDF чужим
распознаванием: в дате стояло «2()23», в сумме — «500 ()()()». В обоих наших
корпусах таких вхождений НОЛЬ. Класс порчи, который реально доезжает до
программы от живых людей, не измерялся ничем.

ЧТО ЭТО. Прибор, а не лечение. Файлы-описания `noise/classes/*.yaml` объявляют
КЛАССЫ ИСКАЖЕНИЙ (что распознавание делает с текстом), этот модуль их читает и
умеет применить к модели документа; `generate_noise.py` порождает пары
«чистый документ — тот же документ, испорченный распознаванием», а
`measure_noise.py` считает разницу.

ГРАНИЦА, КОТОРУЮ НЕЛЬЗЯ ПЕРЕХОДИТЬ. Класс описывает ПОВЕДЕНИЕ РАСПОЗНАВАНИЯ, а
не то, что умеет починить нормализатор. Правило «пустая скобка = ноль» — это
гипотеза лечения; если бы корпус состоял только из случаев, которые она чинит,
прибор мерил бы лекарство само на себе. Поэтому в описании каждого класса есть
поле `evidence`: `observed-user` — видели у пользователя, `inferred` — вывод из
устройства распознавания, наблюдения в этом проекте нет. Смешивать нельзя.

ЭТАЛОН НЕ ИСКАЖАЕТСЯ. Портится только текст документа; правильное значение
уезжает в разметку полем `clean`, а координаты, как и везде в корпусе v2,
ПЕРЕСЧИТЫВАЮТСЯ сериализатором из модели (`corpus_lib.serialize`) — соврать в
них технически невозможно, их никто не хранит.

Стена в силе: отсюда ничего не импортируется в `src/`.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES_DIR = os.path.join(HERE, "noise", "classes")

#: Скобочная пара на месте нуля — наблюдение пользователя. Вариантов больше
#: одного намеренно: в одном документе один и тот же ноль выглядел и как «()»,
#: и как «( )».
_EVIDENCE = ("observed-user", "inferred")


# --------------------------------------------------------------------------- #
#                       ПРИЁМЫ НАД ОДНОЙ ВЕЛИЧИНОЙ (scope: value)              #
# --------------------------------------------------------------------------- #
def op_char_map(s, rnd, p):
    """Символ -> один из вариантов замены. `map` — {символ: [варианты]}."""
    m = p["map"]
    limit = int(p.get("max_hits", 3))
    idx = [i for i, c in enumerate(s) if c in m]
    if not idx:
        return s
    rnd.shuffle(idx)
    hit = sorted(idx[:limit])
    out, prev = [], 0
    for i in hit:
        out.append(s[prev:i])
        out.append(rnd.choice(m[s[i]]))
        prev = i + 1
    out.append(s[prev:])
    return "".join(out)


def op_insert(s, rnd, p):
    """Вставка внутрь цепочки одинакового класса символов (цифры / буквы)."""
    what = p.get("what", "digit")
    ins = p.get("insert", [" "])
    limit = int(p.get("max_hits", 2))
    test = (lambda c: c.isdigit()) if what == "digit" else (lambda c: c.isalpha())
    pos = [i for i in range(1, len(s)) if test(s[i]) and test(s[i - 1])]
    if not pos:
        return s
    rnd.shuffle(pos)
    hit = sorted(pos[:limit])
    out, prev = [], 0
    for i in hit:
        out.append(s[prev:i])
        out.append(rnd.choice(ins))
        prev = i
    out.append(s[prev:])
    return "".join(out)


def op_squeeze(s, rnd, p):
    """Потеря пробелов ВНУТРИ величины: «500 000» -> «500000»."""
    for sep in p.get("drop", [" ", " "]):
        s = s.replace(sep, "")
    return s


def op_drop_char(s, rnd, p):
    """Потеря символа. `what`: digit | alpha | any."""
    what = p.get("what", "digit")
    test = {"digit": str.isdigit, "alpha": str.isalpha}.get(what, lambda c: not c.isspace())
    idx = [i for i, c in enumerate(s) if test(c)]
    if len(idx) < int(p.get("keep_min", 3)):
        return s
    i = rnd.choice(idx[1:-1] or idx)
    return s[:i] + s[i + 1:]


def op_split(s, rnd, p):
    """Разрыв величины: перенос строки, дефис переноса, разрыв в середине."""
    marks = p.get("mark", ["\n"])
    if len(s) < 4:
        return s
    lo, hi = 2, len(s) - 2
    if hi <= lo:
        return s
    i = rnd.randrange(lo, hi)
    return s[:i] + rnd.choice(marks) + s[i:]


def op_sep_swap(s, rnd, p):
    """Сломанный разделитель даты/суммы: точка -> запятая, точка -> пробел и т.п.

    От `char_map` отличается тем, что бьёт ТОЛЬКО по разделителям и меняет их
    ВСЕ или одну — как решит `mode`; распознавание одинаково часто делает и то,
    и другое (единый шум по всей строке против случайной кляксы).
    """
    m = p["map"]
    idx = [i for i, c in enumerate(s) if c in m]
    if not idx:
        return s
    hit = idx if p.get("mode") == "all" else [rnd.choice(idx)]
    out = list(s)
    for i in hit:
        out[i] = rnd.choice(m[s[i]])
    return "".join(out)


VALUE_OPS = {
    "char_map": op_char_map,
    "insert": op_insert,
    "squeeze": op_squeeze,
    "drop_char": op_drop_char,
    "split": op_split,
    "sep_swap": op_sep_swap,
}

# --------------------------------------------------------------------------- #
#         ПРИЁМЫ НАД ОКРУЖЕНИЕМ (scope: context) — их применяет генератор       #
# --------------------------------------------------------------------------- #
#: Эти классы не переписывают величину: они ломают ТО, ЧТО ВОКРУГ неё. Ключи —
#: имена, которые generate_noise.py знает по построению; реализация там же,
#: потому что приёму нужен абзац и соседние чанки, а не строка.
CONTEXT_OPS = ("glue_neighbours", "label_glue", "column_reorder")


def load_noise_classes():
    """[{id, title, evidence, why, targets, scope, op, params, examples}] —
    в алфавитном порядке имён файлов (детерминизм корпуса)."""
    out = []
    for fn in sorted(os.listdir(CLASSES_DIR)):
        if not fn.endswith(".yaml"):
            continue
        with open(os.path.join(CLASSES_DIR, fn), encoding="utf-8") as f:
            c = yaml.safe_load(f)
        for key in ("id", "title", "evidence", "why", "scope", "op"):
            if not c.get(key):
                raise ValueError("%s: нет обязательного поля %r" % (fn, key))
        if c["evidence"] not in _EVIDENCE:
            raise ValueError("%s: evidence=%r, ожидалось одно из %s"
                             % (fn, c["evidence"], _EVIDENCE))
        if c["scope"] == "value":
            if c["op"] not in VALUE_OPS:
                raise ValueError("%s: приём %r не реализован" % (fn, c["op"]))
        elif c["scope"] == "context":
            if c["op"] not in CONTEXT_OPS:
                raise ValueError("%s: приём окружения %r не реализован" % (fn, c["op"]))
        else:
            raise ValueError("%s: scope=%r" % (fn, c["scope"]))
        c.setdefault("params", {})
        c.setdefault("targets", ["any"])
        c.setdefault("examples", [])
        out.append(c)
    return out


def applies_to(cls, text):
    """Класс применим к значению? `targets`: digits | letters | any."""
    t = cls["targets"]
    if "any" in t:
        return True
    if "digits" in t and any(c.isdigit() for c in text):
        return True
    if "letters" in t and any(c.isalpha() and not c.isdigit() for c in text):
        return True
    return False


def apply_value(cls, s, rnd):
    """Испортить величину по классу. Возвращает новую строку (может совпасть с
    исходной — тогда генератор возьмёт следующий по кругу класс)."""
    return VALUE_OPS[cls["op"]](s, rnd, cls.get("params") or {})
