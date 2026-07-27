# -*- coding: utf-8 -*-
"""
subset_lib.py — ЕДИНЫЙ источник состава итерационного среза.

Один файл со списком `doc_id` (`subset_iter.json`) и один способ его прочитать.
До этого этапа четыре инструмента строили состав четырьмя разными способами
(docs/PERF_REPORT.md §4.1), и ни один набор не совпадал ни с одним.

Кто что читает:
  * `subsample.py`   — читает СПИСОК ВСЕГДА (состав больше не вычисляется);
  * `etalon.py`      — по флагу `--subset` (по умолчанию поведение прежнее);
  * `acceptance.py`  — по флагу `--subset` (по умолчанию поведение прежнее);
  * `gate.py`        — НЕ читает и не должен: он гоняет весь корпус и остаётся
                       единственной дорогой к коммиту в `src/`.

Универсум страт и процедура отбора — в `build_subset_iter.py`; здесь только
чтение и общие для всех инструментов вычисления покрытия (чтобы тест
`tests/test_subset_iter_coverage.py` и построитель считали классы ОДНИМ кодом,
а не двумя похожими).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SUBSET_PATH = os.path.join(HERE, "subset_iter.json")
STRUCTURE_PATH = os.path.join(HERE, "structure.json")
GOLD_PATH = os.path.join(HERE, "gold.json")

# Документы, явно названные в docs/FINDINGS.md как редкие вскрытые дефекты
# (Stage4-B, PER-B). Исторически именно они выпадали из точечной проверки,
# поэтому включаются в срез принудительно, вне зависимости от того, добавляет
# ли документ новый класс покрытия. Список перенесён сюда из subsample.py,
# который остаётся его потребителем.
BOUNDARY_DOC_IDS = [
    "loan_0006__m2718_homoglyph",       # Stage4-B: PHONE не детектируется под гомоглифом
    "services_0002__m1337_linebreak",   # Stage4-B: то же
    "works_0009",                       # PER-B: косвенный падеж рвётся двумя спанами
    "sale_0006",                        # PER-B: то же
]

# Потолок состава. Был 25 (docs/PERF_REPORT.md §П.2.2 — «запас до 25
# документов»), поднят до 28 сессией CORPUS-V2 вместе с исправлением признака
# «слово разорвано форматированием».
#
# ПОЧЕМУ 25 СТАЛО НЕДОСТИЖИМО, А НЕ «НЕУДОБНО». После перехода на строгое
# определение (build_structure.py) конфигураций `config_ext` стало 25 вместо
# 21. У каждого .docx РОВНО ОДИН `config_ext`, у .txt его нет вовсе, значит
# покрыть 25 конфигураций нельзя меньше чем 25 документами .docx; плюс хотя бы
# один .txt на класс формата. Нижняя граница состава — 26 документов, и она
# арифметическая, а не алгоритмическая. Отбор даёт 28 (две жадности —
# по приросту и по редкости класса — сходятся к одному числу).
#
# Цена ЗАМЕРЕНА, а не оценена: срез 25 док. занимал 36.4 с
# (docs/HANDOFF_SUBSET_ITER.md §3), новый состав из 28 док. — 46.8 с, 0 крешей.
# Рост +29% больше, чем прирост числа документов (+12%): добранные документы
# оказались тяжелее среднего. Против полного корпуса (324 док.) срез всё равно
# остаётся быстрым, ради чего потолок и существует. Ослаблением теста это не
# является: 100% покрытия страт сохранено, изменилось только число, при котором
# оно достижимо.
MAX_SUBSET_DOCS = 28


def load_gold():
    with open(GOLD_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_structure():
    with open(STRUCTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_subset():
    """Полный объект subset_iter.json (список + провенанс)."""
    with open(SUBSET_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_subset_ids():
    """Зафиксированный список doc_id. НЕ пересчитывается при запуске."""
    return list(load_subset()["doc_ids"])


def doc_classes(d, structure):
    """Множество страт одного документа.

    Универсум (docs/PERF_REPORT.md §3.4 «C», 191 элемент, воспроизведён
    точно) плюс структура и мутационная матрица:
      ('tct', тип, категория, trick)  — 126
      ('feat', f) для features[]      — 48
      ('ct', contract_type)           — 9
      ('parties', parties)            — 6
      ('fmt', format)                 — 2      итого 191
      ('struct', config)              — 14 (только .docx, PERF §3.3)
      ('struct_ext', config_ext)      — 25 (только .docx, строгое определение
                                        «слово разорвано форматированием»;
                                        было 21 по ошибочному, см.
                                        build_structure.py)
      ('mut', суффикс doc_id)         — 14 = 7 мутаций x 2 маркера m1337/m2718
    """
    s = set()
    for e in d["entities"]:
        s.add(("tct", e["type"], e.get("category"), e.get("trick")))
    for f in (d.get("features") or []):
        s.add(("feat", f))
    s.add(("ct", d["contract_type"]))
    s.add(("parties", d["parties"]))
    s.add(("fmt", d["format"]))
    doc_id = d["doc_id"]
    st = structure["docs"].get(doc_id)
    if st is not None:                       # только .docx
        s.add(("struct", st["config"]))
        s.add(("struct_ext", st["config_ext"]))
    if "__" in doc_id:
        s.add(("mut", doc_id.split("__", 1)[1]))
    return s


def universe(gold, structure):
    u = set()
    for d in gold:
        u |= doc_classes(d, structure)
    return u


def coverage(doc_ids, gold, structure):
    """(покрытые классы, непокрытые классы) для набора doc_id."""
    want = universe(gold, structure)
    by_id = {d["doc_id"]: d for d in gold}
    got = set()
    for i in doc_ids:
        if i in by_id:
            got |= doc_classes(by_id[i], structure)
    return got, (want - got)


def describe_class(c):
    """Человекочитаемое имя страты — для сообщения падающего теста."""
    kind = c[0]
    if kind == "tct":
        return "тип x категория x trick: %s / %s / %s" % (c[1], c[2], c[3])
    if kind == "feat":
        return "features[]: %s" % c[1]
    if kind == "ct":
        return "contract_type: %s" % c[1]
    if kind == "parties":
        return "parties: %s" % c[1]
    if kind == "fmt":
        return "формат файла: %s" % c[1]
    if kind == "struct":
        return "структурная конфигурация .docx: %s" % c[1]
    if kind == "struct_ext":
        return "структурная конфигурация .docx (ext): %s" % c[1]
    if kind == "mut":
        return "adversarial-мутация: %s" % c[1]
    return repr(c)
