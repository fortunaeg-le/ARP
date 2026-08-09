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
    """(а) ПЕРЕВЁРНУТО ЭТАПОМ E (мультиспан, 2026-07-23). Исторически этот тест
    пинил ЖИВУЮ ЧАСТИЧНУЮ УТЕЧКУ: счёт «408028104\\n45145901597» (p33, разорван
    переносом строки) не детектировался, 11 цифр доживали до анонимного текста, и
    v2 обязан был показать partial. Мультиспан-сборщик этапа E (\\n-рваные счета
    под якорем) собирает этот счёт ЦЕЛИКОМ -> утечки больше нет, тест теперь
    ПРИШПИЛИВАЕТ ФИКС (красный = сборка сломалась, утечка вернулась).
    Способность МЕТРИКИ видеть partial по-прежнему покрыта модульным зеркалом
    test_v2_numeric_partial_fragment выше (без конвейера, не зависит от детекции)."""
    ents = _accounts(agency_0002_record, "40802810445145901597")
    assert ents, "счёт 40802810445145901597 не найден в gold-записи"
    for e in ents:
        assert e["leak_v2"]["status"] == "none", (
            "рваный счёт снова утёк — мультиспан-сборка этапа E сломалась")
        assert e["leak_v2"]["fragments"] == []


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


def test_agency_0003_birthdate_and_snils_are_detected_and_do_not_leak(agency_0003_record):
    """ПРАВКА ЭТАПА 3. Раньше это было зеркало «типа без детектора»: BIRTHDATE и SNILS
    не детектировались, проходили в анонимный текст целиком, и тест ПРИШПИЛИВАЛ
    честный ответ метрики — full. Этап 3 дал обоим типам детектор, и такого зеркала
    в корпусе больше не существует (типов без детектора не осталось).

    Тест развёрнут в свою противоположность и остаётся пришпиливающим: оба типа
    обязаны детектироваться (found) и НЕ доживать до анонимного текста (none).
    Способность ветки метрики сказать «full» — она и была смыслом старого зеркала —
    проверяется на уровне юнита выше (test_leak_v2_birthdate_*, строка с
    leak_v2_numeric на СНИЛС) и в
    test_future_contracts.TestStage3BirthdateHasNoDetector.test_mirror_birthdate_metric_discriminates,
    где положительная сторона берётся из ИСХОДНОГО текста, а не из дыры."""
    for t in ("BIRTHDATE", "SNILS"):
        ents = [e for e in agency_0003_record["entities"] if e["type"] == t]
        assert ents, f"{t} не найден в gold-записи agency_0003"
        for e in ents:
            assert e["found"] is True, f"{t} перестал детектироваться — регресс этапа 3"
            assert e["leak_v2"]["status"] == "none", (
                f"{t} дожил до анонимного текста: {e['leak_v2']}")


def test_no_entity_is_unclassified(agency_0003_record):
    """ИНВАРИАНТ 0b-fix: у присутствующей сущности нет статуса «неприменимо».
    Каждая получает none/partial/full; консервативный fallback помечается явно."""
    for e in agency_0003_record["entities"]:
        v2 = e["leak_v2"]
        assert v2["status"] in ("none", "partial", "full"), e
        assert not v2.get("unclassified"), (
            f"тип {e['type']} не разобран ни одной веткой leak_v2 — "
            f"сработал консервативный fallback")


# --------------------------------------------------------------------------- #
#   Починка МЕТРИКИ ADDRESS: границы слов + усечение (закрывает 0c-H/Stage2-N)  #
# --------------------------------------------------------------------------- #
# Прибор врал В ОБЕ СТОРОНЫ, поэтому зеркал ровно четыре — по два на направление.
# Без всех четырёх нельзя отличить починенную метрику от той, что просто стала
# молчаливее (или, наоборот, кричит утечку всегда).
#
#   1. положительное (лечение ЗАВЫШЕНИЯ): полностью замаскированный адрес -> none
#   2. отрицательное, усечение (лечение ЗАНИЖЕНИЯ): выжил «екатеринбур» -> утекло
#   3. отрицательное, полное: топоним/индекс выжил целым словом -> утекло
#   4. граница-одиночка: выжил только голый номер дома -> НЕ утекло

_ADDR = "620014, г. Екатеринбург, ул. Кутузовский, д. 23, кв. 58"


def _addr_leak(gold_text, anon_text):
    return ML.leak_v2_address(gold_text, ML.v2_norm_text(anon_text),
                              ML.v2_digit_runs(anon_text))


