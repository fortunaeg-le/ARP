# -*- coding: utf-8 -*-
"""ЭТАП LAYOUT — стражи ремонтного прохода (вёрстка разрушает значение).

Три обязательных стража по приёмке владельца:
  1. МОНОТОННОСТЬ ИСПОЛНЯЕМЫМ ТЕСТОМ, не свойством конструкции: ни одна
     сущность основного прохода не исчезает и не двигает границы; проверка —
     сравнение наборов до/после на корпусных документах (TestMonotonicity).
     Причина: в A6 «улучшение» метрики оказалось потерей значения.
  2. ЛОЖНЫЕ НА СКЛЕЙКЕ: отрицательные примеры, где удаление разрыва даёт
     правдоподобную фамилию/топоним, — не маскируются (TestGlueNegatives).
  3. Сам механизм: рваные вёрсткой значения находятся (позитивы), стопка двух
     целых значений не сшивается, настоящая граница блоков не лечится.
"""
import glob
import os

import pytest

import layout_repair
from extractor import extract
from models import SourceDocument, TextSegment
from pipeline import run_detection
from tokenizer import _detect_boundary_entities, tokenize

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(_PROJECT_ROOT, "entity_types.yaml")
_DOCS_DIR = os.path.join(_PROJECT_ROOT, "tests", "corpus", "docs")


def _doc(lines) -> SourceDocument:
    segs = [TextSegment(id=f"l{i}", text=t, source_type="txt_line",
                        metadata={"line_index": i}) for i, t in enumerate(lines)]
    return SourceDocument(segments=segs, source_format="txt", source_path="t.txt")


def _detect_all(doc):
    prim = run_detection(doc, CONFIG)
    extra = _detect_boundary_entities(doc, CONFIG, prim)
    return prim, extra


# --------------------------------------------------------------------------- #
#                       ПОЗИТИВЫ: рваное значение находится                    #
# --------------------------------------------------------------------------- #

class TestHealIntraSegment:
    """Разрыв ВНУТРИ сегмента (ячейка docx: w:br / стык абзацев ячейки)."""

    @pytest.mark.parametrize("text,etype,value", [
        ("Подписал: Сидоро\nва И. П., бухгалтер", "PERSON", "Сидоро\nва И. П."),
        ("Подписал: Сидоро ва И. П., бухгалтер", "PERSON", "Сидоро ва И. П."),
        ("почта: qwerty@\nrambler.ru", "EMAIL", "qwerty@\nrambler.ru"),
        ("почта: qw erty@ram bler.ru", "EMAIL", "qw erty@ram bler.ru"),
        ("тел.: +7 (49\n5) 111-22-33", "PHONE", "+7 (49\n5) 111-22-33"),
        ("БИК 044\n525225", "BIK", "044\n525225"),
    ])
    def test_broken_value_found(self, text, etype, value):
        doc = _doc([text])
        prim, _extra = _detect_all(doc)
        hulls = [doc.segments[0].text[e.start:e.end]
                 for e in prim if e.entity_type == etype]
        assert any(value in h or h in value for h in hulls), hulls

    def test_passport_group_break(self):
        doc = _doc(["паспорт серии 40 04 \n123456 выдан ОВД"])
        prim, _ = _detect_all(doc)
        pas = [e for e in prim if e.entity_type == "PASSPORT"]
        assert pas and "123456" in pas[0].original_text

    def test_original_text_is_source_slice(self):
        """Обратимость: original_text — байт-в-байт срез сегмента (с разрывом)."""
        doc = _doc(["почта: qwerty@\nrambler.ru"])
        prim, _ = _detect_all(doc)
        for e in prim:
            assert e.original_text == doc.segments[0].text[e.start:e.end]


