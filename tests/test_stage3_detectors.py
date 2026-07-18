# -*- coding: utf-8 -*-
"""ЭТАП 3 — детекторы SNILS, BIRTHDATE и кода подразделения паспорта.

Контракт «дыра закрыта» живёт в tests/test_future_contracts.py (там он написан не
чинившим и проверяет через публичный вход `shifrator.py encrypt`). Здесь —
свойства, которые сам этап обязан удержать и которые тем контрактом не ловятся:

  1. ГРАНИЦЫ (B-корректность). Якорь — ТРИГГЕР детекции, но НЕ часть маскируемого
     спана. Спан обязан быть ровно значением. Иначе повторяется дефект PASSPORT
     0c-B (спан включает слово «паспорт», recall_exact 0%).
  2. НЕВАЛИДНАЯ КОНТРОЛЬНАЯ СУММА. У СНИЛС чек-сумма есть, но на неё НАМЕРЕННО не
     повешен `validate:`: номер с не сошедшейся КС — всё равно ПДн (инвариант этапа
     4, STATE §4/§6), и отбраковка была бы утечкой. Проверяем на реальном
     checksum:invalid из замороженного корпуса.
  3. ЯКОРНОСТЬ. Дата БЕЗ якоря рождения — не BIRTHDATE (договор состоит из дат:
     подписания, выдачи, сроков — общий детектор дат обвалил бы precision). Голый
     «ддд-ддд» без якоря — не код подразделения (кусок телефона/индекса).

Проверки идут через detect_regex по документу корпуса: это уровень, на котором
существует ПОНЯТИЕ СПАНА (в анонимном тексте спана уже нет, только маска), а
свойство «граница спана» иначе непроверяемо.
"""
import json
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORPUS_DIR = os.path.join(_PROJECT_ROOT, "tests", "corpus")
_DOCS_DIR = os.path.join(_CORPUS_DIR, "docs")
_CONFIG = os.path.join(_PROJECT_ROOT, "entity_types.yaml")

for _p in (os.path.join(_PROJECT_ROOT, "src"), _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from extractor import extract          # noqa: E402
from models import SourceDocument, TextSegment  # noqa: E402
from regex_detector import detect_regex  # noqa: E402

_GOLD = {d["doc_id"]: d for d in
         json.load(open(os.path.join(_CORPUS_DIR, "gold.json"), encoding="utf-8"))}


def _detect(doc_id):
    fmt = _GOLD[doc_id]["format"]
    doc = extract(os.path.join(_DOCS_DIR, "{}.{}".format(doc_id, fmt)))
    return detect_regex(doc, _CONFIG)


def _texts(entities, entity_type):
    return [e.original_text for e in entities if e.entity_type == entity_type]


def _detect_text(text):
    """detect_regex над синтетическим односегментным документом.

    Используется ТОЛЬКО для НЕГАТИВОВ (проверка, что якорная логика чего-то НЕ
    берёт). Положительные ожидания строятся на замороженном корпусе (правило: не
    подгонять детектор под выдуманные данные)."""
    doc = SourceDocument(
        segments=[TextSegment(id="s0", text=text, source_type="txt_line", metadata={})],
        source_format="txt",
        source_path="<memory>",
    )
    return detect_regex(doc, _CONFIG)


# --------------------------------------------------------------------------- #
# 1. Границы: детектируем ПО якорю, маскируем БЕЗ якоря                        #
# --------------------------------------------------------------------------- #
class TestAnchorIsNotPartOfSpan:
    """Спан новых детекторов = ровно значение. Якорь остаётся в открытом тексте —
    он и должен там остаться: «СНИЛС»/«Дата рождения»/«код подразделения» сами по
    себе не ПДн, а расширение спана на них ломает B-корректность."""

    @pytest.mark.parametrize("doc_id, entity_type, value, anchor", [
        ("services_0001", "SNILS", "793-234-941 33", "СНИЛС"),
        ("services_0001", "BIRTHDATE", "21.01.1960", "рожден"),
        ("services_0001", "PASSPORT", "812-400", "подразделен"),
        ("supply_0003", "SNILS", "003-219-113 82", "СНИЛС"),
        ("supply_0003", "PASSPORT", "051-237", "подразделен"),
    ])
    def test_span_is_exactly_the_value(self, doc_id, entity_type, value, anchor):
        found = _texts(_detect(doc_id), entity_type)
        assert value in found, (
            "{} {!r} не найден в {} (найдено: {})".format(entity_type, value, doc_id, found))
        for text in found:
            assert anchor.lower() not in text.lower(), (
                "якорь {!r} попал в маскируемый спан {!r} — это дефект границ 0c-B, "
                "детектировать по якорю можно, маскировать якорь нельзя"
                .format(anchor, text))


# --------------------------------------------------------------------------- #
# 2. Невалидная контрольная сумма СНИЛС — всё равно ПДн                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("doc_id, value", [
    ("labor_0005", "599-690-726 97"),
    ("lease_0003", "128-975-306 10"),
    ("services_0011", "600-912-277 54"),
])
def test_snils_with_invalid_checksum_is_still_detected(doc_id, value):
    """Документы корпуса с features=['invalid_checksum', ...]. Такой номер обязан
    детектироваться: для 152-ФЗ он ПДн независимо от того, сошлась ли КС. Если
    когда-нибудь на SNILS повесят `validate:`, этот тест покраснеет — и правильно
    сделает (это заранее воспроизвело бы дыру этапа 4)."""
    assert any(e["type"] == "SNILS" and e["text"] == value
               for e in _GOLD[doc_id]["entities"]), \
        "корпус изменился: в {} нет SNILS {!r}".format(doc_id, value)
    assert value in _texts(_detect(doc_id), "SNILS"), \
        "СНИЛС с невалидной КС отбракован — отбраковка ПДн есть утечка"


