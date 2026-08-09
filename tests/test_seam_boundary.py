# -*- coding: utf-8 -*-
"""ЭТАП REG — стражи немаскируемого шва в линии «е» (measure_lib).

Правка прибора опасна тем, что легко превращается в послабление метрики: «не
считать недобором» можно расширить на что угодно. Здесь заперты ОБА условия
узости по отдельности, потому что каждое из них поодиночке было бы дырой:

  * только «символ похож на разделитель» — прощался бы перенос строки ВНУТРИ
    абзаца (`w:br`), который система маскировать умеет и обязана;
  * только «позиция ничья» — прощался бы текст сегмента, который харнесс не
    сумел локализовать, то есть настоящая открытая ПДн.

Плюс страж на то, что перебор (`over`) правкой не задет вовсе.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML  # noqa: E402


class _Seg:
    def __init__(self, sid, text):
        self.id = sid
        self.text = text


def test_seam_between_segments_is_seam():
    """'\\n', склеивающий два сегмента, ничьей позиции — это шов."""
    text = "abc\ndef"
    segs = [_Seg(1, "abc"), _Seg(2, "def")]
    offs = {1: 0, 2: 4}
    assert ML.seam_spans(segs, offs, text, 0, len(text)) == [(3, 4)]


def test_tab_between_cells_is_seam():
    """'\\t' между ячейками строки таблицы — тоже шов (PT-1 склеивает им ячейки)."""
    text = "A\tB"
    segs = [_Seg(1, "A"), _Seg(2, "B")]
    offs = {1: 0, 2: 2}
    assert ML.seam_spans(segs, offs, text, 0, len(text)) == [(1, 2)]


def test_linebreak_inside_segment_is_not_seam():
    """ГЛАВНЫЙ страж: перенос ВНУТРИ сегмента (`w:br`) швом НЕ считается.

    Такой символ принадлежит тексту, который система читает, — маска его
    накрывает. Простить его значило бы простить настоящий недобор.
    """
    text = "ab\ncd"
    segs = [_Seg(1, "ab\ncd")]
    offs = {1: 0}
    assert ML.seam_spans(segs, offs, text, 0, len(text)) == []


def test_unlocalized_segment_text_is_not_seam():
    """Второй страж: «ничья» позиция с БУКВОЙ — не шов.

    Сегмент, который не удалось локализовать, оставляет свой текст непокрытым.
    Без проверки символа метрика простила бы открытую ПДн как «шов».
    """
    text = "abc DEF"
    segs = [_Seg(1, "abc"), _Seg(2, "DEF")]
    offs = {1: 0, 2: None}          # второй сегмент не локализован
    assert ML.seam_spans(segs, offs, text, 0, len(text)) == []


def test_seam_removes_only_seam_char_from_under():
    """Значение, разорванное швом и закрытое масками с обеих сторон, — не недобор."""
    golds = [{"start": 0, "end": 7, "type": "PER"}]      # "abc\ndef"
    masks = [(0, 3), (4, 7)]
    without = ML.boundary_by_gold(golds, masks, masks)[0]
    withs = ML.boundary_by_gold(golds, masks, masks, seam=[(3, 4)])[0]
    assert without == {"under": 1, "over": 0, "direction": "shorter"}
    assert withs == {"under": 0, "over": 0, "direction": "exact"}


def test_seam_does_not_touch_over():
    """Перебор считается по маскам; шов маской быть не может — `over` не двигается."""
    golds = [{"start": 2, "end": 5, "type": "PER"}]
    masks = [(0, 9)]                                     # маска шире эталона
    a = ML.boundary_by_gold(golds, masks, masks)[0]
    b = ML.boundary_by_gold(golds, masks, masks, seam=[(5, 6)])[0]
    assert a["over"] == b["over"] == 6
    assert a["direction"] == b["direction"] == "longer"


def test_real_under_survives_seam_forgiveness():
    """Открытый ТЕКСТ внутри эталона остаётся недобором и при наличии шва рядом."""
    golds = [{"start": 0, "end": 10, "type": "PER"}]
    masks = [(0, 3)]                                     # 3..10 открыто
    r = ML.boundary_by_gold(golds, masks, masks, seam=[(3, 4)])[0]
    assert r["under"] == 6 and r["direction"] == "shorter"


def test_seam_chars_are_a_closed_set():
    """Набор разделителей закрыт: пробел сюда не входит и входить не должен."""
    assert set(ML.SEAM_CHARS) == {"\n", "\t"}
    assert " " not in ML.SEAM_CHARS
