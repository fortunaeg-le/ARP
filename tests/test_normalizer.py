# -*- coding: utf-8 -*-
"""Этап 2 — юнит-тесты нормализатора перед детекцией (src/normalizer.py).

Проверяем ОБА направления:
  * прямое — искажённое значение приводится к канону (омоглиф-цифра сведена,
    невидимые сняты, дефисы внутри числа схлопнуты, латиница→кириллица);
  * ОБРАТНОЕ (защита от порчи) — «Общество» не становится «0бществом», ООО цело,
    двойная фамилия и адрес 12/1 целы, чистая латиница не сводится;
  * КАРТА — offset_map строго возрастает, индексирует исходный текст, а спан,
    отображённый обратно, покрывает исходное искажённое значение (сердце этапа —
    ошибка здесь тихо сдвинула бы все спаны).
"""
import bisect

import pytest

from normalizer import (
    normalize_for_detection,
    norm_to_src,
    src_to_norm,
)

ZWSP = "​"
ZWJ = "‍"
SHY = "­"
NBSP = " "
NNBSP = " "
WJ = "⁠"


def _assert_map_invariant(base, norm, omap):
    """Инвариант карты: длина == len(norm), строго возрастает, индексирует base."""
    assert len(omap) == len(norm)
    assert all(0 <= x < len(base) for x in omap)
    assert all(omap[i] < omap[i + 1] for i in range(len(omap) - 1))


# --------------------------------------------------------------------------- #
# Тождество на неискажённом тексте (гарантия recall_exact на каноне)
# --------------------------------------------------------------------------- #
class TestIdentityOnClean:
    @pytest.mark.parametrize("s", [
        "",
        "обычный чистый текст 12345",
        "ИНН 7707083893 ООО «Ромашка»",
        "Иванов Иван Иванович, +7 (495) 123-45-67",  # дефисы телефон — уже канон
        "г. Москва, ул. Ленина, д. 5, кв. 12",
    ])
    def test_clean_text_unchanged_where_no_collapse(self, s):
        norm, omap = normalize_for_detection(s)
        _assert_map_invariant(s, norm, omap)
        # Телефон с дефисами схлопнется — проверяем только тексты без дефисов
        # между цифрами; для них норма == исходник.
        if "-" not in s:
            assert norm == s
            assert omap == list(range(len(s)))


# --------------------------------------------------------------------------- #
# Прямое: омоглиф-цифра в числовом контексте
# --------------------------------------------------------------------------- #
class TestDigitLookalike:
    def test_cyrillic_ze_in_number_becomes_3(self):
        norm, _ = normalize_for_detection("12З45")   # З = U+0417
        assert norm == "12345"

    def test_cyrillic_o_in_number_becomes_0(self):
        norm, _ = normalize_for_detection("77О7")     # О = U+041E
        assert norm == "7707"

    def test_latin_o_in_number_becomes_0(self):
        norm, _ = normalize_for_detection("77O7")      # O = U+004F (латиница)
        assert norm == "7707"

    def test_various_lookalikes(self):
        # З→3 Ч→4 І→1 внутри числа с >=2 реальными цифрами
        norm, _ = normalize_for_detection("ЧЗІ05")      # -> 43105 (реальные 0,5)
        assert norm == "43105"

    def test_below_two_real_digits_not_converted(self):
        # Порог >=2 реальных цифр — защитный пол: значение, где реальных цифр
        # меньше двух, неотличимо от букв, свод не применяется (осознанно).
        norm, _ = normalize_for_detection("ЧЗІ0")       # 1 реальная цифра -> не сводим
        assert norm == "ЧЗІ0"


