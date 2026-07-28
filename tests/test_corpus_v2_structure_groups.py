# -*- coding: utf-8 -*-
"""
Контракт двух групп структуры корпуса V2 (задача 4 сессии CORPUS-V2).

Группы не смешиваются, и это не вопрос аккуратности, а вопрос смысла цифр:

  "simple"  — структура как в старом корпусе. На ней меряются НОВЫЕ ВИДЫ
              ДАННЫХ, чтобы результат не смешивался с потерями чтения.
  "complex" — отслеживаемые правки, поля Word, умные теги, элементы формы,
              колонтитулы, документы на 2000+ абзацев. Известно, что программа
              теряет на них текст. Группа нужна НЕ для метрик новых видов, а
              чтобы потери были измеримы и видны числом.

Если завтра кто-то посчитает recall новых видов по всему корпусу разом, он
получит цифру, в которой пропуск детектора и потеря извлекателя неотличимы.
Тесты ниже держат разделение явным и проверяемым.
"""
import json
import os
import re
import sys
import zipfile
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_V2 = os.path.join(ROOT, "tests", "corpus_v2")
DOCS = os.path.join(CORPUS_V2, "docs")
GOLD_V2 = os.path.join(CORPUS_V2, "gold_v2.json")
sys.path.insert(0, CORPUS_V2)

import values as V  # noqa: E402

GROUPS = ("simple", "complex")

# Конструкции, ради которых существует сложная группа, и их след в OOXML.
COMPLEX_XML = {
    "tracked_ins": r"<w:ins[ >]",
    "tracked_del": r"<w:del[ >]",
    "field_simple": r"<w:fldSimple[ >]",
    "field_complex": r"<w:instrText[ >]",
    "smart_tag": r"<w:smartTag[ >]",
    "sdt_form": r"<w:sdt[ >]",
}


@pytest.fixture(scope="module")
def gold():
    if not os.path.exists(GOLD_V2):
        pytest.fail("Нет %s — соберите корпус: "
                    "venv/Scripts/python.exe tests/corpus_v2/generate.py" % GOLD_V2)
    with open(GOLD_V2, encoding="utf-8") as f:
        return json.load(f)


def _xml(doc_id):
    with zipfile.ZipFile(os.path.join(DOCS, doc_id + ".docx")) as z:
        return z.read("word/document.xml").decode("utf-8")