def test_addr_metric_substring_is_not_a_leak():
    """(1) Лечение ЗАВЫШЕНИЯ, юнит: ни один компонент не выжил ЦЕЛЫМ словом.
    «пер» сидит внутри «передать», номер дома «23» — внутри года «2024»,
    индекс — внутри длинного счёта. Подстрочная метрика звала это утечкой."""
    anon = "договор [address_1] от 12 марта 2024 г. обязуется передать счёт 620014620014"
    r = _addr_leak("141300, г. Сергиев Посад, пер. Ленина, д. 23, кв. 12", anon)
    assert r["status"] == "none", f"подстрока засчитана утечкой: {r}"


def test_addr_metric_truncated_toponym_is_a_leak():
    """(2) Лечение ЗАНИЖЕНИЯ, юнит: спан начат на символ позже, «екатеринбур»
    (11 из 12 букв) остался открытым текстом целым словом — город УТЁК.
    Старая метрика сверяла токен целиком и говорила «не утекло»."""
    anon = "юридический адрес: екатеринбур[address_7], тел. [phone_1]"
    r = _addr_leak(_ADDR, anon)
    assert r["status"] != "none", "усечённый топоним не замечен — метрика занижает"
    assert ML.v2_norm_text("екатеринбур") in r["fragments"]


def test_addr_metric_whole_word_toponym_is_a_leak():
    """(3) Отрицательное, полное: топоним выжил целым словом -> утекло."""
    r = _addr_leak(_ADDR, "адрес: екатеринбург, строителей 16")
    assert r["status"] != "none"
    assert ML.v2_norm_text("екатеринбург") in r["fragments"]


def test_addr_metric_bare_house_number_is_not_a_leak():
    """(4) Граница-одиночка: выжил ТОЛЬКО голый номер дома, без топонима.
    Не утечка: голый номер никого не идентифицирует (438 из 447 адресов-одиночек
    этапа 0b-fix — ровно такие). Топоним при этом замаскирован."""
    r = _addr_leak(_ADDR, "объект по адресу [address_1], д. 23, кв. 58")
    assert r["status"] == "none", f"голый номер дома засчитан утечкой: {r}"


def test_addr_metric_index_alone_is_a_leak():
    """Индекс — значимый компонент сам по себе (в отличие от номера дома):
    6 цифр как ЦЕЛЫЙ run адресуют дом с точностью до района."""
    r = _addr_leak(_ADDR, "почтовый индекс 620014, прочее [address_1]")
    assert r["status"] != "none"
    assert "620014" in r["fragments"]


def test_addr_metric_prefix_threshold_rejects_short_stem():
    """Порог префикса (>=6 букв И >=75% длины) обоснован численно: короткая
    основа «нов» — обычное слово прозы, а не утёкший «новосибирск»."""
    r = _addr_leak("630099, г. Новосибирск, наб. Мира, д. 27",
                   "нов [address_1] от нового образца")
    assert r["status"] == "none", f"короткая основа принята за топоним: {r}"


@pytest.fixture(scope="module")
def supply_0001_record():
    import run_measurement as RM
    gold = json.load(open(os.path.join(CORPUS, "gold.json"), encoding="utf-8"))
    d = next(x for x in gold if x["doc_id"] == "supply_0001")
    return RM.process_doc(d)


def test_supply_0001_masked_address_is_not_a_leak(supply_0001_record):
    """(1) то же зеркало НА РЕАЛЬНОМ ДОКУМЕНТЕ, полным конвейером.
    Адрес «141300, г. Сергиев Посад, пер. Ленина, д. 7, кв. 12» замаскирован
    ЦЕЛИКОМ ([ADDRESS_*]); старая метрика рапортовала утечку с фрагментами
    ['0','12','7','пер'] — все четыре суть подстроки чужих слов."""
    ents = [e for e in supply_0001_record["entities"] if e["type"] == "ADDRESS"]
    assert ents, "ADDRESS не найден в gold-записи supply_0001"
    for e in ents:
        assert e["leak_v2"]["status"] == "none", (
            f"замаскированный адрес {e['text']!r} считается утёкшим: {e['leak_v2']}")


# --------------------------------------------------------------------------- #
#   Починка ЧИСЛОВОЙ ветки: окно должно быть ОСТАТКОМ значения (закрывает Stage4-A)
# --------------------------------------------------------------------------- #
# Тот же класс завышения, что был в ADDRESS, но в leak_v2_numeric: окно искалось
# по ВСЕМУ цифровому полю документа, поэтому короткая подстрока постороннего
# числа (КБК, БИК внутри корсчёта, общий префикс счетов «40802810») засчитывалась
# утечкой НИКАК не связанного реквизита, замаскированного ЦЕЛИКОМ.
#
# Зеркала парные: каждая «казнь» ложной утечки прикрыта тестом, который
# доказывает, что настоящая утечка того же вида ВЫЖИВАЕТ.