# --------------------------------------------------------------------------- #
# ОБРАТНОЕ: свод НЕ должен портить слова/ORG/адрес
# --------------------------------------------------------------------------- #
class TestNoCorruption:
    def test_obshestvo_not_zeroed(self):
        # ГЛАВНАЯ ЛОВУШКА: «О» в «Общество» не должна стать нулём.
        norm, _ = normalize_for_detection("Общество с ограниченной")
        assert norm == "Общество с ограниченной"
        assert "0" not in norm

    def test_ooo_marker_intact(self):
        norm, _ = normalize_for_detection("ООО «Ромашка»")
        assert norm.startswith("ООО")
        assert "0" not in norm

    def test_apartment_letter_2b_intact(self):
        # 'б' на краю числового токена (номер квартиры «2б») НЕ сводится в 6.
        norm, _ = normalize_for_detection("кв. 2б, д. 12б")
        assert "2б" in norm and "12б" in norm
        assert "26" not in norm and "126" not in norm

    def test_house_letter_5a_intact(self):
        norm, _ = normalize_for_detection("д. 5А корп. 2")
        assert "5А" in norm

    def test_double_surname_hyphen_intact(self):
        norm, _ = normalize_for_detection("Иванова-Петрова")
        assert norm == "Иванова-Петрова"

    def test_address_slash_intact(self):
        norm, _ = normalize_for_detection("ул. Мира, д. 12/1")
        assert "12/1" in norm

    def test_single_letter_beside_one_digit_not_converted(self):
        # Один омоглиф рядом с ОДНОЙ реальной цифрой (порог >=2) не сводится.
        norm, _ = normalize_for_detection("серия О5")   # О рядом с одной цифрой
        assert norm == "серия О5"


# --------------------------------------------------------------------------- #
# Невидимые символы
# --------------------------------------------------------------------------- #
class TestInvisibles:
    @pytest.mark.parametrize("inv", [ZWSP, ZWJ, SHY, WJ])
    def test_zero_width_removed_inside_number(self, inv):
        norm, omap = normalize_for_detection("7707" + inv + "083893")
        assert norm == "7707083893"
        _assert_map_invariant("7707" + inv + "083893", norm, omap)

    @pytest.mark.parametrize("sp", [NBSP, NNBSP])
    def test_typographic_space_between_digits_collapsed(self, sp):
        """ЭТАП A5 — контракт УЖЕСТОЧЁН. Было: одиночный NBSP/узкий NBSP между
        цифрами приводится к обычному пробелу и остаётся («паттерн реквизита его
        допускает»). Это верно лишь для паттернов со свободным разделителем
        (ИНН/ОГРН), но НЕ для паттернов с фиксированными группами: у паспорта
        «\\d{6}», у СНИЛС «\\d{3}» — вставленный внутрь такой группы пробел ломал
        матч целиком, и значение уходило в LLM открытым (A5, класс mut:invisible).
        Неразрывный пробел внутри числа значений не РАЗДЕЛЯЕТ — он их склеивает
        при вёрстке, поэтому схлопывается, как дефис. Обычный U+0020 при этом
        по-прежнему сохраняется (тест ниже) — вот там он разделяет группы."""
        base = "7707" + sp + "083893"
        norm, omap = normalize_for_detection(base)
        assert norm == "7707083893"
        _assert_map_invariant(base, norm, omap)

    def test_plain_space_between_digits_kept(self):
        norm, _ = normalize_for_detection("7707 083893")
        assert norm == "7707 083893"

    def test_nbsp_between_words_not_glued(self):
        # NBSP между словами становится пробелом, а НЕ удаляется (иначе склеим ФИО).
        norm, _ = normalize_for_detection("Иван" + NBSP + "Иванов")
        assert norm == "Иван Иванов"


# --------------------------------------------------------------------------- #
# Схлопывание разделителей внутри числа
# --------------------------------------------------------------------------- #
class TestSeparatorCollapse:
    def test_dashes_collapsed(self):
        norm, _ = normalize_for_detection("770-123-45-67")
        assert norm == "7701234567"

    def test_double_space_collapsed(self):
        norm, _ = normalize_for_detection("7707  083893")
        assert norm == "7707083893"

    def test_single_space_kept(self):
        # Одиночный пробел между цифрами НЕ трогаем (его допускает паттерн).
        norm, _ = normalize_for_detection("7707 083893")
        assert norm == "7707 083893"

    def test_hyphen_between_letters_kept(self):
        norm, _ = normalize_for_detection("что-то")
        assert norm == "что-то"


