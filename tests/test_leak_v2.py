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


# --------------------------------------------------------------------------- #
#   Этап 0b-fix: зеркало С ДРУГОЙ СТОРОНЫ — «утекло целиком -> full»           #
# --------------------------------------------------------------------------- #
# Тесты 0b выше пинили «замаскировано -> none» и «частично -> partial», но НЕ
# пинили «утекло целиком -> full».  Из-за этого этап 0c не заметил, что BIRTHDATE
# вообще не доходит до веток leak_v2 и молча выпадает из знаменателя (228 сущностей
# типа, который утекает всегда).  Тесты ниже закрывают дыру симметрично.

def test_v2_birthdate_full_leak_when_date_survives():
    """Дата рождения дожила до анонимного текста целиком -> ОБЯЗАН быть full.
    Ключевой кейс: у типа BIRTHDATE нет детектора, значение проходит насквозь."""
    r = ML.leak_v2_birthdate("10.07.1996", ML.v2_date_field("род. 10.07.1996, г. Тверь"))
    assert r["status"] == "full"
    assert r["fragments"] == ["10071996"]


def test_v2_birthdate_survives_corpus_mutations():
    """Реальные мутированные формы из корпуса (невидимые/омоглифы/перенос/пробел)
    тоже обязаны давать full — иначе метрика слепа именно там, где нужна."""
    for form in ("10.07.1‍996", "15.11.199 6", "15\n.11.1996",
                 "21.О5 .197б", "12.10.\n1965"):
        field = ML.v2_date_field(f"дата рождения {form} г.")
        assert ML.leak_v2_birthdate(form, field)["status"] == "full", form


def test_v2_birthdate_masked_is_none():
    """Зеркало зеркала: замаскированная дата -> none (метрика не «кричит всегда»)."""
    r = ML.leak_v2_birthdate("10.07.1996", ML.v2_date_field("род. [birthdate_1], г. Тверь"))
    assert r["status"] == "none"
    assert r["fragments"] == []


def test_v2_birthdate_year_alone_is_not_leak():
    """Выживший год без дня/месяца — не утечка: он никого не идентифицирует."""
    field = ML.v2_date_field("договор заключён в 1996 году, род. [birthdate_1]")
    assert ML.leak_v2_birthdate("10.07.1996", field)["status"] == "none"


def test_v2_date_field_glues_date_separators():
    """Точка/перенос между цифрами склеиваются (дата — одно значение), буква рвёт.
    Именно этого не умеет v2_digit_runs — там дата рвётся на «10 07 1996»."""
    assert ML.v2_date_field("10.07.1996") == "10071996"
    assert ML.v2_date_field("15\n.11.1996") == "15111996"
    assert ML.v2_date_field("123 abc 456") == "123 456"
    # контраст с общей числовой нормой, ради которой и заведено отдельное поле
    assert ML.v2_digit_runs("10.07.1996") == "10 07 1996"


def test_v2_snils_is_numeric_not_unclassified():
    """SNILS (11 цифр, дефисы+пробел) обязан проходить ЧИСЛОВОЙ веткой и давать
    full, а не проваливаться в «неприменимо»."""
    r = ML.leak_v2_numeric("110-924-374 23", ML.v2_digit_runs("СНИЛС 110-924-374 23"))
    assert r["status"] == "full"
    assert r["core_len"] == 11
    assert r["window_len"] == 11


@pytest.fixture(scope="module")
def agency_0003_record():
    """agency_0003 содержит и BIRTHDATE, и SNILS — оба типа БЕЗ детектора."""
    import run_measurement as RM
    gold = json.load(open(os.path.join(CORPUS, "gold.json"), encoding="utf-8"))
    d = next(x for x in gold if x["doc_id"] == "agency_0003")
    return RM.process_doc(d)


def test_agency_0003_undetected_types_report_full_leak(agency_0003_record):
    """End-to-end зеркало: BIRTHDATE и SNILS детектора не имеют, значения проходят
    в анонимный текст целиком -> leak_v2 обязан вернуть full по обоим."""
    for gtype in ("BIRTHDATE", "SNILS"):
        ents = [e for e in agency_0003_record["entities"] if e["type"] == gtype]
        assert ents, f"{gtype} не найден в gold-записи agency_0003"
        for e in ents:
            assert e["found"] is False, f"{gtype} внезапно детектится — тест устарел"
            assert e["leak_v2"]["status"] == "full", (
                f"{gtype} прошёл в анонимный текст целиком, но метрика "
                f"не считает это утечкой: {e['leak_v2']}")


def test_no_entity_is_unclassified(agency_0003_record):
    """ИНВАРИАНТ 0b-fix: у присутствующей сущности нет статуса «неприменимо».
    Каждая получает none/partial/full; консервативный fallback помечается явно."""
    for e in agency_0003_record["entities"]:
        v2 = e["leak_v2"]
        assert v2["status"] in ("none", "partial", "full"), e
        assert not v2.get("unclassified"), (
            f"тип {e['type']} не разобран ни одной веткой leak_v2 — "
            f"сработал консервативный fallback")