_KBK = "18210101011011000110"


def test_numeric_foreign_number_substring_is_not_a_leak():
    """Ложная: КПП «341821013» замаскирован целиком, но его окно «182101»
    случайно лежит внутри КБК, оставшегося в тексте (КБК — не ПДн)."""
    r = ML.leak_v2_numeric("34 18 21 013", ML.v2_digit_runs(f"КБК {_KBK} прочее"))
    assert r["status"] == "none", f"подстрока КБК засчитана утечкой КПП: {r}"


def test_numeric_shared_account_prefix_is_not_a_leak():
    """Ложная: все расчётные счета банка делят префикс «40802810»; совпадение
    префикса с ДРУГИМ, открытым счётом — не утечка своего счёта."""
    other = "40802810555660918343"
    r = ML.leak_v2_numeric("40802810734041341318", ML.v2_digit_runs(f"р/с {other}"))
    assert r["status"] == "none", f"чужой счёт с тем же префиксом засчитан утечкой: {r}"


def test_numeric_split_remnant_still_leaks():
    """Настоящая (зеркало к первым двум): счёт разорван границей ячейки, хвост
    из 11 цифр стоит отдельным run-ом — это утечка, и она обязана остаться."""
    r = ML.leak_v2_numeric("40802810445145901597",
                           ML.v2_digit_runs("... [kpp_1] 45145901597 pekb ..."))
    assert r["status"] == "partial"
    assert "45145901597" in r["fragments"]


def test_numeric_glued_full_value_still_leaks():
    """Настоящая: ядро дожило ЦЕЛИКОМ, но к нему через пробел приклеилась цифра
    соседнего числа (run длиннее ядра). Полное выживание — утечка по
    определению, сколько бы чужих цифр ни прилипло."""
    r = ML.leak_v2_numeric("30101810200000000593",
                           ML.v2_digit_runs("к/с 30101810200000000593 6"))
    assert r["status"] == "full", f"склеенное полное выживание потеряно: {r}"


def test_numeric_glued_partial_remnant_still_leaks():
    """Настоящая: дожили 16 цифр из 20 плюс одна прилипшая чужая (покрытие
    run-а 94% — выше порога RUN_COVERAGE_MIN)."""
    r = ML.leak_v2_numeric("30101810400000000555",
                           ML.v2_digit_runs("счёт 18104000000005556 далее"))
    assert r["status"] == "partial"
    assert "1810400000000555" in r["fragments"]


def test_run_coverage_threshold_sits_in_the_measured_gap():
    """Порог покрытия run-а обоснован зазором в данных корпуса: реальные утечки
    дают >=90%, совпадения <=73%. Порог обязан лежать между ними."""
    assert 0.73 < ML.RUN_COVERAGE_MIN <= 0.90


@pytest.fixture(scope="module")
def lease_0001_record():
    import run_measurement as RM
    gold = json.load(open(os.path.join(CORPUS, "gold.json"), encoding="utf-8"))
    d = next(x for x in gold if x["doc_id"] == "lease_0001")
    return RM.process_doc(d)


def test_lease_0001_kpp_not_leaked_via_kbk(lease_0001_record):
    """Stage4-A НА РЕАЛЬНОМ ДОКУМЕНТЕ, полным конвейером: КПП «341821013»
    замаскирован целиком, открытым в тексте остался только КБК. Старая метрика
    рапортовала утечку КПП фрагментом «182101» — подстрокой КБК."""
    ents = [e for e in lease_0001_record["entities"] if e["type"] == "KPP"]
    assert ents, "KPP не найден в gold-записи lease_0001"
    for e in ents:
        assert e["leak_v2"]["status"] == "none", (
            f"КПП {e['text']!r} замаскирован, но считается утёкшим: {e['leak_v2']}")


# =========================================================================== #
#  ЭТАП METRIC-FIX — РАЗДРОБЛЕННОЕ ЗНАЧЕНИЕ (долг AUD1-LEAK-SUBTHRESHOLD)      #
# =========================================================================== #
# Прибор мерил утечку самым длинным непрерывным окном и потому НЕ ВИДЕЛ
# значения, разорванного вёрсткой на куски короче порога: «704\n-068» открыт
# целиком, вердикт был «утечки нет». Ниже — обе стороны правила: ловит
# настоящий разрыв и НЕ ловит совпадение.


