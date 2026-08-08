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
  (v)   маска на прозе         -> красный по линии «д»   (этап GATE-2)
  (vi)  недобор/перебор границ -> красный по линии «е»   (этап GATE-2)
  (vii) precision реквизита    -> красный по линии «а»   (этап GATE-2:
        линия «а» расширена с NER-хребта на ВСЕ типы эталона)

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
def _entity(etype, text, found=True, leak=False, under=0, over=0, direction="exact"):
    """Запись эталонной сущности в форме run_measurement.process_doc.

    ЭТАП GATE-2: поле `bnd` (границы по направлению ошибки) присутствует ВСЕГДА
    — линия «е» считает его отсутствие регрессом («нечем мерить»), и зелёная
    пара обязана быть зелёной по всем шести линиям, иначе она не эталон."""
    status = "full" if leak else "none"
    return {
        "type": etype, "category": "", "trick": None, "checksum": None,
        "text": text, "found": found, "exact": found, "full": found,
        "left_trim": False, "right_trim": False,
        "miss_reason": None if found else "nothing", "in_body": True,
        "leaked": leak, "leak_pieces": [], "leak_v1": leak,
        "leak_v2": {"status": status, "fragments": []},
        ML.BND_FIELD: {"under": under, "over": over, "direction": direction},
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
#   ЭТАП INSTR, ЧАСТЬ 2 — "отказано" (outcome == "refused") НЕ "упало"        #
#   (outcome == "crashed"): у харнесса разные коды и разные счётчики          #
# --------------------------------------------------------------------------- #
def _refused_doc(doc_id):
    """Форма честного отказа обработки политикой непрочитанных зон
    (run_measurement.process_doc, allow_lossy=False) — не поломка."""
    return {"outcome": "refused", "doc_id": doc_id, "format": "docx",
            "source": "gen", "n_entities": 0, "refused_zones": []}


def test_refused_is_not_reported_as_crash():
    """Отказ НЕ должен зажигать линию «КРЕШИ» — это разные исходы харнесса."""
    baseline = ML.aggregate_results([_processed_doc("doc_a"), _processed_doc("doc_b")])
    current_refused = ML.aggregate_results([_processed_doc("doc_a"), _refused_doc("doc_b")])

    _, regressions, _ = G.compare(baseline, current_refused, fp_tolerance=0)

    assert not any("КРЕШИ" in r for r in regressions), (
        f"Отказ (refused) ошибочно посчитан крешем: {regressions}"
    )


def test_refused_and_crashed_counted_separately():
    """aggregate_results обязан различать 'crashed' и 'refused' — до фикса оба
    попадали в один и тот же список ('outcome != processed')."""
    agg = ML.aggregate_results([
        _processed_doc("doc_a"), _crashed_doc("doc_b"), _refused_doc("doc_c"),
    ])
    assert agg["crashed"] == ["doc_b"]
    assert agg["refused"] == ["doc_c"]
    assert agg["n_docs_processed"] == 1


def test_new_refused_is_visible_but_does_not_redden():
    """Рост числа отказов виден (warning), но сам по себе гейт не роняет —
    отказ не поломка. Условие 1 (креши) при этом остаётся зелёным.

    Базируется на _green_pair() (masks/bnd заполнены), а не на голом
    _processed_doc: у последнего пустые "masks"/entities сами по себе красят
    линии «в»/«г»/«е» ("нечем мерить"), и это заслонило бы проверяемый сигнал."""
    baseline_results, current_results = _green_pair()
    current_results[1] = _refused_doc(current_results[1]["doc_id"])

    v = G.evaluate(baseline_results, current_results, known_leaks_ids=set())

    assert not v["red"], f"Рост отказов ошибочно уронил гейт: {v['regressions']}"
    assert any("ОТКАЗАНО" in w for w in v["warnings"]), (
        f"Рост отказов не отражён предупреждением: {v['warnings']}"
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
    """ТОЧНОСТЬ нового типа охраняется ДВУМЯ линиями.

    ИЗМЕНЕНО ЭТАПОМ GATE-2, намеренно. Раньше этот тест закреплял ОБРАТНОЕ:
    «линия «а» держит только NER-хребет, реквизиты в неё не входят» (T2_INN
    §1г) — то есть фиксировал слепое место как норму, и точность реквизита
    охранял только общий счёт FP (условие 3), который агрегатный: падение
    точности одного типа в нём тонет. Линия «а» расширена на ВСЕ типы, поэтому
    ожидание перевёрнуто: INN_PER обязан краснеть И по precision тоже."""
    baseline, current = _inn_pair()
    current[0]["false_positives"].append(
        _fp("INN_PER", "18210102010011000110", on_negative="КБК — не ПДн"))

    v = _verdict(baseline, current)

    assert v["red"], "Ложная маска INN_PER на негативе не покраснела: %s" % v
    assert any(r.startswith("(3) FP по негативам") for r in v["regressions"]), _reasons(v)
    assert any(r.startswith("(а) precision[INN_PER]") for r in v["regressions"]), \
        "Линия «а» не охватила реквизитный тип — расширение этапа GATE-2 потеряно: %s" \
        % _reasons(v)


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


# --------------------------------------------------------------------------- #
#        (v) ЛИНИЯ «д» — ПОРЧА ДОКУМЕНТА (маски, не легшие ни на что)          #
#                              этап GATE-2                                     #
# --------------------------------------------------------------------------- #
def test_v_overmask_of_prose_reddens_line_d():
    """Система замаскировала кусок обычного текста: маска не легла ни на
    эталонную сущность, ни на объявленный негатив. До этапа GATE-2 гейт такие
    маски ПЕЧАТАЛ, но не сравнивал — можно было закрыть масками номера пунктов
    договора и куски прозы, оставшись зелёным."""
    baseline, current = _green_pair()
    current[0]["false_positives"].append(_fp("ADDRESS", "3.2"))   # ни негатив, ни cross

    v = _verdict(baseline, current)

    assert v["red"], "Маска на неаннотированной прозе не покраснела: %s" % v
    assert any(r.startswith("(д) over-mask прозы[ADDRESS]") for r in v["regressions"]), \
        "Красный не по линии «д»: %s" % _reasons(v)


def test_v_overmask_growth_of_one_type_not_hidden_by_drop_of_another():
    """То же правило «по каждому типу отдельно», что у leak_v2 и masking:
    рост порчи по ADDRESS не должен гаситься её падением по ORG."""
    baseline, current = _green_pair()
    baseline[0]["false_positives"].append(_fp("ORG", "к"))
    current[0]["false_positives"].append(_fp("ADDRESS", "п. 4.1"))

    v = _verdict(baseline, current)

    assert v["red"], "Рост порчи ADDRESS спрятался за падением ORG: %s" % _reasons(v)
    assert any(r.startswith("(д) over-mask прозы[ADDRESS]") for r in v["regressions"]), \
        _reasons(v)


def test_v_overmask_drop_is_improvement_not_regression():
    """Улучшения гейт не роняют (общее правило файла): стало меньше порчи —
    это плюс, а не красный."""
    baseline, current = _green_pair()
    baseline[0]["false_positives"].append(_fp("ADDRESS", "3.2"))

    v = _verdict(baseline, current)

    assert not v["red"], "Падение over-mask уронило гейт: %s" % _reasons(v)
    assert any("over-mask прозы[ADDRESS]" in i for i in v["improvements"]), v["improvements"]


# --------------------------------------------------------------------------- #
#       (vi) ЛИНИЯ «е» — ГРАНИЦЫ ПО НАПРАВЛЕНИЮ ОШИБКИ (этап GATE-2)           #
# --------------------------------------------------------------------------- #
def test_vi_boundary_under_growth_reddens_line_e():
    """Маска стала КОРОЧЕ эталона: часть значения осталась открытым текстом.
    Это утечка, и она обязана быть видна отдельно от перебора."""
    baseline, current = _green_pair()
    current[0]["entities"][0][ML.BND_FIELD] = {"under": 6, "over": 0, "direction": "shorter"}

    v = _verdict(baseline, current)

    assert v["red"], "Недобор границы не покраснел: %s" % v
    assert any(r.startswith("(е) границы[ORG / недобор") for r in v["regressions"]), \
        "Красный не по недобору линии «е»: %s" % _reasons(v)
    assert any("утечка" in r for r in v["regressions"]), _reasons(v)


def test_vi_boundary_over_growth_reddens_line_e():
    """Маска стала ДЛИННЕЕ эталона: закрыт лишний текст. masking B (линия «г»)
    этого не видит ВООБЩЕ (mc_check_bc: маска шире эталона — не нарушение),
    поэтому до линии «е» перебор мог расти неограниченно при зелёном гейте.
    Проверяем ровно это: линия «г» молчит, линия «е» краснеет."""
    baseline, current = _green_pair()
    current[0]["entities"][0][ML.BND_FIELD] = {"under": 0, "over": 42, "direction": "longer"}

    v = _verdict(baseline, current)

    assert v["red"], "Перебор границы не покраснел: %s" % v
    assert any(r.startswith("(е) границы[ORG / перебор") for r in v["regressions"]), \
        "Красный не по перебору линии «е»: %s" % _reasons(v)
    assert not any(r.startswith("(г)") for r in v["regressions"]), (
        "Тест построен неверно: перебор обязан быть НЕВИДИМ линии «г», иначе он "
        "не доказывает нужду в линии «е»: %s" % _reasons(v))


def test_vi_boundary_trade_under_for_over_is_caught():
    """Размен: недобор упал, перебор вырос. Одно общее число границ такой
    размен принимает молча (этап ADDR-B, §«общая цифра в отчёт не выносится»),
    два раздельных — нет."""
    baseline, current = _green_pair()
    baseline[0]["entities"][0][ML.BND_FIELD] = {"under": 9, "over": 0, "direction": "shorter"}
    current[0]["entities"][0][ML.BND_FIELD] = {"under": 0, "over": 30, "direction": "longer"}

    v = _verdict(baseline, current)

    assert v["red"], "Размен «недобор -> перебор» прошёл молча: %s" % _reasons(v)
    assert any("перебор" in r for r in v["regressions"]), _reasons(v)


def test_vi_boundary_line_says_when_it_has_nothing_to_measure():
    """Дамп без поля границ обязан дать РЕГРЕСС с человеческой причиной, а не
    молчаливый зелёный: линия, которой нечем мерить, — это не «нет промахов»
    (тот же приём, что у masking в compare())."""
    baseline, current = _green_pair()
    for d in baseline:
        for e in d["entities"]:
            e.pop(ML.BND_FIELD)

    v = _verdict(baseline, current)

    assert v["red"], "Baseline без поля границ прошёл зелёным: %s" % v
    assert any("в BASELINE нет поля" in r for r in v["regressions"]), _reasons(v)
    assert any("Пересоберите results_baseline.json" in r for r in v["regressions"]), _reasons(v)


def test_vi_boundary_not_found_entity_does_not_count_as_under():
    """Сущность, у которой маски своего типа НЕТ вовсе, — домен recall/утечки,
    а не границ. Иначе линия «е» двигалась бы от любого изменения recall и
    мерила бы не то, что обещает."""
    baseline, current = _green_pair()
    current[0]["entities"][0][ML.BND_FIELD] = {"under": 11, "over": 0, "direction": "not_found"}

    v = _verdict(baseline, current)

    assert not any(r.startswith("(е)") for r in v["regressions"]), (
        "Ненайденная сущность попала в недобор границ: %s" % _reasons(v))


# --------------------------------------------------------------------------- #
#     (vii) ЛИНИЯ «а» РАСШИРЕНА НА РЕКВИЗИТЫ (этап GATE-2)                     #
# --------------------------------------------------------------------------- #
def test_vii_precision_drop_of_requisite_type_reddens_line_a():
    """До этапа GATE-2 линия «а» держала только ORG/PER/ADDRESS, и точность
    любого реквизита (оба ИНН, ОГРН, КПП, счета, БИК, телефон, e-mail,
    паспорт, СНИЛС, дата рождения, суммы) могла упасть до нуля незамеченной."""
    baseline, current = _green_pair()
    for i in range(5):
        current[1]["false_positives"].append(
            _fp("INN", "не-ИНН %d" % i, on_negative="номер документа, не ИНН"))

    v = _verdict(baseline, current)

    assert v["red"], "Падение precision[INN] не покраснело: %s" % v
    assert any(r.startswith("(а) precision[INN]") for r in v["regressions"]), \
        "Красный не по линии «а» для реквизита: %s" % _reasons(v)


# --------------------------------------------------------------------------- #
#      (viii) ЛИНИЯ «ж» — ЗЕРКАЛО ПОДАВЛЕНИЯ (этап T4, ГЛАВНЫЙ РИСК)           #
# --------------------------------------------------------------------------- #
# АБСОЛЮТНАЯ линия (не дельта к baseline, в отличие от остальных шести):
# эталонная сущность, погашенная отрицательным классом (CLAUSE_REF/ROLE_TERM/
# COLLECTIVE), обязана покрасить гейт при ЛЮБОМ ненулевом счёте — даже если
# baseline тоже был ненулевым (иначе один и тот же дефект мог бы протаскиваться
# из прогона в прогон, не краснея ни разу).
def test_viii_suppressed_gold_reddens_line_zh():
    baseline, current = _green_pair()
    d = _doc("doc_c")
    d["suppressed_gold"] = [{
        "gold_type": "PERSON", "gold_text": "Иванов Пётр", "gold_start": 100, "gold_end": 111,
        "suppressed_type": "PERSON", "suppressor_type": "ROLE_TERM",
        "sup_start": 100, "sup_end": 107,
    }]
    current.append(d)

    v = _verdict(baseline, current)

    assert v["red"], "Погашенная эталонная сущность не покрасила гейт"
    assert any(r.startswith("(ж)") for r in v["regressions"]), \
        "Красный не по линии «ж»: %s" % _reasons(v)


def test_viii_suppressed_gold_reddens_even_when_baseline_also_had_it():
    """Линия АБСОЛЮТНАЯ: не «стало хуже», а «есть хоть один» — иначе один и тот
    же дефект протащился бы из прогона в прогон, не покраснев ни разу."""
    baseline, current = _green_pair()
    sup = [{
        "gold_type": "PERSON", "gold_text": "Иванов Пётр", "gold_start": 100, "gold_end": 111,
        "suppressed_type": "PERSON", "suppressor_type": "ROLE_TERM",
        "sup_start": 100, "sup_end": 107,
    }]
    d_base = _doc("doc_c")
    d_base["suppressed_gold"] = sup
    d_cur = _doc("doc_c")
    d_cur["suppressed_gold"] = sup
    baseline.append(d_base)
    current.append(d_cur)

    v = _verdict(baseline, current)

    assert v["red"], "Погашение прежним по счёту к baseline не покрасило гейт"


def test_viii_safe_suppression_of_non_gold_does_not_redden():
    """Безопасный случай: барьер погасил НЕ-эталонного кандидата (диагностика
    `suppressions` непуста, но `suppressed_gold` — измеренное пересечение с
    эталоном, считает measure_lib.suppressed_gold_entities — пусто). Гейт не
    обязан краснеть на том, что барьер сработал по назначению."""
    baseline, current = _green_pair()
    d = _doc("doc_c")
    d["suppressions"] = [{
        "suppressed_type": "PERSON", "suppressor_type": "ROLE_TERM",
        "segment_id": "p0", "start": 0, "end": 5,
        "gstart": 500, "gend": 505, "seg_ok": True,
    }]
    d["suppressed_gold"] = []
    current.append(d)

    v = _verdict(baseline, current)

    assert not v["red"], "Безопасное подавление (не эталон) покрасило гейт: %s" % _reasons(v)


def test_viii_missing_field_defaults_to_empty_not_crash():
    """Дамп старше этапа T4 (нет поля suppressed_gold вовсе) — не крешит гейт и
    не красит его: линия просто не видит того, чего нет в дампе (симметрично
    остальным диагностическим полям)."""
    baseline, current = _green_pair()  # ни один _doc() здесь не несёт suppressed_gold
    v = _verdict(baseline, current)
    assert not v["red"], _reasons(v)
