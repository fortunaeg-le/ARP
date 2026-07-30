# -*- coding: utf-8 -*-
"""
ЗЕРКАЛА ОХРАНЫ ЛИНИИ «д» — «планку молча не поднять» (догонка этапа GATE-2).

Линия «д» (порча документа) блокирующая по решению владельца. У блокирующей
линии с нулевым допуском есть известный способ выродиться: поднять планку и
поехать дальше. Механизм против этого (см. `overmask_ledger.py`) держится на
трёх утверждениях, и каждое проверяется здесь, а не обещается:

  1. рост требует обоснования — `promote_baseline.py` без `--author`/`--reason`
     ОТКАЗЫВАЕТСЯ работать и ничего не пишет;
  2. история накапливается — записи только дописываются, прошлые не теряются;
  3. обход виден — правка точки отсчёта руками, мимо промотера, даёт КРАСНЫЙ
     гейт с прямой формулировкой.

Тесты быстрые: без Natasha, без прогона корпуса, реальные файлы проекта не
изменяются (промотер проверяется на пути ОТКАЗА, который пишет только в stdout).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import gate as G  # noqa: E402
import overmask_ledger as OL  # noqa: E402

PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")

GOOD_REASON = ("Этап намеренно расширил маскирование на банки-контрагенты, "
               "которых корпус не размечает; рост разобран поимённо.")


def _numbers(total, per_type):
    return {"total": total, "per_type": dict(per_type)}


def _entry(total, per_type, date="2026-01-01", author="кто-то"):
    return {"date": date, "author": author, "kind": "поднятие уровня",
            "total": total, "per_type": dict(per_type),
            "reason": GOOD_REASON, "evidence": ""}


# --------------------------------------------------------------------------- #
#                 1. РОСТ ТРЕБУЕТ ОБОСНОВАНИЯ                                  #
# --------------------------------------------------------------------------- #
def test_empty_reason_is_refused():
    problems = OL.validate_reason("", "")
    assert any("автор" in p for p in problems), problems
    assert any("обоснование" in p for p in problems), problems


def test_placeholder_reason_is_refused():
    """«ок» — не причина. Иначе требование обоснования выродится в ритуал."""
    problems = OL.validate_reason("ок", "И. Иванов")
    assert any("заглушка" in p for p in problems), problems


def test_real_reason_is_accepted():
    assert OL.validate_reason(GOOD_REASON, "И. Иванов") == []


def test_promoter_refuses_growth_without_justification_and_writes_nothing(tmp_path):
    """Главный тест пункта: попытка поднять планку молча ОТКАЗЫВАЕТ, называет
    выросшие типы и НЕ трогает ни точку отсчёта, ни журнал."""
    baseline_path = os.path.join(HERE, "results_baseline.json")
    before_baseline = os.path.getmtime(baseline_path)
    before_ledger = open(OL.LEDGER, encoding="utf-8").read()

    # дамп с подложенной порчей: 5 масок на неаннотированной прозе
    dump = json.load(open(os.path.join(HERE, "results_gate_current.json"), encoding="utf-8"))
    added = 0
    for d in dump:
        if d.get("outcome") == "processed" and added < 5:
            d["false_positives"].append(
                {"raw_type": "ADDRESS", "gtype": "ADDRESS", "text": "п. 3.2",
                 "on_negative": None, "cross_type": None})
            added += 1
    grown_dump = tmp_path / "grown.json"
    grown_dump.write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        [PY, os.path.join(HERE, "promote_baseline.py"), str(grown_dump)],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    assert proc.returncode != 0, "Планку подняли молча:\n%s" % out
    assert "не обоснован" in out, out
    assert "ADDRESS" in out, "Отказ не назвал, что именно выросло:\n%s" % out
    assert "--reason" in out, "Отказ не сказал, как поступить правильно:\n%s" % out

    assert os.path.getmtime(baseline_path) == before_baseline, \
        "Точка отсчёта изменена на пути ОТКАЗА"
    assert open(OL.LEDGER, encoding="utf-8").read() == before_ledger, \
        "Журнал изменён на пути ОТКАЗА"


# --------------------------------------------------------------------------- #
#                 2. ИСТОРИЯ НАКАПЛИВАЕТСЯ                                     #
# --------------------------------------------------------------------------- #
def test_ledger_appends_and_keeps_history(tmp_path):
    led = str(tmp_path / "ledger.json")
    OL.append(_numbers(330, {"ORG": 322}), date="2026-01-01", author="первый",
              reason=GOOD_REASON, evidence="лог A", kind="заведение линии", path=led)
    OL.append(_numbers(342, {"ORG": 322, "ADDRESS": 20}), date="2026-02-02",
              author="второй", reason=GOOD_REASON, evidence="лог B",
              kind="поднятие уровня", path=led)

    e = OL.entries(led)
    assert len(e) == 2, "История не накапливается: %s" % e
    assert e[0]["total"] == 330 and e[0]["author"] == "первый", \
        "Прошлая запись переписана — движение планки перестало читаться как история"
    assert OL.latest(led)["total"] == 342


def test_real_ledger_describes_current_baseline():
    """Журнал проекта обязан описывать НЫНЕШНЮЮ точку отсчёта. Этот тест —
    страж на будущее: пересборка мимо promote_baseline.py покраснеет здесь же,
    не дожидаясь восьмиминутного прогона корпуса."""
    import measure_lib as ML

    baseline = json.load(open(os.path.join(HERE, "results_baseline.json"), encoding="utf-8"))
    nums = OL.overmask_numbers(ML.precision_by_type(baseline))
    assert OL.check_against_baseline(nums) == [], (
        "Журнал обоснований разошёлся с results_baseline.json — точку отсчёта "
        "двигали мимо promote_baseline.py")


# --------------------------------------------------------------------------- #
#                 3. ОБХОД ВИДЕН ГЕЙТУ                                         #
# --------------------------------------------------------------------------- #
def test_hand_edited_baseline_reddens_the_gate():
    """Обход: правим точку отсчёта руками так, чтобы прогон ей соответствовал.
    По числам линия «д» молчит (330 -> 330 в её собственной логике), и без
    сверки с журналом гейт был бы ЗЕЛЁНЫМ."""
    base = [{"outcome": "processed", "doc_id": "d", "entities": [], "masks": [],
             "false_positives": [{"raw_type": "ADDRESS", "gtype": "ADDRESS",
                                  "text": "п. 3.2", "on_negative": None,
                                  "cross_type": None}] * 12}]
    cur = json.loads(json.dumps(base))

    ledger_says_zero = _entry(0, {})
    v = G.evaluate(base, cur, set(), check_ledger=True, ledger_latest=ledger_says_zero)

    assert v["red"], "Правка точки отсчёта руками прошла зелёной: %s" % v
    assert any("Планка поднята мимо promote_baseline.py" in r for r in v["regressions"]), \
        v["regressions"]


def test_missing_or_empty_ledger_is_a_problem(tmp_path):
    """Уровень без единого обоснования — тоже нарушение: число неизвестного
    происхождения не является принятой нормой."""
    missing = str(tmp_path / "нет-такого-файла.json")
    assert OL.check_against_baseline(_numbers(0, {}), path=missing),         "Отсутствующий журнал не был назван проблемой"

    empty = tmp_path / "пустой.json"
    empty.write_text(json.dumps({"entries": []}), encoding="utf-8")
    problems = OL.check_against_baseline(_numbers(330, {"ORG": 322}), path=str(empty))
    assert problems and "ничем не объяснён" in problems[0], problems


def test_matching_ledger_adds_no_complaint():
    """Контроль: сверка молчит, когда всё сходится, — иначе красные выше ничего
    не доказывают. Проверяется ИМЕННО линия «д»: синтетический дамп заведомо
    краснит другие линии (в нём нет ни масок, ни границ), и общий вердикт тут
    ничего не сказал бы."""
    base = [{"outcome": "processed", "doc_id": "d", "entities": [], "masks": [],
             "false_positives": [{"raw_type": "ORG", "gtype": "ORG", "text": "к",
                                  "on_negative": None, "cross_type": None}]}]
    cur = json.loads(json.dumps(base))

    v = G.evaluate(base, cur, set(), check_ledger=True,
                   ledger_latest=_entry(1, {"ORG": 1}))

    assert not any(r.startswith("(д)") for r in v["regressions"]),         "Сверка пожаловалась на совпадающий журнал: %s" % v["regressions"]