def _corpus_v2_lib():
    """Ядро корпуса V2, загруженное ПО ПУТИ, а не по имени.

    Обычный `import corpus_lib` здесь недопустим: такой же модуль есть у
    СТАРОГО корпуса, его каталог тоже попадает в sys.path при полном прогоне
    набора, и какой из двух достанется — зависит от порядка тестов. Тест,
    молча проверяющий чужой корпус, хуже отсутствующего.
    """
    import importlib.util
    name = "corpus_v2_lib_for_tests"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(CORPUS_V2, "corpus_lib.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_every_doc_declares_its_group(gold):
    """Пометка группы обязательна у каждого документа."""
    bad = [d["doc_id"] for d in gold if d.get("structure_group") not in GROUPS]
    assert not bad, (
        "У документов нет корректной пометки structure_group: %s" % bad[:10])


def test_both_groups_are_populated(gold):
    c = Counter(d["structure_group"] for d in gold)
    assert c["simple"] and c["complex"], (
        "Одна из групп пуста: %s. Обе нужны — на простой меряются новые виды "
        "данных, сложная показывает потери чтения." % dict(c))


def test_simple_group_has_no_complex_constructions(gold):
    """ГЛАВНЫЙ ИНВАРИАНТ: простая группа чиста от сложных конструкций.

    Иначе «чистая» площадка для метрик новых видов данных перестаёт быть
    чистой, и потеря извлекателя попадёт в цифру пропусков детектора."""
    dirty = []
    for d in gold:
        if d["structure_group"] != "simple" or d["format"] != "docx":
            continue
        xml = _xml(d["doc_id"])
        for name, rx in COMPLEX_XML.items():
            if re.search(rx, xml):
                dirty.append("%s: %s" % (d["doc_id"], name))
    assert not dirty, (
        "В группу «простая структура» просочились сложные конструкции:\n  %s\n"
        "Группы смешивать нельзя." % "\n  ".join(dirty[:15]))


def test_complex_group_covers_every_declared_construction(gold):
    """Каждая конструкция из списка задачи 4 реально встречается в корпусе."""
    seen = set()
    for d in gold:
        if d["structure_group"] != "complex":
            continue
        xml = _xml(d["doc_id"])
        for name, rx in COMPLEX_XML.items():
            if re.search(rx, xml):
                seen.add(name)
    missing = sorted(set(COMPLEX_XML) - seen)
    assert not missing, (
        "Сложная группа не содержит конструкций: %s. Потери на них измерить "
        "будет нечем." % ", ".join(missing))


def test_complex_group_has_header_footer_and_huge_docs(gold):
    """Колонтитулы и документы на 2000+ абзацев — тоже часть списка."""
    n_hdr = n_ftr = n_huge = 0
    for d in gold:
        if d["structure_group"] != "complex":
            continue
        with zipfile.ZipFile(os.path.join(DOCS, d["doc_id"] + ".docx")) as z:
            parts = z.namelist()
            xml = z.read("word/document.xml").decode("utf-8")
        n_hdr += any(p.startswith("word/header") for p in parts)
        n_ftr += any(p.startswith("word/footer") for p in parts)
        n_huge += len(re.findall(r"<w:p[ >]", xml)) >= 2000
    assert n_hdr and n_ftr, (
        "В сложной группе нет колонтитулов (верхних %d, нижних %d)" % (n_hdr, n_ftr))
    assert n_huge >= 1, (
        "В сложной группе нет ни одного документа на 2000+ абзацев — класс "
        "объёмных документов не покрыт.")


def test_docx_bytes_do_not_depend_on_the_process(gold):
    """Корпус обязан быть побайтно воспроизводимым — В ТОМ ЧИСЛЕ между запусками.

    Здесь был дефект, и он прожил незамеченным целую сессию: идентификатор
    фигуры текстбокса считался встроенным `hash(str)`, а тот рандомизируется
    при каждом запуске интерпретатора (PYTHONHASHSEED). Десять .docx выходили
    разными байтами при каждой пересборке; `gold_v2.json` при этом совпадал,
    потому что идентификатор фигуры в разметку не попадает, — и проверка
    «пересобрали, gold совпал» дефект не видела.

    Тест сверяет XML документа с тем же XML, построенным в ЭТОМ процессе из
    сохранённой модели. Разойтись они могут только на значении, зависящем от
    состояния интерпретатора, а не от модели.
    """
    CL = _corpus_v2_lib()
    checked = 0
    bad = []
    for d in gold:
        if d["format"] != "docx":
            continue
        model = CL.load_model(CORPUS_V2, d["doc_id"])
        if not any(b.get("t") == "tb" for b in model["body"]):
            continue
        CL._REV["n"] = 1
        rebuilt = CL._blocks_xml(model["body"], {"items": [], "next": 1})
        checked += 1
        if rebuilt not in _xml(d["doc_id"]):
            bad.append(d["doc_id"])
    assert checked, "Ни одного .docx с текстбоксом — ловушка нечем проверяется."
    assert not bad, (
        "XML этих документов не воспроизводится из модели в другом процессе: "
        "%s.\nЗначит, в него просочилось значение, зависящее от состояния "
        "интерпретатора, а не от модели — корпус перестал быть побайтно "
        "воспроизводимым." % bad)


def test_deleted_text_never_reaches_markup(gold):
    """Текст, удалённый отслеживаемой правкой, в документе НЕ виден.

    Он живёт только в <w:delText> и разметки не получает. Это ловушка для
    извлекателя: если он вычитает удалённый текст наравне с обычным, он
    породит величину, которой в документе нет. Такое обязано считаться ЛОЖНЫМ
    СРАБАТЫВАНИЕМ, а не находкой, — значит, в эталоне фантома быть не должно.
    """
    hits = [d["doc_id"] for d in gold
            for e in d["entities"] if V.DEL_GHOST in e["text"]]
    assert not hits, (
        "Удалённый правкой текст попал в разметку: %s" % hits[:5])

    with_del = [d["doc_id"] for d in gold
                if d["format"] == "docx" and re.search(r"<w:del[ >]", _xml(d["doc_id"]))]
    assert with_del, "Ни одного документа с отслеживаемым удалением — ловушки нет."
