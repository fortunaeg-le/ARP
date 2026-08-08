"""ЭТАП A7 — остаток качества: пять долгов, по каждому зеркало.

Правило зеркал этапа: у каждой правки здесь И положительный пример (то, что
раньше утекало и обязано найтись), И ОТРИЦАТЕЛЬНЫЙ (то, что рядом и обязано
остаться нетронутым). Без второго не видно переусердствования, а лишняя маска
портит документ.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from models import SourceDocument, TextSegment          # noqa: E402
from normalizer import normalize_for_detection, _ALPHA_FOLD, _ALPHA_FOLD_PAIRS  # noqa: E402
from regex_detector import detect_regex                 # noqa: E402

CONFIG = str(ROOT / "entity_types.yaml")


def detect(text):
    """(тип, значение) всех regex-сущностей одного сегмента."""
    seg = TextSegment(id="s1", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="x")
    return sorted((e.entity_type, e.original_text) for e in detect_regex(doc, CONFIG))


def values(text, type_):
    return [v for t, v in detect(text) if t == type_]


# --------------------------------------------------------------------------- #
#             ДОЛГ 1 — РЕГИСТР В СУММАХ (TGOLDA-SUM-CASE)                      #
# --------------------------------------------------------------------------- #
class TestSumCase:
    @pytest.mark.parametrize("text,expected", [
        ("Стоимость составляет 732 589,00 РУБ.", "732 589,00 РУБ."),
        ("Цена — 1 000 РУБЛЕЙ", "1 000 РУБЛЕЙ"),
        ("Аванс 500 Руб.", "500 Руб."),
        ("Комиссия 10 ДОЛЛ.", "10 ДОЛЛ."),
        ("Платёж 250 usd", "250 usd"),
    ])
    def test_uppercase_currency_is_found(self, text, expected):
        assert values(text, "SUM") == [expected]

    def test_lowercase_unchanged(self):
        assert values("Цена 5 руб.", "SUM") == ["5 руб."]

    @pytest.mark.parametrize("text", [
        # правая граница была регистро-независимой ДО правки и обязана
        # остаться такой: слово, начинающееся на «руб», суммой не является
        "Поставка 5 РУБАШЕК белых",
        "Поставка 5 рубашек белых",
        "Изделий 12 РУБИЛЬНИКОВ",
    ])
    def test_word_starting_like_currency_is_not_a_sum(self, text):
        assert values(text, "SUM") == []


# --------------------------------------------------------------------------- #
#        ДОЛГ 2 — РЕГИСТРОВАЯ ДЫРА В СВОДЕ АЛФАВИТОВ (Stage2b-A)               #
# --------------------------------------------------------------------------- #
class TestAlphaFoldCaseClosure:
    def test_table_is_closed_over_case(self):
        """Каждая пара представлена ОБОИМИ регистрами — иначе регистровая
        нормализация выше по конвейеру ломает свод."""
        for lat, cyr in _ALPHA_FOLD_PAIRS.items():
            assert _ALPHA_FOLD.get(lat.lower()) == cyr.lower(), lat
            assert _ALPHA_FOLD.get(lat.upper()) == cyr.upper(), lat

    @pytest.mark.parametrize("word,expected", [
        ("иbаноb", "иванов"),          # строчная латинская b — дыра до A7
        ("петpоh", "петрон"),          # h
        ("hикиtин", "никитин"),        # h и t
        ("иBаНоB", "иВаНоВ"),          # прописная — работала и раньше
    ])
    def test_mixed_alphabet_word_is_folded(self, word, expected):
        assert normalize_for_detection(word)[0] == expected

    @pytest.mark.parametrize("word", [
        "Microsoft",     # чисто латинское слово не трогаем (иначе FP NER)
        "bath",
        "hosting",
    ])
    def test_pure_latin_word_untouched(self, word):
        assert normalize_for_detection(word)[0] == word

    def test_no_new_pairs_invented(self):
        """Замыкание добавляет только регистровые формы УЖЕ принятых пар."""
        added = set(_ALPHA_FOLD) - set(_ALPHA_FOLD_PAIRS)
        assert added == {"b", "h", "t"}


# --------------------------------------------------------------------------- #
#     ДОЛГ 3 — ДАТА РОЖДЕНИЯ С ОМОГЛИФАМИ (ADDRB-BIRTHDATE-EXPOSED)            #
# --------------------------------------------------------------------------- #
class TestBirthdateLookalikes:
    @pytest.mark.parametrize("text,expected", [
        ("Дата рождения: 21.0І.196О", "21.0І.196О"),
        ("Дата рождения: О7.О7.19бЗ", "О7.О7.19бЗ"),
        # остаток Stage3-A: двойник ПЕРВЫМ символом — раньше жадный зазор
        # съедал его и маска была на символ короче
        ("Дата рождения (согласно паспортным данным): З7.07.1963", "З7.07.1963"),
        ("21.О1.198О г.р.", "21.О1.198О"),
    ])
    def test_lookalike_digits_are_masked_whole(self, text, expected):
        assert values(text, "BIRTHDATE") == [expected]

    def test_clean_date_unchanged(self):
        assert values("Дата рождения: 21.01.1960", "BIRTHDATE") == ["21.01.1960"]

    @pytest.mark.parametrize("text", [
        # ОТРИЦАТЕЛЬНЫЕ: расширенный класс не создаёт даты там, где её нет
        "Дата рождения не указана",
        "Дата рождения в анкете отсутствует, см. п. 2.1 договора",
        # дата БЕЗ якоря рождения не берётся ни с двойниками, ни без
        "Договор подписан 21.0І.196О",
        "Акт составлен 21.01.1960",
    ])
    def test_no_birthdate_without_shape_or_anchor(self, text):
        assert values(text, "BIRTHDATE") == []


# --------------------------------------------------------------------------- #
#   ДОЛГ 4 — ЛОЖНЫЕ ПО КОНТРОЛЬНОЙ СУММЕ (Stage4-C, A3-NEG-OGRN, A5-INN-KBK-FP) #
# --------------------------------------------------------------------------- #
class TestChecksumIsNotEnough:
    # значения подобраны так, что КС СХОДИТСЯ — иначе тест доказывал бы не
    # ограничитель, а арифметику
    EAN13 = "4600123456786"
    OKPO10 = "4527083899"
    # КБК, разбитый по три цифры (мутация digit_spaces / вёрстка платёжного
    # реквизита). Окно 0..15 внутри него ложится ровно на границу групп, то
    # есть доступно паттерну ОГРНИП, и его КС сходится — это и есть Stage4-C.
    KBK_GROUPED = "182 101 010 110 110 001 10"
    KBK_OGRN15_WINDOW = "182101010110110"

    def test_checksums_really_pass(self):
        """Ноль ниже обязан быть заслугой ограничителя, а не арифметики:
        у всех трёх значений контрольная сумма СХОДИТСЯ."""
        from regex_detector import ogrn_checksum, inn10_checksum
        assert ogrn_checksum(self.EAN13)
        assert inn10_checksum(self.OKPO10)
        assert ogrn_checksum(self.KBK_OGRN15_WINDOW)

    def test_patterns_still_reach_these_values(self):
        """…и паттерн до них по-прежнему ДОХОДИТ: отсекает именно ограничитель
        этапа A7, а не отсутствие совпадения."""
        import re
        import yaml
        with open(CONFIG, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)["entity_types"]
        assert re.search(cfg["OGRN"]["pattern"], self.EAN13)
        assert re.search(cfg["INN"]["pattern"], self.OKPO10)
        m = re.search(cfg["OGRN"]["pattern"], self.KBK_GROUPED)
        assert m and m.group(0).replace(" ", "") == self.KBK_OGRN15_WINDOW

    def test_barcode_is_not_ogrn(self):
        assert values("Товар маркирован штрихкодом %s." % self.EAN13, "OGRN") == []

    def test_okpo_is_not_inn(self):
        assert values("Код контрагента по ОКПО — %s." % self.OKPO10, "INN") == []

    def test_window_cut_from_longer_run_is_not_a_requisite(self):
        """КБК, разбитый пробелами: 10- и 15-значные окна внутри него КС
        проходят, но соседняя цифра говорит, что это кусок чужого числа."""
        text = "Оплата по КБК %s." % self.KBK_GROUPED
        assert values(text, "INN") == []
        assert values(text, "INN_PERSON") == []
        assert values(text, "OGRN") == []
        # и без слова-якоря чужой номенклатуры — работает второй ограничитель
        # (сосед-цифра), а не анти-якорь
        assert values("Реквизит платежа %s." % self.KBK_GROUPED, "OGRN") == []

    # --- обратная сторона: якорная дорога НЕ ослаблена ---
    def test_anchored_requisites_still_found(self):
        text = "ОГРН 1027700132195, ИНН 7707083893, КПП 770701001."
        assert values(text, "OGRN") == ["1027700132195"]
        assert values(text, "INN") == ["7707083893"]

    def test_anchored_value_next_to_another_number_still_found(self):
        """«ИНН 7707083893 770701001» — ИНН и КПП подряд: сосед-цифра есть,
        но подтверждение даёт якорь, а не КС (инвариант этапа 4)."""
        assert values("ИНН 7707083893 770701001", "INN") == ["7707083893"]

    def test_anti_anchor_does_not_beat_the_anchor(self):
        """Анти-якорь чужих номенклатур снимает только НЕподтверждённое."""
        assert values("Штрихкод товара; ИНН 7707083893", "INN") == ["7707083893"]

    def test_unanchored_valid_requisite_still_found(self):
        """Одиночное число с валидной КС и без соседей берётся как и раньше."""
        assert values("Реквизиты: 1027700132195.", "OGRN") == ["1027700132195"]

    def test_passport_anti_anchor_unchanged(self):
        """У PASSPORT якорь встроен в pattern (`anchor:` нет) — правка A7
        («анти-якорь не перебивает якорь») его поведения не касается."""
        assert values("Паспорт качества 45 08 123456", "PASSPORT") == []
        assert values("Паспорт 45 08 123456", "PASSPORT") != []
