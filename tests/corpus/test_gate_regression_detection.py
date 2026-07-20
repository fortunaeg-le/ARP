# -*- coding: utf-8 -*-
"""
Юнит-тест логики условия 1 гейта (tests/corpus/gate.py::compare) — «появился
креш -> регресс». Ничего в src/ не трогает и НЕ гоняет корпус (быстрый,
секунды): доказывает, что сама логика сравнения множеств `crashed` красит
гейт, независимо от того, кто из двух путей к креш-проверке (pytest -m slow
или gate.py) физически запускался (см. правку docstring gate.py,
EFFICIENCY_AUDIT.md §2.1 — устранение дублирующего полного прогона НЕ должно
ослаблять саму проверку).

Метод: искусственно "ломаем" один документ — строим results ровно в той
форме, в которой run_measurement.run_all() кладёт креш при необработанном
исключении (outcome="crashed", без "entities"/"masks"/"false_positives", см.
run_measurement.py:373-376) — и проверяем, что gate.compare() докладывает
регресс. Затем "откатываем" (results без креша) и проверяем, что гейт снова
зелёный по этому условию.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gate as G  # noqa: E402
import measure_lib as ML  # noqa: E402


def _processed_doc(doc_id):
    """Минимальная запись processed-документа без сущностей — форма,
    достаточная для ML.aggregate_results (крешей условие 1 смотрит только на
    outcome, per_type/fp/masks для этого теста не важны)."""
    return {"outcome": "processed", "doc_id": doc_id, "entities": [],
            "masks": [], "false_positives": []}


def _crashed_doc(doc_id):
    """Форма креша, как её кладёт run_measurement.run_all() при
    необработанном исключении (см. run_measurement.py, except Exception)."""
    return {"outcome": "crashed", "doc_id": doc_id, "error": "RuntimeError('boom')"}


def test_new_crash_is_reported_as_regression():
    baseline = ML.aggregate_results([_processed_doc("doc_a"), _processed_doc("doc_b")])
    # искусственно ломаем doc_b
    current_broken = ML.aggregate_results([_processed_doc("doc_a"), _crashed_doc("doc_b")])

    _, regressions, _ = G.compare(baseline, current_broken, fp_tolerance=0)

    assert any("КРЕШИ" in r and "doc_b" in r for r in regressions), (
        "Условие 1 гейта не заметило новый креш doc_b — регресс-детекция "
        f"сломана. regressions={regressions}"
    )


def test_no_crash_stays_green_on_condition_1():
    baseline = ML.aggregate_results([_processed_doc("doc_a"), _processed_doc("doc_b")])
    # "откатываем" поломку — doc_b снова processed
    current_fixed = ML.aggregate_results([_processed_doc("doc_a"), _processed_doc("doc_b")])

    _, regressions, _ = G.compare(baseline, current_fixed, fp_tolerance=0)

    assert not any("КРЕШИ" in r for r in regressions), (
        f"Ложный креш-регресс без искусственной поломки: {regressions}"
    )
