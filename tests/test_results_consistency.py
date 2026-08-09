# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 3.1 — сторож СОСТАВА дампа (долг AUD1-RESULTS-TRUST).

АУДИТ-1 подложил режиму `--results` катастрофу «сняты ВСЕ маски в десяти
документах» и показал: двигается только точность. Полнота, утечка и границы
живут в блоке `entities` той же записи, а маски — в блоке `masks`, и никто их
между собой не сверял. Устаревший или подпорченный дамп давал ложно-зелёную
картину.

Прибор проверяется ПОДЛОЖЕННОЙ ПОЛОМКОЙ, а не рассуждением: каждый тест ниже
ломает дамп ровно одним способом и требует, чтобы сторож это назвал. Плюс
обязательное зеркало: НЕПОПОРЧЕННЫЙ дамп обязан проходить — иначе сторож,
краснеющий всегда, ничего не охраняет.
"""
import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(HERE, "corpus"))

import measure_lib as ML  # noqa: E402


def _record():
    """Согласованная запись документа — та же форма, что пишет run_measurement."""
    return {
        "outcome": "processed", "doc_id": "probe_0001",
        "n_detected": 2,
        "doc_leaked": False, "doc_leaked_v2": False,
        "entities": [
            {"type": "PER", "found": True, "leaked": False,
             "leak_v2": {"status": "none", "fragments": []}},
            {"type": "INN", "found": True, "leaked": False,
             "leak_v2": {"status": "none", "fragments": []}},
        ],
        "masks": [
            {"gtype": "PER", "scored": True, "b_ok": True, "c_ok": True},
            {"gtype": "INN", "scored": True, "b_ok": True, "c_ok": True},
        ],
        "false_positives": [],
    }


def test_clean_dump_passes():
    """ЗЕРКАЛО. Сторож, краснеющий на исправном дампе, бесполезен."""
    assert ML.check_results_consistency([_record()]) == []


def test_all_masks_removed_is_caught():
    """ПОДЛОЖЕННАЯ ПОЛОМКА АУДИТА-1 дословно: масок нет, а полнота цела."""
    r = _record()
    r["masks"] = []
    r["n_detected"] = 0
    problems = ML.check_results_consistency([r])
    assert any("ни одна маска не легла на эталон" in p for p in problems), problems


def test_counter_and_list_disagree():
    r = _record()
    r["n_detected"] = 7
    assert any("n_detected" in p for p in ML.check_results_consistency([r]))


def test_false_positive_without_any_mask():
    r = _record()
    r["masks"] = []
    r["n_detected"] = 0
    r["false_positives"] = [{"gtype": "ORG", "text": "x"}]
    assert any("ложных срабатываний" in p for p in ML.check_results_consistency([r]))


def test_leak_flag_out_of_sync_with_entities():
    r = _record()
    r["entities"][0]["leak_v2"] = {"status": "full", "fragments": ["x"]}
    assert any("doc_leaked_v2" in p for p in ML.check_results_consistency([r]))
    r2 = _record()
    r2["entities"][0]["leaked"] = True
    assert any("doc_leaked" in p for p in ML.check_results_consistency([r2]))


def test_missing_block_is_caught():
    r = _record()
    del r["masks"]
    assert any("нет блока" in p for p in ML.check_results_consistency([r]))


def test_gate_refuses_to_score_a_broken_dump(tmp_path):
    """СКВОЗЬ ГЕЙТ, а не только через функцию: подложенный дамп обязан
    отменить оценку (код возврата 2), а не дать зелёный отчёт."""
    import json
    import gate

    if not os.path.isfile(gate.BASELINE):
        pytest.skip("нет точки отсчёта — гейту не с чем сравнивать")
    baseline = json.load(open(gate.BASELINE, encoding="utf-8"))
    broken = copy.deepcopy(baseline[:10])
    for r in broken:
        if r.get("outcome") == "processed":
            r["masks"] = []
            r["n_detected"] = 0
    path = tmp_path / "broken.json"
    json.dump(broken, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    assert gate.main(["--results", str(path)]) == 2
