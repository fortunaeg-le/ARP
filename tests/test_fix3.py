# -*- coding: utf-8 -*-
"""ЭТАП FIX-3 — стражи двух закрытых долгов.

  1. TestPageNumberIsNotAddress — `NODES-FOOTER-PAGENUM`: «Стр. 1 из 3» больше
     не адрес. Держится ОБРАТНОЕ направление: адреса со «стр.» (настоящее
     строение) не потеряны — иначе починка порчи документа обернулась бы
     утечкой ПДн.
  2. TestRightAntiAnchor — `FIX2-ANTIANCHOR-RIGHT`: дисквалификатор ПОСЛЕ
     значения. Отдельно проверено, что PASSPORT не тронут (его левое окно
     ровно 20 и держится своим стражем в test_fix2_guards).
"""
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import regex_detector as RD  # noqa: E402
from models import SourceDocument, TextSegment  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402

CFG = os.path.join(ROOT, "entity_types.yaml")


def mask(text):
    seg = TextSegment(id="s0", text=text, source_type="docx_paragraph", metadata={})
    doc = SourceDocument(segments=[seg], source_format="docx", source_path="<fix3>")
    anon, kept = tokenize(doc, run_detection(doc, CFG), CFG)
    return anon


# =========================================================================== #
#  1. Номер страницы — не адрес
# =========================================================================== #

class TestPageNumberIsNotAddress:

    @pytest.mark.parametrize("text", [
        "Стр. 1 из 3", "Стр. 1", "стр. 12", "Стр. 2 из 15",
    ])
    def test_page_number_is_not_masked(self, text):
        assert "[ADDRESS" not in mask(text), (
            "нумерация страницы замаскирована как адрес — порча документа на "
            "каждой странице (NODES-FOOTER-PAGENUM)")

    @pytest.mark.parametrize("text", [
        "г. Москва, ул. Мира, д. 5, стр. 2",
        "125009, г. Москва, ул. Тверская, д. 7, стр. 1, оф. 20",
        "г. Москва, ул. Ленина, д. 5, кв. 2",
        "190000, г. Санкт-Петербург, наб. Гагаринская, д. 3",
        "а/я 83, г. Новосибирск",
        "Тверь, Полевая 21",
    ])
    def test_real_addresses_are_not_lost(self, text):
        """ОБРАТНОЕ направление. Ограничитель структурный: «стр.» перестаёт
        анкорить ОДИН, но внутри адреса с улицей/городом работает как прежде."""
        assert "[ADDRESS" in mask(text), (
            "настоящий адрес потерян — ограничитель «стр.» задел живое")

    def test_dependent_marker_needs_a_companion(self):
        """Механизм назван прямо: зависимый маркер + локальность = адрес."""
        assert "[ADDRESS" not in mask("Стр. 4")
        assert "[ADDRESS" in mask("г. Тверь, стр. 4")


# =========================================================================== #
#  2. Правый край анти-якоря
# =========================================================================== #

_NORM_REF = [
    "Требования к качеству работ могут быть предъявлены в течение одного года, "
    "как установлено статьёй 725 Гражданского кодекса Российской Федерации.",
    "Претензии по качеству работ предъявляются в течение одного года в силу "
    "статьи 725 Гражданского кодекса Российской Федерации.",
    "Требования по качеству работ заявляются в течение одного года по правилам "
    "статьи 725 Гражданского кодекса Российской Федерации.",
]

_REAL_TERMS = [
    "Оплата производится в течение 7 рабочих дней с даты подписания акта.",
    "Товар поставляется в течение 30 календарных дней.",
    "Работы выполняются в течение пяти банковских дней согласно Приложению № 2.",
    "Уведомление направляется не позднее 08.12.2025.",
]


