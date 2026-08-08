# -*- coding: utf-8 -*-
"""assemble_types.py — СБОРЩИК ФАБРИКИ ТИПОВ (этап TYPE-FACTORY, сессия 2).

Что делает: собирает корневой `entity_types.yaml` из двух источников —
  * `entity_types.base.yaml` — непереведённые типы, текст переносится ДОСЛОВНО
    (комментарии живут); место переведённого типа помечено строкой-маркером
    `# @TYPEDEF: <ТИП>` — блок фабричного типа вклеивается РОВНО ТУДА, чтобы
    порядок записей (он же порядок детекции) не двигался ни на одну позицию;
  * `tests/corpus_v2/typedefs/<ТИП>.yaml` — файлы-описания типов. Новые типы
    (без маркера в base) дописываются в конец раздела entity_types.

СТЕНА (главный инвариант). Секция `generator:` файла-описания — примеры
значений, оси, негативы — НЕ ПОПАДАЕТ в собранный конфиг ПО ПОСТРОЕНИЮ:
в артефакт переносятся только ключи из белого списка `_DETECT_KEYS`
(перенос разрешённого, а не вычёркивание запрещённого). После сборки каждый
пример и каждый текст негатива ищется подстрокой в готовом артефакте —
найден хоть один, сборка падает. Детектор (`src/`) читает только собранный
файл; смотреть сюда ему запрещает AST-страж (tests/test_type_factory.py).

ВАЛИДАТОР. Неполное описание роняет сборку с человеческим текстом ошибки —
см. `validate_typedef`. Пороги решения владельца: >=5 примеров, >=3 разных
вида (kind) негативов.

Запуск:
    venv/Scripts/python.exe tests/corpus_v2/assemble_types.py           # собрать
    venv/Scripts/python.exe tests/corpus_v2/assemble_types.py --check   # сверить без записи
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BASE = os.path.join(ROOT, "entity_types.base.yaml")
OUT = os.path.join(ROOT, "entity_types.yaml")
TYPEDEFS = os.path.join(HERE, "typedefs")

MARKER = "# @TYPEDEF: "

# --------------------------------------------------------------------------- #
# Порядок арбитража ДО фабрики (src/tokenizer.py на момент шага 0) — ранги
# базовых типов. Ранг = (позиция+1)*100; фабричный тип встаёт между ними своим
# rank. Списки здесь — ИСТОРИЧЕСКАЯ ТОЧКА ОТСЧЁТА, менять их нельзя: тест
# test_type_factory.py::test_arbitration_order_preserved сверяет итоговый
# порядок с порядком до фабрики.
# --------------------------------------------------------------------------- #
BASE_REGEX_ORDER = [
    "BANK_ACCOUNT", "OGRN", "INN", "INN_PERSON", "BIK", "SNILS", "KPP",
    "PASSPORT", "PHONE", "SUM", "EMAIL", "BIRTHDATE", "DATE",
    "PERCENT", "TERM",
]
BASE_NER_ORDER = ["ADDRESS", "ORG", "PERSON"]

#: Типы, которым разрешён `generator: legacy` — их формы исторически живут в
#: values.py/generate.py и НЕ переводятся (перенос обязан был бы воспроизвести
#: корпус v2 байт-в-байт — риск без выигрыша, дизайн §3). Список ЗАКРЫТ:
#: новый тип обязан принести полную generator-секцию.
LEGACY_GENERATOR_OK = set(BASE_REGEX_ORDER)

#: Наборы, которые знает type_policy (кроме «Максимума» — он сентинел).
KNOWN_SETS = ("personal", "personal_requisites", "with_money")

_TOP_KEYS = {"type", "title", "status", "detect", "generator", "notes"}
#: Белый список detect-ключей — ЕДИНСТВЕННОЕ, что переносится в артефакт
#: (сама стена). form/rank/sets/measure потребляются сборщиком.
_DETECT_KEYS = {"method", "form", "patterns", "validate", "anchor", "anti_anchor",
                "anti_anchor_window", "anti_anchor_right",
                "anti_anchor_right_window", "span_group", "enabled",
                "token_prefix", "rank", "sets", "sets_why", "measure"}
_PATTERN_ENTRY_KEYS = {"pattern", "form", "span_group", "validate", "anchor",
                       "anti_anchor", "anti_anchor_window",
                       "anti_anchor_right", "anti_anchor_right_window"}
_FORM_CLASSES = ("raw", "digit_run", "number_with_unit", "compound")


class TypedefError(SystemExit):
    """Ошибка описания типа: текст — человеку, код возврата 1."""

    def __init__(self, msg):
        super().__init__("ОШИБКА ФАБРИКИ ТИПОВ\n" + msg)


# --------------------------------------------------------------------------- #
# Классы форм: параметры -> строка паттерна
# --------------------------------------------------------------------------- #

def compile_form(t, form):
    """form-словарь -> строка regex. Классы: raw (паттерн дословно),
    digit_run (прогон цифр с одиночным разделителем [ ], класс B2-fix)."""
    if not isinstance(form, dict) or "class" not in form:
        raise TypedefError(
            f"{t}: у form нет class. Разрешены: {', '.join(_FORM_CLASSES)}.")
    cls = form["class"]
    if cls == "raw":
        pat = form.get("pattern")
        if not pat:
            raise TypedefError(
                f"{t}: класс raw требует pattern — сам регэксп дословно.")
        return pat
    if cls == "digit_run":
        p = form.get("params") or {}
        digits = p.get("digits")
        if not isinstance(digits, int) or digits < 2:
            raise TypedefError(
                f"{t}: digit_run требует params.digits (целое >=2) — сколько "
                f"всего цифр в значении.")
        prefix = str(p.get("prefix", "") or "")
        extra = int(p.get("optional_extra", 0) or 0)
        # Разделитель — ЯВНО пробел + неразрывный пробел ( ), класс B2-fix.
        # Не буквами в исходнике: невидимый символ в литерале — это дефект,
        # который уже был пойман живым расхождением (KPP, шаг 4 сессии 2).
        sep = "[  ]?"
        if prefix:
            if not prefix.isdigit() or len(prefix) >= digits:
                raise TypedefError(
                    f"{t}: digit_run prefix обязан быть цифрами короче digits.")
            head = prefix[0] + "".join(sep + c for c in prefix[1:])
            rest = digits - len(prefix)
        else:
            head = r"\d"
            rest = digits - 1
        pat = r"\b" + head + r"(?:%s\d){%d}" % (sep, rest)
        if extra:
            pat += r"(?:(?:%s\d){%d})?" % (sep, extra)
        return pat + r"\b"
    if cls == "number_with_unit":
        # КЛАСС К4 — «число + слово закрытого списка» (ЭТАП FIX-3).
        # Слоты идут ПОСЛЕДОВАТЕЛЬНО и все, кроме number/units, необязательны.
        # Смысл класса не в экономии знаков, а в том, что у каждого куска
        # появляется ИМЯ: «где здесь единица измерения» видно без чтения
        # регэкспа, и новый тип заполняет слоты, а не сочиняет форму заново.
        p = form.get("params") or {}
        if not p.get("number") or not p.get("units"):
            raise TypedefError(
                f"{t}: number_with_unit требует params.number (форма числа) и "
                f"params.units (закрытый список единиц). Единица — это то, что "
                f"делает число величиной; без неё класс не про что.")
        parts = [p.get("flags", "(?i)"), p.get("left_boundary", ""),
                 p["number"], p.get("decimals", ""), p.get("parenthetical", ""),
                 p.get("gap", ""), p["units"], p.get("suffix", ""),
                 p.get("right_boundary", "")]
        return "".join(x for x in parts if x)
    if cls == "compound":
        # КЛАСС К5 — «составная конструкция»: несколько ВЗАИМНО ИСКЛЮЧАЮЩИХ
        # ветвей формы плюс необязательный хвост-основание. Ровно эта форма у
        # процента («число+%» ИЛИ «доля ключевой ставки») и у срока
        # («в течение …» ИЛИ «не позднее …»), и ровно она чаще всего нужна
        # новому типу из живого договора.
        p = form.get("params") or {}
        branches = p.get("branches") or []
        if len(branches) < 1:
            raise TypedefError(
                f"{t}: compound требует params.branches — хотя бы одну ветвь "
                f"формы. Ветвь одна и хвоста нет — это не составная "
                f"конструкция, берите raw.")
        out = p.get("flags", "(?i)") + p.get("left_boundary", "")
        out += "(?:" + "|".join(branches) + ")"
        tail = p.get("tail") or []
        if tail:
            out += "(?:" + "|".join(tail) + ")?"
        return out + p.get("right_boundary", "")
    raise TypedefError(
        f"{t}: неизвестный класс формы «{cls}». Разрешены: "
        f"{', '.join(_FORM_CLASSES)}. Не ложится ни в один — берите raw "
        f"и приведите паттерн дословно.")


# --------------------------------------------------------------------------- #
# Валидатор
# --------------------------------------------------------------------------- #

def validate_typedef(td, fname):
    t = td.get("type")
    if not t or not isinstance(t, str):
        raise TypedefError(f"{fname}: нет поля type — как тип называется?")
    if os.path.splitext(os.path.basename(fname))[0] != t:
        raise TypedefError(
            f"{fname}: имя файла обязано совпадать с type ({t}) — один файл, "
            f"один тип, находится по имени.")
    unknown = set(td) - _TOP_KEYS
    if unknown:
        raise TypedefError(
            f"{t}: неизвестные ключи верхнего уровня: {sorted(unknown)}. "
            f"Разрешены: {sorted(_TOP_KEYS)}. Опечатка не должна молчать.")
    if not td.get("title"):
        raise TypedefError(
            f"{t}: нет title — под каким именем тип показывать человеку "
            f"(отчёт, экран «Что маскировать»).")
    status = td.get("status")
    if status not in ("active", "corpus_only"):
        raise TypedefError(
            f"{t}: status обязан быть active (детектируется и маскируется) "
            f"или corpus_only (только эталон, детектора нет).")

    detect = td.get("detect")
    if status == "corpus_only":
        if detect:
            raise TypedefError(
                f"{t}: status corpus_only, но есть секция detect. Тип без "
                f"измеримого эталона детектора не получает — либо снимите "
                f"detect, либо переведите в active решением владельца.")
    else:
        _validate_detect(t, detect)

    gen = td.get("generator")
    if gen == "legacy":
        if t not in LEGACY_GENERATOR_OK:
            raise TypedefError(
                f"{t}: generator: legacy разрешён только 15 дофабричным типам "
                f"(их формы исторически в values.py). Новый тип обязан принести "
                f"полную generator-секцию: examples, negatives, provenance.")
    elif isinstance(gen, dict):
        _validate_generator(t, gen)
    else:
        raise TypedefError(
            f"{t}: нет секции generator. Без примеров значений тип не попадает "
            f"в корпус и не измеряется вовсе — это молчаливая слепота, а не "
            f"экономия. Нужны examples (>=5) и negatives (>=3 видов).")


def _validate_detect(t, detect):
    if not isinstance(detect, dict):
        raise TypedefError(f"{t}: нет секции detect у active-типа.")
    unknown = set(detect) - _DETECT_KEYS
    if unknown:
        raise TypedefError(
            f"{t}: неизвестные ключи detect: {sorted(unknown)}. Разрешены: "
            f"{sorted(_DETECT_KEYS)}. Белый список — это и есть стена: "
            f"лишний ключ не должен молча утечь в артефакт.")
    if detect.get("method", "regex") != "regex":
        raise TypedefError(
            f"{t}: фабрика собирает только method: regex. Якорная модель "
            f"(PERSON/ORG/ADDRESS) — код, не конфиг; ей фабрика не занимается "
            f"(дизайн §6).")
    has_form = "form" in detect
    has_patterns = "patterns" in detect
    if has_form == has_patterns:
        raise TypedefError(
            f"{t}: нужно РОВНО ОДНО из form (один паттерн через класс) или "
            f"patterns (список записей, у каждой свой form).")
    if has_patterns:
        for i, entry in enumerate(detect["patterns"]):
            unknown = set(entry) - _PATTERN_ENTRY_KEYS
            if unknown:
                raise TypedefError(
                    f"{t}: patterns[{i}]: неизвестные ключи {sorted(unknown)}.")
            if "form" not in entry:
                raise TypedefError(f"{t}: patterns[{i}]: нет form.")
            compile_form(t, entry["form"])
    else:
        compile_form(t, detect["form"])
    if not detect.get("token_prefix"):
        raise TypedefError(
            f"{t}: нечем маскировать — нет token_prefix. Либо задайте, либо "
            f"объявите тип corpus_only.")
    rank = detect.get("rank")
    if not isinstance(rank, int):
        raise TypedefError(
            f"{t}: нет rank — места в арбитраже пересечений. Тип без места "
            f"молча проигрывает каждое пересечение (это уже случалось до "
            f"T-ARB). Базовые ранги: "
            + ", ".join(f"{n}={(i + 1) * 100}" for i, n in enumerate(BASE_REGEX_ORDER)))
    sets_ = detect.get("sets")
    if sets_ is None:
        raise TypedefError(
            f"{t}: не сказано, в какие наборы входит тип (sets). Без этого он "
            f"маскируется только в «Максимуме» — если так задумано, напишите "
            f"sets: [] и sets_why с обоснованием.")
    if not isinstance(sets_, list) or any(s not in KNOWN_SETS for s in sets_):
        raise TypedefError(
            f"{t}: sets — список из {KNOWN_SETS} (Максимум — сентинел, его не "
            f"перечисляют).")
    if sets_ == [] and not detect.get("sets_why"):
        raise TypedefError(
            f"{t}: sets пуст, а sets_why нет. Пустые наборы — решение, у "
            f"решения должно быть записано «почему».")
    measure = detect.get("measure")
    if not isinstance(measure, dict) or measure.get("leak_class") not in ("numeric", "text"):
        raise TypedefError(
            f"{t}: нет measure.leak_class (numeric|text) — какой веткой мерить "
            f"утечку. Молчаливое выпадение типа из метрики уже случалось "
            f"(BIRTHDATE, этап 0b-fix).")


def _validate_generator(t, gen):
    unknown = set(gen) - {"provenance", "examples", "axes", "negatives", "weave"}
    if unknown:
        raise TypedefError(f"{t}: неизвестные ключи generator: {sorted(unknown)}.")
    if not gen.get("provenance"):
        raise TypedefError(
            f"{t}: нет provenance — откуда примеры. Правило владельца: "
            f"формулировки из живых договоров, и происхождение записывается.")
    examples = gen.get("examples") or []
    if len(examples) < 5:
        raise TypedefError(
            f"{t}: примеров значений {len(examples)}, нужно >=5. Генератору не "
            f"из чего строить формы, метрика по типу будет о "
            f"{len(examples)} строках.")
    negatives = gen.get("negatives") or []
    kinds = {n.get("kind") for n in negatives if isinstance(n, dict)}
    kinds.discard(None)
    if len(kinds) < 3:
        raise TypedefError(
            f"{t}: видов негативов {len(kinds)}, нужно >=3 разных kind. Тип без "
            f"негативов не охраняется от переусердствования: один вид случая = "
            f"одна дыра, повторённая 138 раз (урок NEG).")
    for n in negatives:
        if not isinstance(n, dict) or not n.get("text") or not n.get("why"):
            raise TypedefError(
                f"{t}: каждый негатив — {{kind, text, why}}; без why не "
                f"проверить, что случай осмыслен, а не совпал.")


# --------------------------------------------------------------------------- #
# Эмиссия YAML-блока типа (текстом, чтобы notes стали комментариями)
# --------------------------------------------------------------------------- #

def _q(s):
    """Строка -> одинарно-квотированный YAML-скаляр."""
    return "'" + str(s).replace("'", "''") + "'"


def _emit_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return _q(v)


def emit_type_block(td):
    """Файл-описание -> текст записи для entity_types (с отступом 2)."""
    t = td["type"]
    detect = td.get("detect") or {}
    lines = []
    for note_line in (td.get("notes") or "").rstrip().splitlines():
        lines.append(("  # " + note_line).rstrip())
    lines.append(f"  # [собрано фабрикой из tests/corpus_v2/typedefs/{t}.yaml"
                 f" — правки только там]")
    lines.append(f"  {t}:")
    if not detect:  # corpus_only: записи в конфиге нет вовсе
        return ""
    lines.append("    method: regex")
    if "patterns" in detect:
        lines.append("    patterns:")
        for entry in detect["patterns"]:
            lines.append("      - pattern: " + _q(compile_form(t, entry["form"])))
            for k in ("span_group", "validate", "anchor", "anti_anchor",
                      "anti_anchor_window", "anti_anchor_right",
                      "anti_anchor_right_window"):
                if k in entry:
                    lines.append(f"        {k}: " + _emit_value(entry[k]))
    else:
        lines.append("    pattern: " + _q(compile_form(t, detect["form"])))
        for k in ("validate", "anchor", "anti_anchor", "anti_anchor_window",
                  "anti_anchor_right", "anti_anchor_right_window",
                  "span_group"):
            if k in detect:
                lines.append(f"    {k}: " + _emit_value(detect[k]))
    if "enabled" in detect:
        lines.append("    enabled: " + _emit_value(detect["enabled"]))
    lines.append("    token_prefix: " + _emit_value(detect["token_prefix"]))
    sets_ = detect.get("sets", [])
    lines.append("    sets: [" + ", ".join(sets_) + "]")
    lines.append("    measure: {leak_class: %s}" % detect["measure"]["leak_class"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Сборка
# --------------------------------------------------------------------------- #

HEADER = (
    "# ФАЙЛ СОБРАН tests/corpus_v2/assemble_types.py — НЕ ПРАВИТЬ РУКАМИ:\n"
    "# правку сотрёт следующая сборка. Источники: entity_types.base.yaml\n"
    "# (непереведённые типы, текст дословно) + tests/corpus_v2/typedefs/*.yaml\n"
    "# (фабричные типы). Сверка: assemble_types.py --check; страж —\n"
    "# tests/test_type_factory.py.\n"
)


def load_typedefs():
    tds = {}
    if not os.path.isdir(TYPEDEFS):
        return tds
    for fn in sorted(os.listdir(TYPEDEFS)):
        if not fn.endswith(".yaml"):
            continue
        path = os.path.join(TYPEDEFS, fn)
        with open(path, encoding="utf-8") as f:
            td = yaml.safe_load(f)
        validate_typedef(td, path)
        tds[td["type"]] = td
    return tds


def build_arbitration(tds):
    """Итоговый порядок арбитража: базовые ранги (позиция+1)*100 + rank
    фабричных типов. Переведённый базовый тип обязан нести СВОЙ исторический
    ранг — расхождение роняет сборку (поведение не должно двигаться молча)."""
    ranked = {n: (i + 1) * 100 for i, n in enumerate(BASE_REGEX_ORDER)}
    for t, td in tds.items():
        detect = td.get("detect")
        if not detect:
            continue
        rank = detect["rank"]
        if t in ranked and rank != ranked[t]:
            raise TypedefError(
                f"{t}: rank {rank} != исторического {ranked[t]}. Перевод типа "
                f"не двигает его место в арбитраже; сдвиг — отдельное решение "
                f"владельца, не побочный эффект переезда.")
        if rank in {v for k, v in ranked.items() if k != t}:
            other = [k for k, v in ranked.items() if v == rank and k != t][0]
            raise TypedefError(
                f"{t}: rank {rank} уже занят типом {other}. Одно место — один "
                f"тип, иначе порядок недетерминирован.")
        ranked[t] = rank
    regex_order = [t for t, _ in sorted(ranked.items(), key=lambda kv: (kv[1], kv[0]))]
    return regex_order, list(BASE_NER_ORDER)


def assemble():
    with open(BASE, encoding="utf-8") as f:
        base_text = f.read()
    tds = load_typedefs()

    out_lines = []
    seen_markers = set()
    for line in base_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(MARKER):
            t = stripped[len(MARKER):].strip()
            seen_markers.add(t)
            if t not in tds:
                raise TypedefError(
                    f"в base стоит маркер «{MARKER}{t}», а файла "
                    f"typedefs/{t}.yaml нет. Маркер без файла = дыра на месте "
                    f"типа.")
            block = emit_type_block(tds[t])
            if block:
                out_lines.append(block.rstrip("\n"))
            continue
        out_lines.append(line)

    # новые типы (без маркера) — в конец раздела entity_types
    for t in sorted(tds):
        if t in seen_markers:
            continue
        block = emit_type_block(tds[t])
        if block:
            out_lines.append("")
            out_lines.append(block.rstrip("\n"))

    regex_order, ner_order = build_arbitration(tds)
    out_lines.append("")
    out_lines.append("# Порядок арбитража пересечений (rule 3/rule 4 tokenizer._winner).")
    out_lines.append("# СОБРАН из базового порядка (до фабрики) и rank фабричных типов.")
    out_lines.append("arbitration_order:")
    out_lines.append("  regex: [" + ", ".join(regex_order) + "]")
    out_lines.append("  ner: [" + ", ".join(ner_order) + "]")

    text = HEADER + "\n".join(out_lines).rstrip("\n") + "\n"

    # --- СТЕНА, самопроверка: ни одно значение генератора не в артефакте --- #
    for t, td in tds.items():
        gen = td.get("generator")
        if not isinstance(gen, dict):
            continue
        probes = list(gen.get("examples") or [])
        probes += [n.get("text", "") for n in (gen.get("negatives") or [])]
        for p in probes:
            if p and str(p) in text:
                raise TypedefError(
                    f"СТЕНА: значение генератора «{p}» ({t}) попало в собранный "
                    f"entity_types.yaml. Сборка остановлена — детектор не "
                    f"должен видеть примеры.")

    # разбираемость и структурная целостность
    parsed = yaml.safe_load(text)
    for t, td in tds.items():
        if td.get("detect") and t not in parsed["entity_types"]:
            raise TypedefError(f"{t}: после сборки типа нет в entity_types — "
                               f"маркер потерян?")
    return text


def main(argv):
    check = "--check" in argv
    text = assemble()
    if check:
        with open(OUT, encoding="utf-8") as f:
            current = f.read()
        if current != text:
            print("entity_types.yaml РАСХОДИТСЯ со сборкой — файл правили руками "
                  "или забыли пересобрать. Запустите assemble_types.py.",
                  file=sys.stderr)
            return 1
        print("entity_types.yaml совпадает со сборкой.")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"собрано: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
