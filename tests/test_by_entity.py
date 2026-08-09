# -*- coding: utf-8 -*-
"""ЭТАП REG — стражи метрики ПО СУЩНОСТЯМ (tests/corpus/by_entity.py).

Метрика существует ради одного правила: человек защищён, только если закрыты
ВСЕ его упоминания. Правило легко потерять при первой же правке — тогда счёт
тихо вернётся к вхождениям и станет опять приукрашивать. Здесь заперты само
правило, склейка мутированных форм в одно значение и совокупный критерий
«защищена».
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import by_entity as BE  # noqa: E402


def _doc(doc_id, ents):
    return {"outcome": "processed", "doc_id": doc_id, "entities": ents}


def _e(t, text, found, leak_status="none"):
    return {"type": t, "text": text, "found": found,
            "leak_v2": {"status": leak_status, "fragments": [],
                        "window_len": 0, "core_len": 0}}


def test_one_open_occurrence_kills_the_whole_entity():
    """ГЛАВНОЕ ПРАВИЛО: восемь закрытых из девяти — это НЕ 89 % защиты, а ноль."""
    ents = [_e("PER", "Петров И. С.", True)] * 8 + [_e("PER", "Петров И. С.", False)]
    g = BE.group_entities([_doc("d1", ents)])
    assert len(g) == 1, "одно значение в одном документе — одна сущность"
    assert g[0]["n_occ"] == 9 and g[0]["found_occ"] == 8
    assert g[0]["masked_all"] is False
    assert g[0]["protected"] is False


def test_all_occurrences_masked_is_protected():
    g = BE.group_entities([_doc("d1", [_e("PER", "Петров И. С.", True)] * 3)])
    assert g[0]["masked_all"] and g[0]["protected"]


def test_leak_kills_protection_even_when_masked():
    """Совокупный критерий: маска есть, но фрагмент дожил — сущность не защищена."""
    g = BE.group_entities([_doc("d1", [_e("PER", "Петров И. С.", True, "partial")])])
    assert g[0]["masked_all"] is True
    assert g[0]["protected"] is False


def test_same_value_in_different_docs_are_different_entities():
    """Защита оценивается по документу, который уходит в LLM, а не по корпусу."""
    e = [_e("PER", "Петров И. С.", True)]
    g = BE.group_entities([_doc("d1", e), _doc("d2", e)])
    assert len(g) == 2


def test_mutations_of_one_value_collapse():
    """Регистр и пробелы внутри номера — то же самое значение, не два."""
    g = BE.group_entities([_doc("d1", [_e("PER", "ПЕТРОВ И.С.", True),
                                       _e("PER", "Петров  и.с.", True)])])
    assert len(g) == 1 and g[0]["n_occ"] == 2
    g2 = BE.group_entities([_doc("d1", [_e("INN", "7736050003", True),
                                        _e("INN", "77 36 050 003", True)])])
    assert len(g2) == 1 and g2[0]["n_occ"] == 2


def test_person_key_merges_forms_of_one_person():
    assert BE.person_key("Петров И. С.") == BE.person_key("И. С. Петров")
    assert BE.person_key("Петров И. С.") != BE.person_key("Сидоров А. А.")


def test_crashed_documents_are_not_counted():
    g = BE.group_entities([{"outcome": "crashed", "doc_id": "d1",
                            "entities": [_e("PER", "Петров", True)]}])
    assert g == []
