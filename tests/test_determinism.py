# -*- coding: utf-8 -*-
"""Этап E'' — тест-страж детерминизма детекции.

Находка Eprime-A (docs/FINDINGS.md): на РЕАЛЬНОМ документе (~2500 сегментов,
не в репозитории — real_docs/ политикой владельца не хранится в проекте)
детекция давала разные результаты между прогонами В ОДНОМ ПРОЦЕССЕ (239/226/226
масок), подпись "первый прогон отличается, следующие совпадают" — типичный
след непрогретого пула потоков BLAS (numpy собран с OpenBLAS, до 24 потоков,
см. HANDOFF_STAGE_EPRIME_DETERMINISM). На синтетическом двойнике сопоставимого
объёма (tests/fixtures/synthetic_corporate_large.docx, ~5000 сегментов) эффект
НЕ воспроизвёлся ни разу за 10 прогонов ни при какой стратегии усиления — это
само по себе не доказывает отсутствие дефекта на реальных документах. Раз
проблему нельзя показать на этом фикстуре, единственное, что можно застраховать
здесь — РЕГРЕССИЮ: если в код детекции когда-нибудь просочится глобальное
мутируемое состояние (реестр/кэш, не сбрасываемый между документами) или
недетерминированная сортировка, этот тест должен покраснеть первым, а не
через полгода на проде.

Защитная мера (не chasing bug) — src/ner_detector.py и src/syntax_compound.py
фиксируют число потоков BLAS (OMP/OPENBLAS/MKL/NUMEXPR=1) до импорта natasha и
прогревают модели фиктивным текстом при импорте модуля, чтобы "холодный" первый
вызов не мог отличаться от прогретых последующих внутри процесса.
"""
import hashlib
import os

import pytest

from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "synthetic_corporate_large.docx")
_CONFIG = os.path.join(_ROOT, "entity_types.yaml")


def _run_once(doc_path):
    doc = extract(doc_path)
    entities = run_detection(doc, _CONFIG)
    anon, kept = tokenize(doc, entities, _CONFIG)
    sig = tuple(sorted(
        (e.entity_type, e.segment_id, e.start, e.end,
         tuple(tuple(s) for s in (e.spans or [])), e.token)
        for e in kept
    ))
    return len(kept), hashlib.sha256(anon.encode("utf-8")).hexdigest(), sig


@pytest.mark.slow
def test_same_process_repeated_detection_is_byte_identical():
    """N=10 прогонов детекции на одном документе В ОДНОМ ПРОЦЕССЕ обязаны дать
    побайтно идентичный anon-текст и идентичный набор сущностей (тип/сегмент/
    границы/spans/токен) на каждом прогоне."""
    results = [_run_once(_FIXTURE) for _ in range(10)]
    counts = [r[0] for r in results]
    hashes = [r[1] for r in results]
    sigs = [r[2] for r in results]

    assert len(set(counts)) == 1, "число масок разошлось между прогонами: %r" % counts
    assert len(set(hashes)) == 1, "anon-текст (sha256) разошёлся между прогонами"
    assert len(set(sigs)) == 1, "состав/границы сущностей разошлись между прогонами"


def test_two_runs_byte_identical_fast():
    """Быстрая версия (N=2) без @slow — для обычного `pytest -q`."""
    r1 = _run_once(_FIXTURE)
    r2 = _run_once(_FIXTURE)
    assert r1 == r2