class TestHealSeam:
    """Разрыв ГРАНИЦЕЙ СЕГМЕНТОВ — ремонт стыка окна B3, эмит парой половин."""

    @pytest.mark.parametrize("lines,etype,half_a,half_b", [
        (["Договор подписан: Кузнецо", "ва А. М., представитель"],
         "PERSON", "Кузнецо", "ва А. М."),
        (["почта для связи: makarov@gma", "il.com, звонить днём"],
         "EMAIL", "makarov@gma", "il.com"),
        (["контактный телефон +7 (49", "9) 167-19-83, добавочный 5"],
         "PHONE", "+7 (49", "9) 167-19-83"),
        (["БИК банка 04", "9205603, корсчёт далее"],
         "BIK", "04", "9205603"),
        # ЭТАП DEBTS (0c-B): половина A — ЗНАЧЕНИЕ без слова-якоря. До закрытия
        # долга здесь стояло «паспорт серии 62 11 »: якорь входил в спан и
        # уходил под маску. Ремонт стыка сам по себе не изменился — проверка
        # по-прежнему требует ОБЕ половины и их привязку к своим сегментам.
        (["паспорт серии 62 11 ", "029696, выдан ОВД"],
         "PASSPORT", "62 11 ", "029696"),
    ])
    def test_seam_value_found_as_pair(self, lines, etype, half_a, half_b):
        doc = _doc(lines)
        _prim, extra = _detect_all(doc)
        got = {(e.segment_id, e.original_text)
               for e in extra if e.entity_type == etype}
        assert ("l0", half_a) in got and ("l1", half_b) in got, got

    def test_stacked_phones_not_stitched(self):
        """Зеркало (−): два ЦЕЛЫХ телефона в столбик не сшиваются (обе
        стороны целы порознь — страж «обе половины накрыты» = стопка)."""
        doc = _doc(["тел. +7 (495) 217-44-49", "+7 (383) 496-17-58 факс"])
        prim, extra = _detect_all(doc)
        assert len({e.original_text for e in prim
                    if e.entity_type == "PHONE"}) == 2
        assert not extra

    def test_structural_boundary_not_healed(self):
        """Заглавная справа от стыка — новый структурный блок, не продолжение."""
        doc = _doc(["5. ОТВЕТСТВЕННОСТЬ СТОРОН", "Стороны несут ответственность"])
        _prim, extra = _detect_all(doc)
        assert not extra


# --------------------------------------------------------------------------- #
#        СТРАЖ 2 (владелец): склейка даёт правдоподобную фамилию — не маска    #
# --------------------------------------------------------------------------- #

class TestGlueNegatives:
    """Слова, которые при удалении разрыва дают правдоподобную фамилию или
    топоним. Ни PERSON, ни ORG, ни ADDRESS здесь возникнуть не должны — иначе
    «точность не упала» значило бы «корпус такого не содержит»."""

    NEG = [
        ["в городе Иванов", "о шла стройка завода"],
        ["граница Иванов", "ской области проходит южнее"],
        ["посёлок Комаро", "вка стоит у реки"],
        ["за селом Барсуко", "во начинается лес"],
        ["у деревни Петро во росли сосны"],
        ["стоимость выполняемых работ", "Подрядчик оплачивает аванс"],
        ["мы обсуждали виды спор", "та и активного отдыха"],
        ["дело слушалось в Ленин", "ском районном суде города"],
    ]

    @pytest.mark.parametrize("lines", NEG,
                             ids=[f"neg{i}" for i in range(len(NEG))])
    def test_no_person_mask_on_glue(self, lines):
        doc = _doc(lines)
        prim, extra = _detect_all(doc)
        hits = [(e.entity_type, e.original_text) for e in prim + extra
                if e.entity_type in ("PERSON", "ORG", "ADDRESS")]
        assert not hits, hits


# --------------------------------------------------------------------------- #
#        СТРАЖ 1 (владелец): монотонность сравнением наборов на корпусе        #
# --------------------------------------------------------------------------- #

def _corpus_slice() -> list[str]:
    """Детерминированный срез корпусных документов: мутации всех трёх причин
    класса (linebreak/invisible/combo) + канонические, txt и docx."""
    def pick(pattern, n):
        return sorted(glob.glob(os.path.join(_DOCS_DIR, pattern)))[:n]
    files = (pick("*__m2718_linebreak.*", 3) + pick("*__m1337_invisible.*", 2)
             + pick("*__m1337_combo.*", 2) + pick("agency_0001.txt", 1)
             + pick("agency_0002.docx", 1))
    assert len(files) >= 8, files
    return files


