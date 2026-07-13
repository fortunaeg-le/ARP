# -*- coding: utf-8 -*-
"""Регресс-тесты метрики частичной утечки leak_v2 (этап 0b).

Два обязательных кейса из ЗАДАНИЯ:
  (а) ПОЛОЖИТЕЛЬНЫЙ: agency_0002, счёт 40802810445145901597 — разорван границей
      ячейки, до анонимного текста дожили 11 цифр (45145901597).  v2 ОБЯЗАН
      показать partial-leak с этим фрагментом.  v1 его не видит (leaked=False).
  (б) ЗЕРКАЛЬНЫЙ, отрицательный: счёт 40702810806374902378 в том же документе
      системой полностью замаскирован — v2 ОБЯЗАН показать none.  Без этого кейса
      нельзя отличить исправную метрику от той, что «кричит утечку всегда».

Плюс быстрые модульные проверки нормализатора/окна без запуска конвейера.
"""
import os
import sys
import json

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(HERE, "corpus")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, CORPUS)

import measure_lib as ML  # noqa: E402


# --------------------------------------------------------------------------- #
#             Быстрые модульные проверки (без конвейера, без natasha)          #
# --------------------------------------------------------------------------- #
def test_v2_numeric_partial_fragment():
    """11 выживших цифр 20-значного счёта -> partial, фрагмент найден."""
    field = ML.v2_digit_runs("... [kpp_1] 45145901597 pekb ...")
    r = ML.leak_v2_numeric("40802810445145901597", field)
    assert r["status"] == "partial"
    assert "45145901597" in r["fragments"]


def test_v2_numeric_full_and_none():
    core = "40802810445145901597"
    assert ML.leak_v2_numeric(core, ML.v2_digit_runs(f"счёт {core} в банке"))["status"] == "full"
    assert ML.leak_v2_numeric(core, ML.v2_digit_runs("счёт [account_1] в банке"))["status"] == "none"


def test_v2_digit_runs_glues_intra_number_separators_only():
    # пробел/дефис ВНУТРИ числа склеиваются; буква рвёт run
    assert ML.v2_digit_runs("4080 2810-4451") == "408028104451"
    assert ML.v2_digit_runs("123 abc 456") == "123 456"


def test_v2_per_catches_short_korean_name():
    """«Ким Ен Су» — ни одного токена >=4, v1 слеп; v2 (порог 3) ловит."""
    r = ML.leak_v2_per("Ким Ен Су", ML.v2_norm_text("директор Ким подписал акт"))
    assert r["status"] == "partial"


def test_v2_email_domain_leak():
    r = ML.leak_v2_email("ivan@example.com", ML.v2_norm_text("домен example.com открыт"))
    assert r["status"] in ("partial", "full")


# --------------------------------------------------------------------------- #
#              Обязательные end-to-end кейсы на реальном документе             #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def agency_0002_record():
    """Полный прогон харнесса по agency_0002 (тот же код, что и в замере)."""
    import run_measurement as RM
    gold = json.load(open(os.path.join(CORPUS, "gold.json"), encoding="utf-8"))
    d = next(x for x in gold if x["doc_id"] == "agency_0002")
    return RM.process_doc(d)


def _accounts(rec, text):
    return [e for e in rec["entities"] if e["type"] == "ACCOUNT" and e["text"] == text]


def test_agency_0002_account_partial_leak(agency_0002_record):
    """(а) Положительный: разорванный счёт даёт partial с выжившим фрагментом."""
    ents = _accounts(agency_0002_record, "40802810445145901597")
    assert ents, "счёт 40802810445145901597 не найден в gold-записи"
    for e in ents:
        assert e["leak_v1"] is False, "v1 не должен видеть частичную утечку"
        assert e["leak_v2"]["status"] == "partial"
        assert "45145901597" in e["leak_v2"]["fragments"]


def test_agency_0002_masked_account_no_leak(agency_0002_record):
    """(б) Зеркальный: реально замаскированный счёт даёт none (метрика не врёт)."""
    ents = _accounts(agency_0002_record, "40702810806374902378")
    assert ents, "контрольный счёт не найден в gold-записи"
    for e in ents:
        assert e["leak_v2"]["status"] == "none", (
            "полностью замаскированный счёт не должен считаться утёкшим")
        assert e["leak_v2"]["fragments"] == []
