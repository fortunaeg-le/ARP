# -*- coding: utf-8 -*-
"""
САМО-ТЕСТ ЕДИНОГО ГЕЙТА (tests/corpus/gate.py). Быстрый, без Natasha и без
прогона корпуса (секунды): ничего в src/ не трогает.

Зачем. Гейт, который никогда не краснел, — непроверен. Этап 0d покрыл этим
тестом условие 1 (креши); этап D сделал то же для precision в отдельном
файле; этап S2 свёл гейты в один и распространил приём на ВСЕ ЧЕТЫРЕ линии.

Метод один и тот же: берём заведомо зелёную пару (baseline == current),
подкладываем в КОПИЮ current РОВНО ОДИН синтетический регресс и проверяем,
что gate.evaluate() краснеет ИМЕННО по своей линии, а не «вообще». Плюс
контрольный зелёный прогон — иначе тест, который красит всё подряд, тоже
ничего не доказывает.

  (i)   precision одного типа  -> красный по линии «а»
  (ii)  утечка одного типа     -> красный по линии «б»
  (iii) round-trip одной маски -> красный по линии «в»
  (iv)  граница одной маски    -> красный по линии «г»

Отдельно проверяются условие 1 (креши, историческая часть этапа 0d),
условие 5 (известный долг + ДИАГНОСТИКА СОСТАВА: подмена «N на другой N»
обязана дать ПРЕДУПРЕЖДЕНИЕ, не растворяясь в мягком пороге) и мягкость
masking C (решение владельца, STATE §6: печатается, но не роняет).
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gate as G  # noqa: E402
import measure_lib as ML  # noqa: E402


# --------------------------------------------------------------------------- #
#                    Конструкторы синтетических results-записей                #
# --------------------------------------------------------------------------- #
def _entity(etype, text, found=True, leak=False):
    """Запись эталонной сущности в форме run_measurement.process_doc."""
    status = "full" if leak else "none"
    return {
        "type": etype, "category": "", "trick": None, "checksum": None,
        "text": text, "found": found, "exact": found, "full": found,
        "left_trim": False, "right_trim": False,
        "miss_reason": None if found else "nothing", "in_body": True,
        "leaked": leak, "leak_pieces": [], "leak_v1": leak,
        "leak_v2": {"status": status, "fragments": []},
    }


def _mask(gtype, text, a_ok=True, b_ok=True, c_ok=True, scored=True):
    """Запись маски системы (знаменатель masking_correctness и tp в precision)."""
    return {
        "gtype": gtype, "raw_type": gtype, "token": "[%s_1]" % gtype, "text": text,
        "a_ok": a_ok, "a_channel_ok": a_ok, "a_reason": None,
        "scored": scored, "b_ok": b_ok if scored else None,
        "c_ok": c_ok if scored else None,
        "gold_type": gtype if scored else None,
    }


def _fp(gtype, text, on_negative=None, cross_type=None):
    return {"raw_type": gtype, "gtype": gtype, "text": text,
            "on_negative": on_negative, "cross_type": cross_type}


def _doc(doc_id, entities=None, masks=None, fps=None):
    return {
        "outcome": "processed", "doc_id": doc_id, "format": "docx", "source": "gen",
        "contract_type": "", "n_entities": len(entities or []),
        "roundtrip_ok": True, "rt": {"ok": True},
        "entities": entities or [], "masks": masks or [], "false_positives": fps or [],
    }


def _crashed_doc(doc_id):
    """Форма креша, как её кладёт run_measurement.run_all() при
    необработанном исключении (см. run_measurement.py, except Exception)."""
    return {"outcome": "crashed", "doc_id": doc_id, "error": "RuntimeError('boom')"}


def _green_pair():
    """Заведомо зелёная пара (baseline, current): маски корректны, утечек нет,
    FP на негативах нет. Каждый тест портит РОВНО ОДНУ вещь в копии current."""
    results = [
        _doc("doc_a",
             entities=[_entity("ORG", "ООО Ромашка"), _entity("PER", "Иванов Иван"),
                       _entity("PHONE", "+7 999 123-45-67")],
             masks=[_mask("ORG", "ООО Ромашка"), _mask("PER", "Иванов Иван"),
                    _mask("PHONE", "+7 999 123-45-67")]),
        _doc("doc_b",
             entities=[_entity("ADDRESS", "г. Москва, ул. Ленина, д. 5"),
                       _entity("INN", "7707083893")],
             masks=[_mask("ADDRESS", "г. Москва, ул. Ленина, д. 5"),
                    _mask("INN", "7707083893")]),
    ]
    return results, copy.deepcopy(results)


def _verdict(baseline, current, known_leaks=frozenset()):
    return G.evaluate(baseline, current, set(known_leaks))


def _reasons(v):
    return " | ".join(v["regressions"])


# --------------------------------------------------------------------------- #
#             КОНТРОЛЬ: без подложенного регресса гейт ЗЕЛЁНЫЙ                 #
# --------------------------------------------------------------------------- #
def test_identical_run_is_green():
    """Основание всего файла: если гейт красит и без поломки, четыре красных
    ниже ничего не доказывают."""
    baseline, current = _green_pair()
    v = _verdict(baseline, current)
    assert not v["red"], "Гейт красный на идентичных прогонах: %s" % _reasons(v)
    assert not v["warnings"], "Предупреждения на идентичных прогонах: %s" % v["warnings"]


# --------------------------------------------------------------------------- #
#                 (i) ЛИНИЯ «а» — PRECISION одного типа                        #
# --------------------------------------------------------------------------- #
def test_i_precision_drop_reddens_line_a():
    """Подкладываем ORG-маску на ОБЪЯВЛЕННЫЙ негатив: tp прежний, fp_neg +1 ->
    precision[ORG] падает. Это ровно тот класс, к которому masking C слеп
    (FINDINGS D-PRECISION-90) — линия «а» существует ради него."""
    baseline, current = _green_pair()
    current[0]["false_positives"].append(
        _fp("ORG", "Общество", on_negative="generic-noun-not-an-org"))

    v = _verdict(baseline, current)

    assert v["red"], "Падение precision[ORG] не покраснело: %s" % v
    assert any(r.startswith("(а) precision[ORG]") for r in v["regressions"]), \
        "Красный не по линии «а»: %s" % _reasons(v)
    # линия «а» обязана краснеть САМА, не через FP-условие 3 — проверяем, что
    # причина названа precision'ом, а не только ростом FP
    assert any("precision" in r for r in v["regressions"])


# --------------------------------------------------------------------------- #
#                 (ii) ЛИНИЯ «б» — УТЕЧКА одного типа                          #
# --------------------------------------------------------------------------- #
def test_ii_new_leak_reddens_line_b():
    """Одна PHONE-сущность начала утекать (leak_v2 none -> full). Именно это
    условие простояло тёмным пять этапов (ARCHITECTURE_AUDIT §5.1)."""
    baseline, current = _green_pair()
    current[0]["entities"][2]["leak_v2"] = {"status": "full", "fragments": ["9991234567"]}

    v = _verdict(baseline, current)

    assert v["red"], "Новая утечка PHONE не покраснела: %s" % v
    assert any(r.startswith("(б) PHONE[leak_v2>=6]") for r in v["regressions"]), \
        "Красный не по линии «б» для PHONE: %s" % _reasons(v)
    assert any("TOTAL(BIK-excl)" in r for r in v["regressions"]), \
        "Агрегат BIK-excl не заметил рост утечки: %s" % _reasons(v)


def test_ii_leak_growth_of_one_type_not_hidden_by_drop_of_another():
    """Проверка «по каждому типу отдельно»: рост утечки ORG не должен
    прятаться за одновременным падением утечки PER."""
    baseline, current = _green_pair()
    baseline[0]["entities"][1]["leak_v2"] = {"status": "full", "fragments": ["иванов"]}
    baseline[0]["entities"][1]["leak_v1"] = True
    current[0]["entities"][0]["leak_v2"] = {"status": "full", "fragments": ["ромашка"]}

    v = _verdict(baseline, current)

    assert v["red"], "Рост ORG спрятался за падением PER: %s" % v
    assert any(r.startswith("(б) ORG[leak_v2>=6]") for r in v["regressions"]), _reasons(v)


# --------------------------------------------------------------------------- #
#              (iii) ЛИНИЯ «в» — ROUND-TRIP одной маски                        #
# --------------------------------------------------------------------------- #
def test_iii_broken_roundtrip_reddens_line_c():
    """Одна маска перестала восстанавливаться байт-в-байт (a_ok False).
    Асимметрия ошибок (STATE §6): пропуск человек поймает на проверке, кривую
    маскировку — нет, поэтому допуск 0."""
    baseline, current = _green_pair()
    current[0]["masks"][0]["a_ok"] = False
    current[0]["masks"][0]["a_channel_ok"] = False

    v = _verdict(baseline, current)

    assert v["red"], "Сломанный round-trip не покраснел: %s" % v
    assert any(r.startswith("(в) masking_correctness[A round-trip]")
               for r in v["regressions"]), "Красный не по линии «в»: %s" % _reasons(v)


def test_iii_absolute_a_must_be_100_even_if_baseline_was_lower():
    """Дополнительный инвариант линии «в»: A обязана быть РОВНО 100%, иначе
    baseline с A<100 сделал бы порчу значения неотличимой от нормы."""
    baseline, current = _green_pair()
    baseline[0]["masks"][0]["a_ok"] = False      # baseline уже «испорчен»
    current[0]["masks"][0]["a_ok"] = False       # текущий не хуже baseline

    v = _verdict(baseline, current)

    assert v["red"], "A<100%% не покраснела при равном baseline: %s" % v
    assert any("< 100%" in r for r in v["regressions"]), _reasons(v)


# --------------------------------------------------------------------------- #
#               (iv) ЛИНИЯ «г» — ГРАНИЦА одной маски                           #
# --------------------------------------------------------------------------- #
def test_iv_broken_boundary_reddens_line_g():
    """Одна маска перестала закрывать эталонный спан целиком (b_ok False) —
    значение частично осталось наружу, хотя маска на месте."""
    baseline, current = _green_pair()
    current[1]["masks"][0]["b_ok"] = False

    v = _verdict(baseline, current)

    assert v["red"], "Испорченная граница маски не покраснела: %s" % v
    assert any(r.startswith("(г) masking_correctness[B границы]")
               for r in v["regressions"]), "Красный не по линии «г»: %s" % _reasons(v)


def test_iv_per_type_boundary_collapse_not_hidden_by_aggregate_growth():
    """РЕГРЕССИОННЫЙ тест на находку S2-2. Границы ОДНОГО типа обваливаются,
    а агрегат B при этом РАСТЁТ (другой тип подтянулся, и знаменатель
    обвалившегося типа мал). Проверка только по агрегату такой обвал
    пропускает — именно так на дельте 2b -> HEAD прошёл незамеченным
    ADDRESS-B 75.33% -> 51.39% при агрегате 79.59% -> 86.79%."""
    baseline = [
        _doc("doc_a",
             entities=[_entity("ADDRESS", "ул. Ленина, 5")] + [_entity("PER", "П%d" % i) for i in range(10)],
             masks=[_mask("ADDRESS", "ул. Ленина, 5", b_ok=True)]
                   + [_mask("PER", "П%d" % i, b_ok=(i < 5)) for i in range(10)]),
    ]
    current = copy.deepcopy(baseline)
    current[0]["masks"][0]["b_ok"] = False              # ADDRESS-B: 100% -> 0%
    for m in current[0]["masks"][1:]:
        m["b_ok"] = True                                # PER-B: 50% -> 100%

    base_agg = ML.aggregate_results(baseline)
    cur_agg = ML.aggregate_results(current)
    agg_b_before = ML.mc_rates(base_agg["masking_correctness"]["total"])[1]
    agg_b_after = ML.mc_rates(cur_agg["masking_correctness"]["total"])[1]
    assert agg_b_after > agg_b_before, (
        "тест построен неверно: агрегат обязан ВЫРАСТИ, иначе он поймал бы обвал "
        "сам и находка не воспроизводится (%.2f -> %.2f)" % (agg_b_before, agg_b_after))

    v = _verdict(baseline, current)

    assert v["red"], (
        "Обвал границ ADDRESS (100%%->0%%) прошёл молча под выросшим агрегатом "
        "(%.2f%%->%.2f%%) — линия «г» проверяется только по агрегату"
        % (agg_b_before, agg_b_after))
    assert any(r.startswith("(г) masking_correctness[B границы / ADDRESS]")
               for r in v["regressions"]), "Красный не по типу ADDRESS: %s" % _reasons(v)


def test_masking_c_is_soft_warns_but_does_not_redden():
    """Решение владельца (STATE §6): C — мягкий уровень. Он обязан быть ВИДЕН
    (предупреждение), но не ронять прогон: блокирующая роль по типу маски
    отдана precision (линия «а»), которая, в отличие от C, видит over-mask."""
    baseline, current = _green_pair()
    current[0]["masks"][0]["c_ok"] = False

    v = _verdict(baseline, current)

    assert not v["red"], "Мягкий уровень C уронил гейт: %s" % _reasons(v)
    assert any("masking_correctness[C тип]" in w for w in v["warnings"]), \
        "Падение C прошло молча: %s" % v["warnings"]


# --------------------------------------------------------------------------- #
#         УСЛОВИЕ 1 — креши (историческая часть этапа 0d, не менять)           #
# --------------------------------------------------------------------------- #
def _processed_doc(doc_id):
    """Минимальная запись processed-документа без сущностей — форма,
    достаточная для ML.aggregate_results (крешей условие 1 смотрит только на
    outcome, per_type/fp/masks для этого теста не важны)."""
    return {"outcome": "processed", "doc_id": doc_id, "entities": [],
            "masks": [], "false_positives": []}


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


# --------------------------------------------------------------------------- #
#   ЭТАП T2-INN — НОВЫЙ ТИП ПОД ОХРАНОЙ (тип, на котором гейт ни разу не       #
#   краснел, охраняется только на словах)                                      #
# --------------------------------------------------------------------------- #
def _inn_pair():
    """Зелёная пара с ОБОИМИ типами ИНН: организации (10 цифр) и человека (12)."""
    results = [
        _doc("doc_inn",
             entities=[_entity("INN", "7707083893"), _entity("INN_PER", "500100732259")],
             masks=[_mask("INN", "7707083893"), _mask("INN_PER", "500100732259")]),
    ]
    return results, copy.deepcopy(results)


def test_t2_new_inn_type_is_green_when_nothing_changed():
    baseline, current = _inn_pair()
    v = _verdict(baseline, current)
    assert not v["red"], "Гейт красный на идентичных прогонах с INN_PER: %s" % _reasons(v)


def test_t2_leak_of_the_person_inn_reddens_line_b():
    """Утечка ИНН ФИЗЛИЦА обязана краснить линию «б» ИМЕННО под своим именем.
    До этапа T2-INN такой утечки не существовало как отдельного события: она
    растворялась в общей строке INN вместе с ИНН организаций."""
    baseline, current = _inn_pair()
    current[0]["entities"][1]["leak_v2"] = {"status": "full", "fragments": ["500100732259"],
                                            "window_len": 12, "core_len": 12}

    v = _verdict(baseline, current)

    assert v["red"], "Новая утечка INN_PER не покраснела: %s" % v
    assert any(r.startswith("(б) INN_PER[leak_v2>=6]") for r in v["regressions"]),         "Красный не по линии «б» для INN_PER: %s" % _reasons(v)


def test_t2_broken_round_trip_of_the_person_inn_reddens_line_v():
    """Линия «в» по типу: значение нового типа перестало восстанавливаться."""
    baseline, current = _inn_pair()
    current[0]["masks"][1]["a_ok"] = False

    v = _verdict(baseline, current)

    assert v["red"], "Сломанный round-trip INN_PER не покраснел: %s" % v
    assert any("INN_PER" in r and "round-trip" in r for r in v["regressions"]), _reasons(v)


def test_t2_mask_of_the_person_inn_on_a_negative_counts_as_fp():
    """Условие 3 — единственная линия, охраняющая ТОЧНОСТЬ нового типа: линия
    «а» держит только NER-хребет (ORG/PER/ADDRESS), реквизиты в неё не входят
    (см. docs/T2_INN_REPORT §1г). Проверяем, что хотя бы общий счёт FP его
    ловит, — иначе тип не охраняется по точности ВООБЩЕ."""
    baseline, current = _inn_pair()
    current[0]["false_positives"].append(
        _fp("INN_PER", "18210102010011000110", on_negative="КБК — не ПДн"))

    v = _verdict(baseline, current)

    assert v["red"], "Ложная маска INN_PER на негативе не покраснела: %s" % v
    assert any(r.startswith("(3) FP по негативам") for r in v["regressions"]), _reasons(v)
    assert not any(r.startswith("(а) precision[INN_PER]") for r in v["regressions"]),         "Линия «а» неожиданно охватила реквизитный тип — обновите §1г отчёта T2-INN"


# --------------------------------------------------------------------------- #
#      УСЛОВИЕ 5 — известный долг + ДИАГНОСТИКА СОСТАВА (перенос из этапа D)   #
# --------------------------------------------------------------------------- #
def _debt_entity(text):
    """ADDRESS, НЕ детектированный и утекающий — ровно класс known_leaks."""
    return _entity("ADDRESS", text, found=False, leak=True)


def _debt_doc(doc_id, entities):
    """Документ для тестов долга. Маска-«балласт» обязательна: без единой маски
    в baseline линии «в»/«г» законно краснеют («в baseline нет поля masks»), и
    тест мерил бы этот отказ, а не диагностику долга."""
    return _doc(doc_id, entities=entities, masks=[_mask("INN", "7707083893")])


def test_debt_growth_reddens():
    baseline = [_debt_doc("doc_a", [_debt_entity("ул. Ленина, 5")])]
    current = [_debt_doc("doc_a", [_debt_entity("ул. Ленина, 5"),
                                   _debt_entity("ул. Мира, 7")])]

    v = _verdict(baseline, current)

    assert v["red"], "Рост утечки-долга не покраснел: %s" % v
    assert any(r.startswith("(5) утечка-долг") for r in v["regressions"]), _reasons(v)


def test_debt_substitution_at_same_count_warns_not_reddens():
    """Компенсатор мягкого порога: счёт тот же (1 -> 1), но утечка ДРУГАЯ.
    Без этой диагностики подмена «N на другой N» была бы невидима."""
    baseline = [_debt_doc("doc_a", [_debt_entity("ул. Ленина, 5")])]
    current = [_debt_doc("doc_a", [_debt_entity("ул. Мира, 7")])]

    v = _verdict(baseline, current, known_leaks={("doc_a", "ул. Ленина, 5")})

    assert not v["red"], "Подмена состава при том же счёте уронила гейт: %s" % _reasons(v)
    assert any("состав утечки-долга сменился" in w for w in v["warnings"]), \
        "Подмена состава прошла молча: %s" % v["warnings"]
    comp = v["composition"]
    assert comp["joined_undocumented"] == [("doc_a", "ул. Мира, 7")], comp
    assert comp["registry_not_leaking"] == [("doc_a", "ул. Ленина, 5")], comp