def _masked_coverage(doc, entities):
    """(seg_id, позиция символа) -> тип маски по итогам арбитража."""
    cover = {}
    for e in entities:
        for pos in range(e.start, e.end):
            cover[(e.segment_id, pos)] = e.entity_type
    return cover


class TestMonotonicity:
    """Ремонтный проход ТОЛЬКО ДОБАВЛЯЕТ. Сравнение наборов до/после:

      а) уровень детекции: каждая сущность основного прохода (сегментного и
         граничного) присутствует после включения ремонта с ТЕМИ ЖЕ границами
         и типом — исчезновение или сдвиг границ = падение;
      б) уровень арбитража (там жил урок A6): каждый символ, замаскированный
         без ремонта, замаскирован и с ремонтом, тем же типом.
    """

    @pytest.mark.parametrize("path", _corpus_slice(),
                             ids=[os.path.basename(p) for p in _corpus_slice()])
    def test_repair_only_adds(self, path, monkeypatch):
        doc_off = extract(path)
        monkeypatch.setattr(layout_repair, "ENABLED", False)
        prim_off = run_detection(doc_off, CONFIG)
        extra_off = _detect_boundary_entities(doc_off, CONFIG, prim_off)
        _text_off, kept_off = tokenize(doc_off, prim_off, CONFIG)

        monkeypatch.setattr(layout_repair, "ENABLED", True)
        doc_on = extract(path)
        prim_on = run_detection(doc_on, CONFIG)
        extra_on = _detect_boundary_entities(doc_on, CONFIG, prim_on)
        _text_on, kept_on = tokenize(doc_on, prim_on, CONFIG)

        key = lambda e: (e.segment_id, e.start, e.end, e.entity_type)
        missing = ({key(e) for e in prim_off} - {key(e) for e in prim_on})
        assert not missing, f"сущности основного прохода исчезли: {sorted(missing)[:5]}"
        missing_b = ({key(e) for e in extra_off}
                     - {key(e) for e in extra_on} - {key(e) for e in prim_on})
        assert not missing_b, f"граничные сущности исчезли: {sorted(missing_b)[:5]}"

        cov_off = _masked_coverage(doc_off, kept_off)
        cov_on = _masked_coverage(doc_on, kept_on)
        lost = {k: v for k, v in cov_off.items()
                if cov_on.get(k) != v}
        assert not lost, (f"маскированное покрытие потеряно/сменило тип: "
                          f"{sorted(lost.items())[:5]}")


# --------------------------------------------------------------------------- #
#                       Юнит: классификация прогонов вёрстки                    #
# --------------------------------------------------------------------------- #