# --------------------------------------------------------------------------- #
# 3. Якорность: без якоря не берём                                             #
# --------------------------------------------------------------------------- #
class TestNoAnchorNoDetection:

    @pytest.mark.parametrize("text", [
        "Договор подписан 12.03.2024 в городе Москве.",
        "Дата выдачи 03.12.2019, срок действия до 31.12.2025.",
        "Настоящий договор действует с 01.01.2020 по 01.01.2021.",
        "Дата составления акта: 15.11.2022",
    ])
    def test_non_birth_dates_are_not_birthdate(self, text):
        """Главный риск precision этапа: договор состоит из дат. Ловится ТОЛЬКО дата
        под якорем рождения; остальные — не наше дело, их увидит человек."""
        assert _texts(_detect_text(text), "BIRTHDATE") == []

    def test_birth_anchor_makes_the_same_date_detected(self):
        """Обратная сторона предыдущего: та же по форме дата ПОД якорем берётся.
        Без этой проверки предыдущий тест был бы зелёным и у сломанного детектора."""
        assert _texts(_detect_text("Дата рождения: 15.11.2022"), "BIRTHDATE") == ["15.11.2022"]

    @pytest.mark.parametrize("text", [
        "Позиция 051-237 в спецификации.",
        "Телефон 8 (495) 051-237-11-22.",
        "Индекс 051-237 не является кодом.",
    ])
    def test_bare_ddd_ddd_is_not_a_subdivision_code(self, text):
        """Голый «ддд-ддд» — мусор (кусок телефона, номер позиции). Код
        подразделения берётся ТОЛЬКО под своим якорем."""
        assert "051-237" not in _texts(_detect_text(text), "PASSPORT")

    def test_subdivision_anchor_makes_the_same_code_detected(self):
        assert "051-237" in _texts(
            _detect_text("паспорт выдан 01.02.2015, код подразделения 051-237"), "PASSPORT")

    @pytest.mark.parametrize("text", [
        "Расчётный счёт 123-456-789 12 указан в реквизитах.",
        "Позиции 003-219-113 82 по накладной.",
    ])
    def test_bare_snils_shaped_number_is_not_snils(self, text):
        """Форма СНИЛС без якоря «СНИЛС» не берётся как SNILS. Чек-сумма НЕ
        используется как основание детекции без якоря: ~1% случайных чисел проходят
        её, и одиночная форма — слишком слабый сигнал для нового типа."""
        assert _texts(_detect_text(text), "SNILS") == []
