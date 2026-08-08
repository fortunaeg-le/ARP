# -*- coding: utf-8 -*-
"""ЭТАП TYPE-FACTORY-2 — авто-тесты фабричных типов ИЗ ИХ ЖЕ ФАЙЛОВ-ОПИСАНИЙ.

Седьмое «место» ручной работы при заведении типа (тесты) собирается отсюда
параметризацией: каждый пример из typedefs обязан детектироваться и
маскироваться СВОИМ типом (в том же обрамлении weave, что и в корпусе),
каждый негатив — НЕ получать маску своего типа. Тест живёт в зоне измерителя:
ему можно читать typedefs (стена запрещает это src/, не тестам).
"""
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus_v2"))

import typedef_lib  # noqa: E402
from models import SourceDocument, TextSegment  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")

with open(CONFIG, encoding="utf-8") as _f:
    _DETECTABLE = set(yaml.safe_load(_f)["entity_types"])

_FACTORY = [ft for ft in typedef_lib.load_factory_types()
            if ft["type"] in _DETECTABLE]


def _mask(text):
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt",
                         source_path="<typedef-smoke>")
    entities = run_detection(doc, CONFIG)
    anon, kept = tokenize(doc, entities, CONFIG)
    return anon, kept


def _ex_text(ex):
    return ex["text"] if isinstance(ex, dict) else ex


_EX_CASES = [pytest.param(ft, ex, id="%s-ex%d" % (ft["type"], i))
             for ft in _FACTORY for i, ex in enumerate(ft["examples"])]
_NEG_CASES = [pytest.param(ft, ng, id="%s-%s" % (ft["type"], ng.get("kind")))
              for ft in _FACTORY for ng in ft["negatives"]]


@pytest.mark.skipif(not _FACTORY, reason="фабричных типов с generator-секцией нет")
class TestFactoryExamples:

    @pytest.mark.parametrize("ft,ex", _EX_CASES)
    def test_example_is_masked_with_own_type(self, ft, ex):
        text = ft["intro"] + _ex_text(ex) + ft["outro"]
        anon, kept = _mask(text)
        own = [e for e in kept if e.entity_type == ft["type"]]
        assert own, (
            "пример «%s» типа %s не дал ни одной маски своего типа; anon: %r"
            % (_ex_text(ex), ft["type"], anon))
        assert _ex_text(ex) not in anon, (
            "значение выжило в анонимном тексте: %r" % anon)


@pytest.mark.skipif(not _FACTORY, reason="фабричных типов с generator-секцией нет")
class TestFactoryNegatives:

    @pytest.mark.parametrize("ft,ng", _NEG_CASES)
    def test_negative_gets_no_mask_of_own_type(self, ft, ng):
        text = ft["neg_intro"] + ng["text"] + "."
        _, kept = _mask(text)
        own = [e for e in kept if e.entity_type == ft["type"]]
        assert not own, (
            "негатив «%s» (kind=%s, why=%s) получил маску типа %s: %r"
            % (ng["text"], ng.get("kind"), ng.get("why"), ft["type"],
               [e.original_text for e in own]))