class TestScanRuns:
    def test_plain_space_never_dropped(self):
        """Обычный пробел — законный разделитель: прогонов нет вовсе."""
        assert layout_repair._scan_runs("Иванов И. И.") == []

    def test_nbsp_between_words_classified_but_person_guarded(self):
        """NBSP между словом и заглавной: прогон есть (regex-класс), но PERSON
        на нём не принимается (заглавная справа — не продолжение слова)."""
        runs = layout_repair._scan_runs("Иванов И.И.")
        assert len(runs) == 1
        _drops, klass, left, right = runs[0]
        assert klass == "strict"
        assert not layout_repair._person_ok(klass, left, right)

    def test_nbsp_next_to_plain_space_not_dropped(self):
        """NBSP вплотную к обычному пробелу — типографика границы слов."""
        assert layout_repair._scan_runs("кв.  50") == []

    def test_newline_in_mixed_run_dropped(self):
        """'40 04 \\n123456': лечится ради '\\n', пробелы прогона выживают."""
        runs = layout_repair._scan_runs("40 04 \n123456")
        assert len(runs) == 1
        drops = runs[0][0]
        assert drops == ["40 04 \n123456".index("\n")]

    def test_double_newline_is_structural(self):
        """'\\n\\n' — пустая строка, граница абзацев по разметке: не лечится."""
        assert layout_repair._scan_runs("titova@rom\n\nashka.ru") == []

    def test_underscore_not_value_edge(self):
        """Линия подписи «____» за переносом не притягивается к значению."""
        runs = layout_repair._scan_runs("x.ru\n_____")
        assert runs == []

    def test_midword_break_person_ok(self):
        runs = layout_repair._scan_runs("Кузнецо\nва А. М.")
        (_drops, klass, left, right) = runs[0]
        assert layout_repair._person_ok(klass, left, right)

    # ------------------------------------------------------------------ #
    #   ЭТАП SEAM-JOIN: разрыв лечится ТОЛЬКО внутри одного токена         #
    # ------------------------------------------------------------------ #

    def test_newline_between_number_and_word_is_a_boundary(self):
        """«…525187\nТел.:» — значение кончилось, началась метка. Склейка
        ломала `\b` паттерна и ОТНИМАЛА находку (16 БИК корпуса)."""
        runs = layout_repair._scan_runs("БИК 044\n525187\nТел.: +7")
        assert [d for d, _k, _l, _r in runs] == [[7]]

    def test_newline_lower_to_upper_is_a_boundary(self):
        """Строчная слева, ЗАГЛАВНАЯ справа — начало нового блока."""
        assert layout_repair._scan_runs("Олеговна\nАдрес регистрации") == []

    def test_newline_inside_number_is_healed(self):
        assert len(layout_repair._scan_runs("044\n525187")) == 1

    def test_homoglyph_digits_stay_one_number(self):
        """Омоглиф цифры считается цифрой: испорченный реквизит остаётся
        числом по обе стороны разрыва и лечится."""
        assert len(layout_repair._scan_runs("149З\nІ1О09843б")) == 1

    def test_surviving_plain_space_is_not_a_glue(self):
        """Прогон с ВЫЖИВШИМ пробелом ничего не склеивает — правило
        однородности сторон к нему не применяется («199 898,00\n руб.»)."""
        assert len(layout_repair._scan_runs("199 898,00\n руб.")) == 1

    def test_tab_inside_word_is_healed(self):
        """Табуляция внутри абзаца (w:tab) — такой же разрыв, как перенос."""
        runs = layout_repair._scan_runs("Макаро\tв Юрий Николаевич")
        assert len(runs) == 1
        (_drops, klass, left, right) = runs[0]
        assert layout_repair._person_ok(klass, left, right)

    def test_tab_between_columns_is_not_healed(self):
        """Та же табуляция как разделитель КОЛОНОК: слово слева, число
        справа — два разных токена, склейки нет."""
        assert layout_repair._scan_runs("ИНН\t7707083893") == []

    def test_two_tabs_are_structural(self):
        """Две табуляции подряд — пропуск ячейки, граница по разметке."""
        assert layout_repair._scan_runs("кол.\t\tцена") == []

    # ------------------------------------------------------------------ #
    #        ЭТАП SEAM-JOIN: РАЗРЯДКА («И в а н о в») — третий вид         #
    # ------------------------------------------------------------------ #

    def test_spread_word_is_joined(self):
        runs = layout_repair._scan_runs("Подписал: И в а н о в И. И.")
        assert [d[0] for d, _k, _l, _r in runs] == [11, 13, 15, 17, 19]
        assert all(k == "spread" for _d, k, _l, _r in runs)

    def test_spread_allows_person_in_upper_case(self):
        """У разрядки форма сама доказывает, что это одно слово, поэтому
        правило «строчная справа» к ней не применяется."""
        runs = layout_repair._scan_runs("П Е Т Р О В А Мария")
        assert runs and all(
            layout_repair._person_ok(k, l, r) for _d, k, l, r in runs)

    def test_three_single_letters_are_speech_not_spread(self):
        """Три односимвольных токена подряд — ещё обычная речь."""
        assert layout_repair._scan_runs("срок и в порядке, установленном") == []
        assert layout_repair._scan_runs("а б в") == []

    def test_spread_of_digits_is_not_this_rule(self):
        """Разрядка цифр — форма записи числа, её разбирает normalizer
        (этап CHAR-NORM). Здесь «1 2 3 4» соседних колонок не склеивается."""
        assert layout_repair._scan_runs("1 2 3 4 5") == []

    def test_address_abbreviations_are_not_spread(self):
        assert layout_repair._scan_runs(
            "г. Пермь, ш. Ленинградская, д. 82, кв. 54") == []