# --------------------------------------------------------------------------- #
# Пустая скобка-ноль — артефакт OCR/распознавания («2()23», «500 ()()()»),
# НЕ скобка с цифрами внутри — код города «(495) …» остаётся скобкой.
# --------------------------------------------------------------------------- #
class TestEmptyParenZero:
    def test_empty_paren_between_digits_becomes_zero(self):
        # «2()23» — дата с пустой скобкой вместо нуля, поймана ПОСЛЕ нормализации.
        norm, omap = normalize_for_detection("2()23")
        _assert_map_invariant("2()23", norm, omap)
        assert norm == "2023"

    def test_phone_with_digits_in_parens_not_touched(self):
        # РЕГРЕСС: «(495) 123-45-67» — скобка НЕ пустая (цифры внутри),
        # телефон ловится ЦЕЛИКОМ включая код города, скобки не съедены.
        base = "(495) 123-45-67"
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm.startswith("(495)")
        s, e = norm_to_src(omap, 0, len(norm))
        assert base[s:e] == base

    def test_empty_paren_without_digit_context_untouched(self):
        # Пустая скобка БЕЗ цифр по соседству — не реквизит, не трогаем.
        base = "см. приложение () к договору"
        norm, _ = normalize_for_detection(base)
        assert norm == base

    def test_trailing_empty_parens_become_zeros(self):
        # «500 ()()()» — хвостовые пустые скобки круглой суммы, цифра есть
        # только ПЕРЕД (после — конец значения, не цифра).
        base = "500 ()()()"
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == "500 000"

    def test_trailing_empty_parens_before_word_become_zeros(self):
        # «500 ()()() рублей» — та же порча в живом предложении, после хвоста
        # не цифра, а слово: гейт по «цифра ПЕРЕД» не требует цифры после.
        base = "500 ()()() рублей"
        norm, _ = normalize_for_detection(base)
        assert norm == "500 000 рублей"

    def test_paragraph_ref_with_digits_in_parens_not_touched(self):
        # РЕГРЕСС: «п. 5(2)» — скобка НЕ пустая (цифра внутри), не реквизит-ноль.
        base = "см. п. 5(2) договора"
        norm, _ = normalize_for_detection(base)
        assert norm == base

    def test_inclusive_vat_parens_not_touched(self):
        # РЕГРЕСС: «(в том числе НДС)» — между скобками текст, не пусто.
        base = "1000 (в том числе НДС)"
        norm, _ = normalize_for_detection(base)
        assert norm == base


# --------------------------------------------------------------------------- #
# Алфавитные омоглифы латиница→кириллица (только в смешанном слове)
# --------------------------------------------------------------------------- #
class TestAlphaFold:
    def test_mixed_word_folded(self):
        # «Aндрей» с латинской A → кириллица.
        norm, _ = normalize_for_detection("Aндрей")   # A = U+0041
        assert norm == "Андрей"

    def test_pure_latin_not_folded(self):
        # Чистая латиница не сводится (иначе FP на настоящем англ. тексте).
        norm, _ = normalize_for_detection("Microsoft Ivanov")
        assert norm == "Microsoft Ivanov"

    def test_pure_cyrillic_unchanged(self):
        norm, _ = normalize_for_detection("Ромашка")
        assert norm == "Ромашка"