def test_fragmented_passport_is_seen_as_leak():
    """Паспортный код «704\n-068» открыт полностью — обе половины подряд."""
    anon = "код подразделения 704\n-068, именуемый"
    r = ML.leak_v2_numeric("704068", ML.v2_digit_runs(anon),
                           anon_runs=ML.v2_digit_runs_pos(anon))
    assert r["status"] == "full", r
    assert r["assembled"] == 6
    assert r["assembled_parts"] == [3, 3]


def test_fragmented_kpp_assembles_across_single_char_seam():
    anon = "К\nПП 20832\n1813\nОГ\nРН"
    r = ML.leak_v2_numeric("208321813", ML.v2_digit_runs(anon),
                           anon_runs=ML.v2_digit_runs_pos(anon))
    assert r["status"] == "full"
    assert r["assembled"] == 9


def test_fragmented_branch_needs_adjacency_not_just_pieces():
    """ЗЕРКАЛО. Те же куски, но РАЗВЕДЁННЫЕ по документу, — не утечка.
    Без этого теста правило нельзя отличить от «кричит утечку всегда»:
    сумма кусков без требования соседства давала +753 ложных на корпусе."""
    anon = "704 " + ("прочий текст " * 8) + "068"
    r = ML.leak_v2_numeric("704068", ML.v2_digit_runs(anon),
                           anon_runs=ML.v2_digit_runs_pos(anon))
    assert r["status"] == "none", r


def test_fragmented_branch_has_a_floor_on_piece_length():
    """ЗЕРКАЛО. Одиночные цифры не собираются в значение: без пола длины
    цепочка складывалась из нулей, разбросанных по документу (165 призрачных
    ACCOUNT в замере)."""
    # разделитель — БУКВА: пробел и дефис между цифрами склеиваются в один run
    # (v2_digit_runs), и «1 2 3» было бы непрерывным числом, а не кусками.
    anon = "1x2x3x4x5x6"
    r = ML.leak_v2_numeric("123456", ML.v2_digit_runs(anon),
                           anon_runs=ML.v2_digit_runs_pos(anon))
    assert r["status"] == "none", r
    assert ML.FRAG_MIN_LEN >= 2


def test_fragmented_branch_does_not_touch_continuous_verdicts():
    """УЗОСТЬ в малом: значение, решённое непрерывным окном, обязано дать РОВНО
    ту же запись с anon_runs и без него (полное сличение дампов — отдельно,
    experiments/stage_metric_fix/narrowness.py)."""
    core = "40802810445145901597"
    for anon in (f"счёт {core} в банке", "счёт [account_1] в банке",
                 "... [kpp_1] 45145901597 pekb ..."):
        a = ML.leak_v2_numeric(core, ML.v2_digit_runs(anon))
        b = ML.leak_v2_numeric(core, ML.v2_digit_runs(anon),
                               anon_runs=ML.v2_digit_runs_pos(anon))
        assert a == b, anon


def test_digit_runs_pos_matches_the_string_field():
    """Позиционный разбор обязан давать ТЕ ЖЕ группы, что строковое поле:
    разъехались бы — раздроблённая ветвь мерила бы не то, что непрерывная."""
    for s in ("4080 2810-4451", "123 abc 456", "К\nПП 20832\n1813",
              "сумма 893 033,00 руб.", "10.07.1996", "704\n-068"):
        assert [r[0] for r in ML.v2_digit_runs_pos(s)] == ML.v2_digit_runs(s).split()


def test_harness_passes_positions_to_the_metric():
    """СТОРОЖ ПРОВОДКИ. Раздроблённая ветвь неприменима без anon_runs — если
    харнесс перестанет их передавать, метрика тихо вернётся к слепоте."""
    import inspect
    import run_measurement as RM
    src = inspect.getsource(RM.process_doc)
    assert "anon_runs = ML.v2_digit_runs_pos(anon_text)" in src
    assert "anon_runs=anon_runs" in src
    assert "anon_runs=anon_runs" in inspect.getsource(RM.leak_v2)


def test_strict_line_counts_assembled_length():
    """Строгий порог (>=8) берёт ОТКРЫТУЮ длину, а не длину непрерывного окна:
    счёт, открытый двумя кусками, — тяжёлая утечка ровно так же."""
    hit6, hit8 = ML._leak_v2_hits("KPP", {"status": "full", "window_len": 5,
                                          "core_len": 9, "assembled": 9})
    assert hit6 and hit8
    hit6, hit8 = ML._leak_v2_hits("KPP", {"status": "partial", "window_len": 6,
                                          "core_len": 9})
    assert hit6 and not hit8
