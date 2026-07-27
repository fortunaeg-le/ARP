# -*- coding: utf-8 -*-
"""
Тест разнообразия форм записи (задача 3 сессии CORPUS-V2).

ОТ ЧЕГО ЗАЩИЩАЕТ. Главный риск порождённого корпуса: генератор пишет и текст,
и эталон. Если он породит сумму одним способом, детектор научится одному
способу и покажет отличные цифры, которые ничего не значат. Поэтому здесь
считается НЕ количество вхождений, а количество РАЗНЫХ ФОРМ ЗАПИСИ на каждый
вид данных — и тест краснеет, называя вид данных.

ПОЧЕМУ ОДНОГО СЧЁТА ФОРМ МАЛО. Проверка «форм не меньше N» обманывается
одним вхождением-заглушкой: форма формально есть, а корпус на 95% состоит из
одной удобной записи. Поэтому порогов ЧЕТЫРЕ, и они разной природы:

  1. ПОЛНОТА. Множество встреченных форм == множество объявленных
     (values.DECLARED_FORMS). Порог: РАВЕНСТВО, не «не меньше».
     Обоснование: список форм задан техническим заданием сессии (подборка
     формулировок из открытых источников), а не выбран «побольше». Если формы
     стало меньше — генератор замолчал; если больше — реестр разъехался с
     тем, что объявлено, и число «разных форм» в отчётах перестало
     что-либо значить.

  2. НЕ ГОЛОДАЕТ. Ни одна форма не встречается реже, чем ТРЕТЬ равномерной
     доли (total/F/3). Обоснование: равномерная доля — 1/F; допуск в три раза
     вниз оставляет генератору свободу (формы раздаются по кругу со
     смещением, точного равенства не бывает), но отсекает форму,
     присутствующую номинально — ровно тот обход, который делает проверку 1
     бессмысленной.

  3. НЕ ДОМИНИРУЕТ. Ни одна форма не занимает больше ТРОЙНОЙ равномерной доли
     (3/F). Обоснование симметрично пункту 2: это и есть случай из ТЗ
     «12 разных форм на 900 вхождений» — вхождений много, а корпус на деле
     учит одному способу записи.

  4. ФОРМЫ ЕСТЬ В ПРОСТОЙ ГРУППЕ. Все формы обязаны встречаться среди
     документов `structure_group == "simple"`. Обоснование: метрики новых
     видов данных меряются именно на простой структуре (задача 4). Форма,
     живущая только в сложной группе, из измерения выпадает молча — потеря
     будет объяснена «потерями чтения», а не отсутствием формы.

ЧЕГО ЭТОТ ТЕСТ НЕ ДЕЛАЕТ. Он не знает, ПОХОЖИ ЛИ формы на реальные договоры.
Он сверяет корпус с реестром форм, а реестр — с техническим заданием. Красная
линия сессии остаётся в силе: цифры на порождённом корпусе — верхняя граница,
а не измерение.
"""
import json
import os
import sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_V2 = os.path.join(ROOT, "tests", "corpus_v2")
sys.path.insert(0, CORPUS_V2)

import values as V  # noqa: E402

GOLD_V2 = os.path.join(CORPUS_V2, "gold_v2.json")

# Пороги (см. шапку). Вынесены константами, чтобы их нельзя было «подкрутить»
# незаметно внутри проверки.
STARVATION_FACTOR = 3.0     # допустимо не реже, чем (1/F)/3
DOMINANCE_FACTOR = 3.0      # допустимо не чаще, чем 3*(1/F)


@pytest.fixture(scope="module")
def gold():
    if not os.path.exists(GOLD_V2):
        pytest.fail(
            "Нет %s. Корпус V2 не собран — соберите:\n"
            "    venv/Scripts/python.exe tests/corpus_v2/generate.py" % GOLD_V2)
    with open(GOLD_V2, encoding="utf-8") as f:
        return json.load(f)


def _forms(gold, type_, group=None):
    """Counter форм записи вида данных (опционально — только по группе)."""
    c = Counter()
    for d in gold:
        if group is not None and d["structure_group"] != group:
            continue
        for e in d["entities"]:
            if e["type"] == type_:
                c[e.get("form")] += 1
    return c