# --------------------------------------------------------------------------- #
# КАРТА: обратное отображение спана покрывает исходное искажённое значение
# --------------------------------------------------------------------------- #
class TestOffsetMap:
    def test_phone_span_maps_back_to_distorted_source(self):
        base = "Тел: +7 (495) 12З-45-67 звоните"
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        i = norm.index("+7")
        j = norm.index("1234567") + len("1234567")
        s, e = norm_to_src(omap, i, j)
        assert base[s:e] == "+7 (495) 12З-45-67"

    def test_inn_dash_span_maps_back_including_dashes(self):
        base = "ИНН 7707-083893 конец"
        norm, omap = normalize_for_detection(base)
        i = norm.index("7707083893")
        j = i + len("7707083893")
        s, e = norm_to_src(omap, i, j)
        assert base[s:e] == "7707-083893"

    def test_src_to_norm_roundtrip(self):
        base = "abc 770-123 xyz"
        norm, omap = normalize_for_detection(base)
        # исходный спан числа "770-123"
        s = base.index("770-123")
        e = s + len("770-123")
        ns, ne = src_to_norm(omap, s, e)
        assert norm[ns:ne] == "770123"

    def test_map_end_excludes_trailing_dropped_separator(self):
        # Норм-спан только цифр не должен захватить хвостовой невидимый/разделитель.
        base = "7707" + ZWSP + " остальное"
        norm, omap = normalize_for_detection(base)
        i = norm.index("7707")
        s, e = norm_to_src(omap, i, i + 4)
        assert base[s:e] == "7707"

# --------------------------------------------------------------------------- #
# ЭТАП CHAR-NORM — символьная порча, оставшаяся после этапов 2/A5/A7.
# Каждый случай ниже взят из ДАМПА ГЕЙТА (tests/corpus/results_gate_current.json)
# как значение, ушедшее в LLM ОТКРЫТЫМ: класс mut:homoglyph / mut:invisible.
# --------------------------------------------------------------------------- #
class TestCharNormEdgeB:
    """'б' на краю ДЛИННОГО цифрового токена — искажённая шестёрка, не литера."""

    @pytest.mark.parametrize("base,expected", [
        ("б597769253", "6597769253"),        # ИНН, 'б' в начале
        ("б475Ч972б5", "6475497265"),        # ИНН, 'б' в начале и внутри
        ("55Ч97624870б", "554976248706"),    # ИНН-12, 'б' в конце
        ("4Ч170335б", "441703356"),          # КПП, 'б' в конце
        ("149ЗІ1О09843б", "1493110098436"),  # ОГРН
        ("408028І057753526867б", "40802810577535268676"),  # счёт
    ])
    def test_long_token_edge_b_folded(self, base, expected):
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == expected

    @pytest.mark.parametrize("base", [
        "630099 12б",      # индекс + номер дома с литерой
        "д. 12б, кв. 2б",  # литера дома и квартиры
        "кв. 2б",
    ])
    def test_house_letter_still_intact(self, base):
        """РЕГРЕСС A5: литера дома/квартиры остаётся буквой. Различение — по
        ДЛИНЕ собственного токена: литеру пишут при коротком номере."""
        norm, _ = normalize_for_detection(base)
        assert norm == base


class TestCharNormChainLength:
    """Цепь признаётся реквизитной по ДЛИНЕ в знаках, а не только по числу
    настоящих цифр: код подразделения паспорта и хвост телефона состоят из
    двойников больше чем наполовину и прежний порог не набирали."""

    @pytest.mark.parametrize("base,expected", [
        ("б22-513", "622513"),   # код подразделения, 5 настоящих цифр из 6
        ("30б-273", "306273"),
        ("ЗО6-27З", "306273"),
        ("812-Ч0О", "812400"),
        ("9ЧО-2І9", "940219"),   # настоящих цифр всего 3
    ])
    def test_division_code_folded(self, base, expected):
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == expected

    def test_phone_tail_group_folded(self):
        # «-бО» — хвостовая группа телефона без единой настоящей цифры.
        base = "+7 (812) 79Ч-58-бО"
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == "+7 (812) 7945860"

    def test_ooo_beside_requisite_still_intact(self):
        """РЕГРЕСС A5, главный: «ООО» рядом с длинным реквизитом НЕ становится
        «000». Порог длины цепи расширен, ограничитель группы без цифр (не
        длиннее двух знаков и не приклеена к букве) — на месте."""
        base = "ИНН 7707083893 ООО «Ромашка»"
        norm, _ = normalize_for_detection(base)
        assert norm == base

    def test_zao_beside_account_still_intact(self):
        base = "счёт 40702810 ЗАО «Банк»"
        norm, _ = normalize_for_detection(base)
        assert norm == base


