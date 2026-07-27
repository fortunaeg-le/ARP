# -*- coding: utf-8 -*-
"""
Контракт зафиксированного итерационного среза (tests/corpus/subset_iter.json).

Список документов заморожен и не пересчитывается при запуске инструментов —
иначе сравнение с results_iter_baseline.json теряет смысл. Плата за заморозку:
список может протухнуть. Появился в корпусе документ с новым классом поведения
(новый trick, новый contract_type, новая структурная конфигурация .docx) — срез
молча перестаёт покрывать 100% страт, а инструменты продолжают печатать зелёные
числа на неполном наборе.

Этот файл превращает список-догму в список-контракт: тест краснеет и называет
КОНКРЕТНЫЙ непокрытый класс. Лечение — пересобрать состав
(`tests/corpus/build_subset_iter.py`) и вместе с ним baseline среза
(`tests/corpus/run_iter_baseline.py`), а НЕ ослабить тест.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, CORPUS)

import subset_lib as SL  # noqa: E402


@pytest.fixture(scope="module")
def data():
    return SL.load_gold(), SL.load_structure(), SL.load_subset_ids()


def _fail_message(missing, doc_ids):
    lines = [
        "Зафиксированный срез больше НЕ покрывает 100%% страт gold.json: "
        "непокрыто %d класс(ов)." % len(missing),
        "Документов в срезе: %d." % len(doc_ids),
        "НЕПОКРЫТЫЕ КЛАССЫ:",
    ]
    lines += ["    " + SL.describe_class(c) for c in sorted(missing, key=repr)]
    lines += [
        "",
        "Что делать: пересобрать состав и baseline среза, В ЭТОМ ПОРЯДКЕ —",
        "    venv/Scripts/python.exe tests/corpus/build_structure.py",
        "    venv/Scripts/python.exe tests/corpus/build_subset_iter.py",
        "    venv/Scripts/python.exe tests/corpus/run_iter_baseline.py",
        "Ослаблять этот тест нельзя: непокрытый класс — это класс, регресс в "
        "котором срез не увидит.",
    ]
    return "\n".join(lines)


def test_subset_covers_all_strata(data):
    """Главный инвариант: 100% страт текущего gold.json."""
    gold, structure, doc_ids = data
    _, missing = SL.coverage(doc_ids, gold, structure)
    assert not missing, _fail_message(missing, doc_ids)


def test_subset_docs_exist_in_gold(data):
    """Список не должен ссылаться на документы, которых в корпусе уже нет."""
    gold, _, doc_ids = data
    known = {d["doc_id"] for d in gold}
    unknown = [i for i in doc_ids if i not in known]
    assert not unknown, (
        "В subset_iter.json есть doc_id, которых нет в gold.json: %s. "
        "Пересоберите состав build_subset_iter.py." % ", ".join(unknown))


def test_subset_within_cap(data):
    """Потолок состава — subset_lib.MAX_SUBSET_DOCS (там же обоснование числа).

    Сессия CORPUS-V2 подняла его с 25 до 28: после исправления признака
    «слово разорвано форматированием» структурных конфигураций стало 25, у
    каждого .docx она ровно одна, и 25 документов физически не могут покрыть
    25 конфигураций ПЛЮС класс формата .txt."""
    _, _, doc_ids = data
    assert len(doc_ids) <= SL.MAX_SUBSET_DOCS, (
        "В срезе %d документов при потолке %d — срез перестал быть быстрым."
        % (len(doc_ids), SL.MAX_SUBSET_DOCS))


def test_subset_contains_boundary_docs(data):
    """Граничные документы (Stage4-B, PER-B) — в срезе всегда.

    Они там не за покрытие классов, а потому что исторически именно на них
    точечная проверка пропускала реальные дефекты."""
    gold, _, doc_ids = data
    known = {d["doc_id"] for d in gold}
    want = [i for i in SL.BOUNDARY_DOC_IDS if i in known]
    missing = [i for i in want if i not in doc_ids]
    assert not missing, (
        "Из среза выпали граничные документы: %s" % ", ".join(missing))


def test_subset_covers_both_formats(data):
    """И .docx, и .txt: etalon.py/acceptance.py до этого этапа .txt не видели."""
    gold, _, doc_ids = data
    by_id = {d["doc_id"]: d for d in gold}
    got = {by_id[i]["format"] for i in doc_ids if i in by_id}
    assert got == {d["format"] for d in gold}, (
        "Срез не покрывает оба формата файла: %s" % sorted(got))


def test_subset_covers_all_mutations(data):
    """Все adversarial-мутации по ОБОИМ маркерам m1337/m2718."""
    gold, _, doc_ids = data
    want = {d["doc_id"].split("__", 1)[1] for d in gold if "__" in d["doc_id"]}
    got = {i.split("__", 1)[1] for i in doc_ids if "__" in i}
    missing = sorted(want - got)
    assert not missing, (
        "В срезе нет adversarial-мутаций: %s" % ", ".join(missing))


def test_subset_covers_every_contract_type(data):
    """Минимум по одному документу каждого contract_type."""
    gold, _, doc_ids = data
    by_id = {d["doc_id"]: d for d in gold}
    want = {d["contract_type"] for d in gold}
    got = {by_id[i]["contract_type"] for i in doc_ids if i in by_id}
    missing = sorted(want - got)
    assert not missing, (
        "В срезе нет contract_type: %s" % ", ".join(missing))


def test_structure_file_matches_corpus(data):
    """structure.json описывает ровно те .docx, что лежат в корпусе.

    Если .docx добавили или заменили, а build_structure.py не перезапускали,
    универсум структурных конфигураций окажется вычислен по устаревшим данным
    и покрытие будет проверено не по тому корпусу."""
    _, structure, _ = data
    on_disk = {os.path.splitext(f)[0]
               for f in os.listdir(os.path.join(CORPUS, "docs"))
               if f.lower().endswith(".docx")}
    described = set(structure["docs"])
    assert described == on_disk, (
        "structure.json рассинхронизирован с tests/corpus/docs: "
        "только в файле %s; только на диске %s. "
        "Перезапустите tests/corpus/build_structure.py."
        % (sorted(described - on_disk)[:10], sorted(on_disk - described)[:10]))
