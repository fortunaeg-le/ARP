# -*- coding: utf-8 -*-
"""ЗЕРКАЛА метрики precision этапа D (measure_lib.precision_by_type).

Метрика без зеркал — фикция («precision 90%»): надо ДОКАЗАТЬ, что она различает
чистую детекцию и мусор. Для каждого NER-типа хребта (ORG/PER/ADDRESS):
  (+) корректная маска на реальной сущности → precision НЕ падает (не штрафуется);
  (−) маска на ОБЪЯВЛЕННОМ негативе → precision ПАДАЕТ (штрафуется).
Плюс: cross-type и on-nothing маски precision НЕ двигают (диагностика, не гейт), но
их отдельные счётчики растут — иначе over-маскирование прозы тонуло бы молча.

Данные синтетические (не корпус): метрика — чистая функция над записями process_doc.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests", "corpus"))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import measure_lib as ML  # noqa: E402


def _mask(gtype, on_gold_of_type=None):
    """Синтетическая запись маски. on_gold_of_type=T → TP (легла на свой тип);
    =другой тип → cross; None → не легла на gold (негатив/проза — см. _fp)."""
    scored = on_gold_of_type is not None
    return {"gtype": gtype, "scored": scored,
            "c_ok": scored and on_gold_of_type == gtype,
            "b_ok": scored, "a_ok": True, "a_channel_ok": True}


def _fp(gtype, kind):
    """kind: 'neg' (на объявленном негативе), 'cross' (на gold другого типа),
    'nothing' (на неаннотированной прозе)."""
    return {"gtype": gtype, "raw_type": gtype,
            "on_negative": ("сумма — не ПДн" if kind == "neg" else None),
            "cross_type": ("PER" if kind == "cross" else None)}


def _rec(masks, fps):
    return {"outcome": "processed", "doc_id": "syn", "masks": masks,
            "false_positives": fps, "entities": []}


def _prec(masks, fps, t):
    out = ML.precision_by_type([_rec(masks, fps)])
    return out["per_type"][t]


import pytest  # noqa: E402


@pytest.mark.parametrize("T", ML.NER_SPINE_TYPES)
def test_mirror_plus_does_not_penalise(T):
    """(+) Корректная маска на реальной сущности precision НЕ роняет."""
    base = _prec([_mask(T, T)], [], T)                       # 1 TP
    assert base["precision"] == 100.0 and base["fp_neg"] == 0
    plus = _prec([_mask(T, T), _mask(T, T)], [], T)          # +1 TP
    assert plus["precision"] == 100.0, (
        "корректная маска оштрафовала precision — метрика сломана")


@pytest.mark.parametrize("T", ML.NER_SPINE_TYPES)
def test_mirror_minus_penalises(T):
    """(−) Маска на ОБЪЯВЛЕННОМ негативе precision РОНЯЕТ (метрика двигается)."""
    base = _prec([_mask(T, T)], [], T)                       # 1 TP → 100%
    minus = _prec([_mask(T, T)], [_fp(T, "neg")], T)         # +1 FP-neg
    assert minus["fp_neg"] == 1
    assert minus["precision"] == 50.0, (
        "маска на негативе НЕ оштрафовала precision — метрика ЗЕЛЁНАЯ НА МУСОРЕ, СТОП")
    assert minus["precision"] < base["precision"], "метрика не двинулась на (−)"


@pytest.mark.parametrize("T", ML.NER_SPINE_TYPES)
def test_diagnostics_do_not_move_precision(T):
    """cross-type и on-nothing маски precision НЕ двигают (диагностика), но их
    счётчики растут — over-mask прозы виден отдельно, а не молча в precision."""
    base = _prec([_mask(T, T)], [], T)
    withdiag = _prec([_mask(T, T)],
                     [_fp(T, "cross"), _fp(T, "nothing"), _fp(T, "nothing")], T)
    assert withdiag["precision"] == base["precision"] == 100.0, (
        "cross/nothing оштрафовали precision — они должны быть ТОЛЬКО диагностикой")
    assert withdiag["cross"] == 1 and withdiag["nothing"] == 2, (
        "диагностические счётчики не выросли — over-mask утонул бы молча")


def test_fp_neg_matches_aggregate_definition():
    """Единая ось: precision.fp_neg == aggregate_results.fp_on_neg (та же дефиниция)."""
    masks = [_mask("ORG", "ORG"), _mask("PER", "PER")]
    fps = [_fp("ORG", "neg"), _fp("PER", "neg"), _fp("ADDRESS", "neg"),
           _fp("ORG", "cross"), _fp("ORG", "nothing")]
    rec = _rec(masks, fps)
    prec = ML.precision_by_type([rec])
    agg = ML.aggregate_results([rec])
    for t in ("ORG", "PER", "ADDRESS"):
        assert prec["per_type"][t]["fp_neg"] == agg["fp_on_neg"][t], t
    assert prec["fp_neg_total"] == agg["fp_on_neg_total"] == 3