class TestCharNormWeakJoin:
    """Запятая/точка между цифровыми токенами продолжают цепь — копейки суммы
    перестали быть отдельным двузнаковым числом («525,0О» -> «525,00»)."""

    @pytest.mark.parametrize("base,expected", [
        ("73Ч 525,0О руб.", "734 525,00 руб."),
        ("б2З 624,0О руб.", "623 624,00 руб."),
    ])
    def test_kopecks_folded(self, base, expected):
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == expected

    def test_clause_ref_not_touched(self):
        norm, _ = normalize_for_detection("п. 3.2")
        assert norm == "п. 3.2"


class TestCharNormSoftSpaceAtValueEdge:
    """Типографский пробел У ЗНАКА ПУНКТУАЦИИ внутри значения выбрасывается;
    обычный пробел и любое соседство с БУКВОЙ — нет."""

    def test_nbsp_before_dot_in_birthdate(self):
        base = "19.11" + NBSP + ".1962"
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == "19.11.1962"

    def test_soft_space_inside_phone_parens(self):
        base = "+7" + NBSP + " (" + NNBSP + "495) 190-95" + NNBSP + "-63"
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        assert norm == "+7 (495) 1909563"

    @pytest.mark.parametrize("base,expected", [
        ("Иван" + NBSP + "Иванов", "Иван Иванов"),   # буква справа — не трогаем
        ("2023" + NBSP + "г.", "2023 г."),           # буква справа — не трогаем
        ("кв" + NBSP + " " + ZWSP + "50", "кв  50"),  # буква слева — не трогаем
    ])
    def test_letter_neighbour_blocks_drop(self, base, expected):
        norm, _ = normalize_for_detection(base)
        assert norm == expected


class TestBackProjectionHasNoTail:
    """ТРЕБОВАНИЕ ЭТАПА CHAR-NORM. Обратная проекция спана обязана покрывать
    ИСХОДНУЮ искажённую последовательность ЦЕЛИКОМ. Граница маски, легшая
    ВНУТРЬ значения, оставляет открытый хвост — это утечка того же класса, что
    недобор masking B, и на корпусе она видна лишь линией «е»; здесь тот же
    класс закрыт исполняемым тестом на самом нормализаторе."""

    @pytest.mark.parametrize("base", [
        "б597769253",
        "55Ч97624870б",
        "б22-513",
        "408028І057753526867б",
        "770-123-45-67",
        "7707" + NBSP + "083893",
        "661944828" + WJ + "б",
        "19.11" + NBSP + ".1962",
    ])
    def test_span_covers_whole_source_value(self, base):
        """Спан всего норм-текста, отображённый назад, покрывает base целиком:
        ни одного исходного символа значения не остаётся вне маски."""
        norm, omap = normalize_for_detection(base)
        _assert_map_invariant(base, norm, omap)
        s, e = norm_to_src(omap, 0, len(norm))
        assert s == 0, f"хвост СЛЕВА: {base[:s]!r} остался открытым"
        assert e == len(base), f"хвост СПРАВА: {base[e:]!r} остался открытым"

    def test_dropped_char_inside_span_is_covered(self):
        """Выброшенный символ ВНУТРИ значения попадает в исходный спан
        естественно — проверяется посимвольно, а не на глаз."""
        base = "+7 (812) 79Ч-58-бО"
        norm, omap = normalize_for_detection(base)
        ns = norm.index("7945860")
        s, e = norm_to_src(omap, ns, ns + len("7945860"))
        assert base[s:e] == "79Ч-58-бО"