@pytest.mark.parametrize("type_", sorted(V.DECLARED_FORMS))
def test_all_declared_forms_present(gold, type_):
    """Порог 1: множество форм в корпусе == объявленное реестром множество.

    Сверяются именно МНОЖЕСТВА, а не количества: подмена одной формы на
    другую количества не меняет и мимо счётчика прошла бы незамеченной."""
    got = set(_forms(gold, type_))
    want = V.declared_form_ids()[type_]
    assert None not in got, (
        "%s: есть величины без идентификатора формы записи — "
        "разметка неполна, считать разнообразие нельзя." % type_)
    lost, extra = sorted(want - got), sorted(got - want)
    assert not lost and not extra, (
        "ВИД ДАННЫХ %s: корпус разошёлся с реестром форм values.py.\n"
        "  нет в корпусе (генератор перестал порождать): %s\n"
        "  нет в реестре (форма появилась мимо реестра): %s\n"
        "Лечится генератором или реестром, НЕ ослаблением теста."
        % (type_, ", ".join(lost) or "—", ", ".join(extra) or "—"))
    assert len(want) == V.DECLARED_FORMS[type_], (
        "ВИД ДАННЫХ %s: реестр даёт %d форм, а объявлено %d. Число в "
        "values.DECLARED_FORMS — контракт из отчёта сессии; расходиться с "
        "фактическим реестром оно не имеет права."
        % (type_, len(want), V.DECLARED_FORMS[type_]))


@pytest.mark.parametrize("type_", sorted(V.DECLARED_FORMS))
def test_no_form_is_starved(gold, type_):
    """Порог 2: форма-заглушка (одно вхождение «для галочки») недопустима."""
    c = _forms(gold, type_)
    total = sum(c.values())
    f = len(c)
    floor = total / f / STARVATION_FACTOR
    thin = {k: v for k, v in c.items() if v < floor}
    assert not thin, (
        "ВИД ДАННЫХ %s: формы встречаются слишком редко (порог %.1f вхождений "
        "= равномерная доля %.1f / %.0f).\n%s\n"
        "Редкая форма формально закрывает проверку полноты, но корпус на деле "
        "учит другим формам."
        % (type_, floor, total / f, STARVATION_FACTOR,
           "\n".join("    %-34s %d" % (k, v) for k, v in sorted(thin.items()))))


@pytest.mark.parametrize("type_", sorted(V.DECLARED_FORMS))
def test_no_form_dominates(gold, type_):
    """Порог 3: случай из ТЗ — вхождений много, а форма по сути одна."""
    c = _forms(gold, type_)
    total = sum(c.values())
    f = len(c)
    ceiling = total / f * DOMINANCE_FACTOR
    fat = {k: v for k, v in c.items() if v > ceiling}
    assert not fat, (
        "ВИД ДАННЫХ %s: форма доминирует (порог %.1f вхождений = равномерная "
        "доля %.1f x %.0f) при %d вхождениях всего.\n%s\n"
        "Много вхождений одной формы — это НЕ разнообразие: детектор выучит "
        "её и покажет цифры, которые ничего не значат."
        % (type_, ceiling, total / f, DOMINANCE_FACTOR, total,
           "\n".join("    %-34s %d" % (k, v) for k, v in sorted(fat.items()))))


@pytest.mark.parametrize("type_", sorted(V.DECLARED_FORMS))
def test_all_forms_present_in_simple_group(gold, type_):
    """Порог 4: форма, живущая только в сложной группе, из метрик выпадает."""
    everywhere = set(_forms(gold, type_))
    simple = set(_forms(gold, type_, group="simple"))
    missing = sorted(everywhere - simple)
    assert not missing, (
        "ВИД ДАННЫХ %s: формы есть в корпусе, но отсутствуют в группе "
        "«простая структура»: %s.\n"
        "Метрики новых видов данных меряются на простой структуре — эти формы "
        "в измерение не попадут, а их отсутствие спишут на потери чтения."
        % (type_, ", ".join(missing)))


def test_report_form_distribution(gold, capsys):
    """Не проверка, а печать распределения: цифры для отчёта сессии."""
    lines = []
    for t in sorted(V.DECLARED_FORMS):
        c = _forms(gold, t)
        lines.append("%s: %d вхождений, %d разных форм" % (t, sum(c.values()), len(c)))
        for k, v in sorted(c.items()):
            lines.append("    %-34s %4d" % (k, v))
    with capsys.disabled():
        print("\n" + "\n".join(lines))
