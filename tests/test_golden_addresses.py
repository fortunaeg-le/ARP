# -*- coding: utf-8 -*-
"""GOLDEN-регресс адресов (волна 2, этап A): полнота спана, а не только детекция.

Главная метрика этапа A из ТЗ — «сколько адресов закрыто ЦЕЛИКОМ» (идентифицирующий
хвост улица/дом/кв/индекс не торчит наружу), важнее скорости. Здесь она закреплена как
регресс-барьер: если правка снова начнёт ронять хвост адреса, эти тесты покраснеют.

Набор и эталонные полные спаны — tests/golden_addresses.py (89 адресов + 16 не-адресов
из разведки). Метрика:
  detection    — хоть один ADDRESS-спан пересекает эталон;
  full-closure — объединение ADDRESS-спанов покрывает эталон целиком (над-закрытие ок);
  на не-адресах — ADDRESS-спанов быть не должно.

База до этапа A (yargy-сканер): detection 89/89, full-closure 29/89, ложных 2/16.
После этапа A (гибрид): detection 89/89, full-closure 89/89, ложных 0/16 — и это тут
зафиксировано как контракт.
"""
import os

import pytest

from models import SourceDocument, TextSegment
from ner_detector import detect_ner
from golden_addresses import address_items, nonaddress_items

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _address_spans(text: str) -> list[tuple[int, int]]:
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<golden>")
    return [(e.start, e.end) for e in detect_ner(doc, _CONFIG) if e.entity_type == "ADDRESS"]


def _covers(spans, gs, ge) -> bool:
    """Объединение spans покрывает [gs, ge) целиком?"""
    covered = [False] * (ge - gs)
    for s, e in spans:
        for i in range(max(s, gs), min(e, ge)):
            covered[i - gs] = True
    return all(covered)


def _overlaps(spans, gs, ge) -> bool:
    return any(max(s, gs) < min(e, ge) for s, e in spans)


_ADDR = address_items()
_NON = nonaddress_items()
_ADDR_IDS = [f"{cat}:{text[:32]}" for cat, text, _ in _ADDR]
_NON_IDS = [f"{cat}:{text[:32]}" for cat, text, _ in _NON]


@pytest.mark.parametrize("cat,text,span", _ADDR, ids=_ADDR_IDS)
def test_address_detected(cat, text, span):
    """Адрес детектируется (хоть один ADDRESS-спан пересекает эталон)."""
    gs = text.index(span)
    assert _overlaps(_address_spans(text), gs, gs + len(span)), f"адрес не найден: {text!r}"


@pytest.mark.parametrize("cat,text,span", _ADDR, ids=_ADDR_IDS)
def test_address_fully_closed(cat, text, span):
    """Адрес закрыт ЦЕЛИКОМ — идентифицирующий хвост не торчит наружу (главная метрика A)."""
    gs = text.index(span)
    spans = _address_spans(text)
    assert _covers(spans, gs, gs + len(span)), (
        f"хвост адреса утёк: эталон={span!r}, закрыто={[text[s:e] for s, e in spans]}"
    )


@pytest.mark.parametrize("cat,text,span", _NON, ids=_NON_IDS)
def test_nonaddress_no_false_positive(cat, text, span):
    """На не-адресе, похожем на адрес, ADDRESS-спанов быть не должно."""
    spans = _address_spans(text)
    assert not spans, f"ложный ADDRESS на не-адресе {text!r}: {[text[s:e] for s, e in spans]}"


def test_golden_aggregate_thresholds():
    """Сводный барьер: детекция 89/89, полнота 89/89, ложных 0/16 (контракт этапа A)."""
    det = full = 0
    for _, text, span in _ADDR:
        gs = text.index(span)
        ge = gs + len(span)
        spans = _address_spans(text)
        det += _overlaps(spans, gs, ge)
        full += _covers(spans, gs, ge)
    fp = sum(1 for _, text, _ in _NON if _address_spans(text))
    assert det == len(_ADDR), f"детекция {det}/{len(_ADDR)}"
    assert full == len(_ADDR), f"полнота спана {full}/{len(_ADDR)}"
    assert fp == 0, f"ложных срабатываний {fp}/{len(_NON)}"
