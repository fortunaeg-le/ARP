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