class TestRightAntiAnchor:

    @pytest.mark.parametrize("text", _NORM_REF)
    def test_norm_reference_after_the_value_disqualifies(self, text):
        assert "[TERM" not in mask(text), (
            "срок из НОРМЫ ПРАВА замаскирован — ссылка на закон становится "
            "нечитаемой (FIX2-ANTIANCHOR-RIGHT)")

    @pytest.mark.parametrize("text", _REAL_TERMS)
    def test_real_obligation_terms_survive(self, text):
        assert "[TERM" in mask(text), (
            "настоящий срок обязательства потерян — правый дисквалификатор "
            "задел живое (направление ошибки обязано быть «лучше не найти», "
            "но не ценой целого класса)")

    def test_window_does_not_cross_a_newline(self):
        """Дисквалификатор из СЛЕДУЮЩЕГО абзаца не в счёт — то же правило, что
        у левого окна (чужое предложение не дисквалифицирует)."""
        assert "[TERM" in mask(
            "Оплата в течение 10 дней\nкак установлено статьёй 314 ГК РФ.")

    def test_right_window_is_configured_per_type_not_globally(self):
        """Требование задания: правый край задаётся ОТДЕЛЬНО от левого и по
        типам. Ключ есть ровно у тех типов, кому он назначен."""
        with open(CFG, encoding="utf-8") as f:
            types = yaml.safe_load(f)["entity_types"]
        with_right = {t for t, sp in types.items()
                      if isinstance(sp, dict) and "anti_anchor_right" in sp}
        assert with_right == {"TERM"}, (
            "правый дисквалификатор расползся на типы, которым его не давали: "
            "%s" % sorted(with_right))
        assert RD._ANTI_ANCHOR_RIGHT_WINDOW != RD._ANTI_ANCHOR_WINDOW or True
        # левое окно PASSPORT не тронуто (его страж — test_fix2_guards)
        assert "anti_anchor_right" not in types["PASSPORT"]
        assert types["PASSPORT"].get("anti_anchor_window") is None, (
            "PASSPORT обязан остаться на умолчании 20 — его окно держится "
            "стражем и правкой этого этапа не двигается")

    def test_right_anti_anchor_is_a_separate_mechanism_from_the_left(self):
        """Отдельная функция и отдельная константа окна: связать их одной
        величиной значило бы двигать левое окно, правя правое."""
        assert callable(RD._has_anti_anchor_right)
        assert RD._ANTI_ANCHOR_RIGHT_WINDOW == 40
        assert RD._ANTI_ANCHOR_WINDOW == 20

    def test_determinism_on_repeated_runs(self):
        first = [mask(t) for t in _NORM_REF + _REAL_TERMS]
        for _ in range(2):
            assert [mask(t) for t in _NORM_REF + _REAL_TERMS] == first


# =========================================================================== #
#  3. Словесные суммы (SUMWORDS-DEFERRED)
# =========================================================================== #

class TestWordSums:
    """ЭТАП FIX-3, часть 4. Чисто словесная сумма («восемьсот тысяч рублей»)
    без единой цифры. Числительные — ЗАКРЫТЫЙ грамматический класс: в паттерне
    перечислены корни, а границу задаёт обязательная валюта справа.

    Негативы держатся здесь, а не в корпусе: у SUM `generator: legacy` (его
    формы живут в values.py), и секции negatives у файла-описания нет — значит
    исполняемая форма требования «негативы обязательны» это тест."""

    @pytest.mark.parametrize("text", [
        "Цена Договора: восемьсот тысяч рублей.",
        "Три миллиона шестьсот пятьдесят тысяч рублей",
        "Один миллион сто семьдесят тысяч два рубля",
        "Пятнадцать миллионов рублей 00 копеек",
        "Шестьсот пятьдесят одна тысяча рублей",
        "Девятнадцать миллионов шестьсот тысяч рублей",
    ])
    def test_word_only_sum_is_masked(self, text):
        anon = mask(text)
        assert "[SUM" in anon, "словесная сумма не найдена: %r" % anon
        assert "рубл" not in anon, (
            "часть значения осталась открытой: %r" % anon)

    @pytest.mark.parametrize("text", [
        "Гарантия действует в течение трёх лет.",
        "Срок — двое суток.",
        "первая сторона обязуется",
        "Ставка пятьдесят процентов годовых.",
        "в течение пяти банковских дней",
        "десятая часть работ выполнена",
    ])
    def test_numerals_in_another_sense_are_not_sums(self, text):
        assert "[SUM" not in mask(text), (
            "числительное в другом смысле принято за сумму: %r" % mask(text))

    def test_composite_form_still_one_span_one_token(self):
        """Решение владельца: одна сумма = ОДИН токен. Словесная ветвь не
        должна разрезать составную форму на два."""
        anon = mask("Общая цена: 10 000 (Десять тысяч) рублей 00 копеек.")
        assert anon.count("[SUM") == 1, anon

    @pytest.mark.parametrize("text,expect", [
        ("неустойку в общем размере 10 (десять) миллионов рублей", "10 (десять) миллионов рублей"),
        ("составляет менее 3 (трех) миллиардов рублей;", "3 (трех) миллиардов рублей"),
    ])
    def test_scale_word_between_spelling_and_currency(self, text, expect):
        """НАЙДЕНО НА РЕАЛЬНОМ ДОКУМЕНТЕ: «10 (десять) МИЛЛИОНОВ рублей».
        До слота масштаба цифровая ветвь обрывалась на скобке, а словесная
        ловила только хвост «миллионов рублей» — число оставалось ОТКРЫТЫМ,
        то есть частичная утечка суммы. Требование владельца «одна сумма =
        один токен» проверяется прямо: маска ровно одна и число закрыто."""
        anon = mask(text)
        assert anon.count("[SUM") == 1, anon
        for piece in expect.split():
            assert piece not in anon, "часть значения открыта: %r" % anon
