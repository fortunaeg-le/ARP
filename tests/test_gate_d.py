# -*- coding: utf-8 -*-
"""ЗЕРКАЛА ГЕЙТА D (experiments/stage_d/gate_d.evaluate).

⚠ ЭТОТ ФАЙЛ ПРОВЕРЯЕТ ИСТОРИЮ, А НЕ ТЕКУЩЕЕ СОСТОЯНИЕ (этап GATE-2).
Гейт D ОТСТАВЛЕН: его запуск запрещён (`gate_d.main()` печатает отказ и выходит
с кодом 2), потому что его точка отсчёта фиксирует 650 ложных срабатываний
против фактических 16 — рост в 40 раз он показал бы зелёным. Числа ниже
(`fp_neg_total: 650`, допуск +5) — НЕ действующие пороги проекта, а слепок
этапа D, сохранённый как провенанс.

Тесты живы намеренно: они покрывают `compute_state`/`evaluate`, на которые
ссылается HANDOFF_STAGE_D_PRECISION, и доказывают, что сохранённая история
осталась той самой, а не была тихо переписана. Действующая охрана — шесть линий
`tests/corpus/gate.py` и её собственное зеркало
`tests/corpus/test_gate_regression_detection.py`; ЗЕЛЁНЫЙ прогон ЭТОГО файла
про качество продукта не говорит НИЧЕГО.

Гейт без само-теста непроверен. Подкладываем СИНТЕТИЧЕСКИЙ регресс и доказываем,
что гейт КРАСНЕЕТ (или предупреждает) там, где должен:
  (i)   уронить precision одного типа  → КРАСНЫЙ по 2a;
  (ii)  добавить утечку сверх порога   → КРАСНЫЙ по 2c;
  (iii) подменить состав при том же числе → НЕ красный, но ПРЕДУПРЕЖДЕНИЕ.
+ контроль: чистое состояние (== baseline) → ЗЕЛЁНЫЙ без предупреждений.
+ 2b (FP вырос) → КРАСНЫЙ.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "experiments", "stage_d"))
sys.path.insert(0, os.path.join(HERE, "..", "tests", "corpus"))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import gate_d  # noqa: E402


BASE_IDS = [["docA", "адрес A"], ["docB", "адрес B"], ["docC", "адрес C"]]
BASELINE = {
    "tolerances": {"precision_pp": 0.5, "fp_neg": 5, "missed_leak": 3},
    "precision": {"ORG": 99.77, "PER": 99.96, "ADDRESS": 99.92},
    "fp_neg_total": 650,
    "missed_leak_count": 3,
    "missed_leak_ids": [list(x) for x in BASE_IDS],
}
KNOWN = {("docA", "адрес A"), ("docB", "адрес B"), ("docC", "адрес C")}


def _state(precision=None, fp=650, leak_count=3, leak_ids=None, crashed=None):
    return {
        "precision": precision or dict(BASELINE["precision"]),
        "fp_neg_total": fp,
        "missed_leak_count": leak_count,
        "missed_leak_ids": leak_ids if leak_ids is not None else [list(x) for x in BASE_IDS],
        "crashed": crashed or [],
    }


def test_clean_state_is_green():
    v = gate_d.evaluate(_state(), BASELINE, KNOWN)
    assert not v["red"], v["reasons"]
    assert not v["warnings"]


def test_i_precision_drop_is_red_2a():
    prec = dict(BASELINE["precision"]); prec["ORG"] = 99.0     # −0.77пп > 0.5пп допуск
    v = gate_d.evaluate(_state(precision=prec), BASELINE, KNOWN)
    assert v["red"] and any("2a precision[ORG]" in r for r in v["reasons"]), v


def test_precision_within_tolerance_is_green():
    prec = dict(BASELINE["precision"]); prec["ORG"] = 99.40    # −0.37пп < 0.5пп допуск
    v = gate_d.evaluate(_state(precision=prec), BASELINE, KNOWN)
    assert not v["red"], v["reasons"]


def test_ii_extra_leak_is_red_2c():
    ids = [list(x) for x in BASE_IDS] + [["docNEW", "новый адрес %d" % i] for i in range(5)]
    v = gate_d.evaluate(_state(leak_count=8, leak_ids=ids), BASELINE, KNOWN)  # 8 > 3+3
    assert v["red"] and any("2c утечка-долг" in r for r in v["reasons"]), v


def test_iii_composition_swap_same_count_warns_not_red():
    # тот же счёт (3), но docC ушёл, docZ пришёл — подмена состава
    ids = [["docA", "адрес A"], ["docB", "адрес B"], ["docZ", "подменный адрес"]]
    v = gate_d.evaluate(_state(leak_count=3, leak_ids=ids), BASELINE, KNOWN)
    assert not v["red"], "подмена при том же числе не должна КРАСНИТЬ (мягкий порог): %s" % v["reasons"]
    assert v["warnings"], "подмена состава должна дать ПРЕДУПРЕЖДЕНИЕ"
    assert "leak-set changed" in v["warnings"][0]
    assert v["composition"]["joined"] == [("docZ", "подменный адрес")]
    assert v["composition"]["left"] == [("docC", "адрес C")]
    # docZ не в known_leaks → помечен как недокументированный
    assert v["composition"]["joined_undocumented"] == [("docZ", "подменный адрес")]


def test_2b_fp_grows_is_red():
    v = gate_d.evaluate(_state(fp=700), BASELINE, KNOWN)       # 700 > 650+5
    assert v["red"] and any("2b FP" in r for r in v["reasons"]), v


def test_crash_is_red():
    v = gate_d.evaluate(_state(crashed=["docX"]), BASELINE, KNOWN)
    assert v["red"] and any("КРЕШИ" in r for r in v["reasons"]), v


# --------------------------------------------------------------------------- #
#          ЭТАП GATE-2 — ОТСТАВКА: гейт D не должен уметь выстрелить            #
# --------------------------------------------------------------------------- #
def test_retired_gate_refuses_to_run_and_explains_why():
    """Запуск gate_d.py как проверки обязан ОТКАЗАТЬ, а не напечатать вердикт.

    Иначе он остаётся тем, чем был: работоспособным на вид инструментом,
    который на сегодняшних данных печатает «зелено» против точки отсчёта с 650
    ложными срабатываниями (фактически их 16). Проверяем не «упало», а что
    человек получил ПРИЧИНУ и АДРЕС актуальной проверки."""
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [os.path.join(root, "venv", "Scripts", "python.exe"),
         os.path.join(root, "experiments", "stage_d", "gate_d.py")],
        capture_output=True, text=True, encoding="utf-8", cwd=root,
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    assert proc.returncode != 0, (
        "Отставной гейт D завершился успешно — значит он снова выглядит "
        "работающей проверкой. Вывод:\n%s" % out)
    assert "ОТСТАВЛЕН" in out, "Отказ не назвал себя отставкой:\n%s" % out
    assert "650" in out and "16" in out, (
        "Отказ не назвал ЦИФРЫ, из-за которых гейт опасен (650 против 16):\n%s" % out)
    assert "tests/corpus/gate.py" in out, (
        "Отказ не указал, чем проверять на самом деле:\n%s" % out)


def test_retired_gate_keeps_its_numbers_for_provenance():
    """Отставка — не удаление. Числа этапа D обязаны остаться читаемыми: на них
    ссылается HANDOFF_STAGE_D_PRECISION, и они же объясняют, почему запуск
    запрещён."""
    assert BASELINE["fp_neg_total"] == 650, (
        "Числа этапа D изменены. Их роль — история; действующие пороги живут в "
        "tests/corpus/gate_config.py и в results_baseline.json.")
    assert gate_d.evaluate(_state(), BASELINE, KNOWN)["red"] is False, (
        "Историческое зеркало перестало работать: compute_state/evaluate "
        "сохраняются рабочими намеренно, отставлен только ЗАПУСК.")
