# -*- coding: utf-8 -*-
"""ЭТАП V1-SCOPE — стражи манифеста исключений из линий гейта.

Три группы:
  1. TestLock — замок: запись без автора/обоснования/с чужой линией роняет
     сборку (тот же порядок, что promote_baseline: --author + --reason).
  2. TestNarrow — исключение УЗКОЕ: снята одна линия одного типа одного
     корпуса, всё остальное меряется по-прежнему. Это главная группа: цена
     ошибки здесь — молча переставший охраняться тип.
  3. TestVisible — цифра не спрятана: отчёт печатает её отдельной строкой.

Проверяется СВОЙСТВО, а не текущий состав списка: тесты параметризованы по
самому манифесту, поэтому следующая запись попадёт под ту же охрану.
"""
import io
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import gate as G  # noqa: E402
import measure_lib as ML  # noqa: E402
import scope_exclusions as SX  # noqa: E402


def _rec(**over):
    r = {
        "type": "PROBE", "line": "а", "corpus": "v1", "date": "2026-08-08",
        "author": "владелец продукта", "stage": "PROBE",
        "reason": "О" * (SX._MIN_REASON + 10),
        "known_count": 1, "closed": None,
    }
    r.update(over)
    return r


# =========================================================================== #
#  1. Замок
# =========================================================================== #

class TestLock:

    def test_real_manifest_passes(self):
        SX.validate()  # не бросает

    def test_missing_author_breaks_the_build(self):
        with pytest.raises(SystemExit) as exc:
            SX.validate([_rec(author="")])
        assert "НЕТ АВТОРА" in str(exc.value)

    def test_missing_reason_breaks_the_build(self):
        with pytest.raises(SystemExit) as exc:
            SX.validate([_rec(reason="")])
        assert "НЕТ ОБОСНОВАНИЯ" in str(exc.value)

    def test_short_reason_is_not_a_reason(self):
        with pytest.raises(SystemExit) as exc:
            SX.validate([_rec(reason="мешает гейту")])
        assert "НЕТ ОБОСНОВАНИЯ" in str(exc.value)

    def test_leak_line_can_never_be_excluded(self):
        """Линия «б» (утечка ПДн) вне закрытого списка — снять с неё тип
        нельзя ни с каким обоснованием."""
        with pytest.raises(SystemExit) as exc:
            SX.validate([_rec(line="б")])
        assert "утечк" in str(exc.value)

    def test_unknown_corpus_breaks_the_build(self):
        with pytest.raises(SystemExit) as exc:
            SX.validate([_rec(corpus="v3")])
        assert "неизвестен" in str(exc.value)

    def test_duplicate_record_breaks_the_build(self):
        with pytest.raises(SystemExit) as exc:
            SX.validate([_rec(), _rec()])
        assert "овтор" in str(exc.value)

    def test_every_real_record_has_author_and_reason(self):
        for r in SX.EXCLUSIONS:
            assert r["author"].strip(), r["type"]
            assert len(r["reason"].strip()) >= SX._MIN_REASON, r["type"]


# =========================================================================== #
#  2. Узость: исключена ОДНА линия ОДНОГО типа ОДНОГО корпуса
# =========================================================================== #

_V1_LINE_A = sorted(SX.excluded_types("а", "v1"))


class TestNarrow:

    def test_only_declared_types_are_skipped_on_precision(self):
        """compare_precision пропускает РОВНО объявленные типы и никакие
        другие: подкладываем обвал точности каждому типу по очереди и ждём
        регресс у всех, кроме исключённых."""
        for t in ML.ALL_ENTITY_TYPES:
            base = {"per_type": {t: {"precision": 100.0}}}
            cur = {"per_type": {t: {"precision": 10.0}}}
            reg, _ = G.compare_precision(base, cur, 0.0)
            if t in _V1_LINE_A:
                assert reg == [], "%s исключён — но линия «а» его уронила" % t
            else:
                assert reg, "%s НЕ исключён — но обвал точности прошёл молча" % t

    @pytest.mark.parametrize("t", _V1_LINE_A)
    def test_excluded_type_is_still_under_other_v1_lines(self, t):
        """Исключение снимает ОДНУ линию. Линии «д» (порча документа) и «е»
        (границы) обязаны продолжать краснеть по этому же типу."""
        base_prec = {"per_type": {t: {"nothing": 0}}, "nothing_total": 0}
        cur_prec = {"per_type": {t: {"nothing": 40}}, "nothing_total": 40}
        reg, _ = G.compare_overmask(base_prec, cur_prec, 0)
        assert any(t in s for s in reg), (
            "%s исключён из точности, но перестал охраняться и линией «д» — "
            "исключение расползлось" % t)

        empty = ML._empty_bnd()
        base_bnd = {"per_type": {t: dict(empty)}, "total": dict(empty),
                    "n_with_field": 1, "n_exact": 0, "n_typed": 0}
        cur_bnd = {"per_type": {t: dict(empty, shorter=5, under_chars=50)},
                   "total": dict(empty, shorter=5, under_chars=50),
                   "n_with_field": 1, "n_exact": 0, "n_typed": 0}
        reg_e, _ = G.compare_boundaries(base_bnd, cur_bnd, 0, 0)
        assert any(t in s for s in reg_e), (
            "%s перестал охраняться линией «е» (границы) — исключение "
            "расползлось" % t)

    @pytest.mark.parametrize("t", _V1_LINE_A)
    def test_excluded_type_is_still_under_leak_line(self, t):
        """Линия «б» (утечка) — по этому типу тоже жива. Гейт считает её по
        ML.ALL_ENTITY_TYPES без оглядки на манифест; тест это фиксирует."""
        z = {"n": 0, "found": 0, "leak_v2_6": 0, "leak_v2_8": 0}
        base = {"crashed": [], "refused": [], "per_type": {t: dict(z)},
                "total_bik_excl": dict(z), "fp_on_neg_total": 0, "fp_on_neg": {},
                "masking_correctness": {}}
        cur = {"crashed": [], "refused": [],
               "per_type": {t: dict(z, leak_v2_6=7, leak_v2_8=7)},
               "total_bik_excl": dict(z), "fp_on_neg_total": 0, "fp_on_neg": {},
               "masking_correctness": {}}
        _, reg, _ = G.compare(base, cur, fp_tolerance=0)
        assert any(t in s and "утечк" in s for s in reg), (
            "%s перестал охраняться линией «б» — это запрещено манифестом "
            "(линии «б» в LINES нет)" % t)

    def test_fp_total_subtracts_only_excluded_types(self):
        """Из итога FP вычитаются только исключённые типы: рост FP у любого
        другого типа по-прежнему роняет гейт."""
        z = {"n": 0, "found": 0, "leak_v2_6": 0, "leak_v2_8": 0}
        t = _V1_LINE_A[0]
        base = {"crashed": [], "refused": [], "per_type": {},
                "total_bik_excl": dict(z), "fp_on_neg_total": 0,
                "fp_on_neg": {}, "masking_correctness": {}}
        # только исключённый тип вырос -> гейт молчит
        cur_ok = dict(base, fp_on_neg_total=315, fp_on_neg={t: 315})
        _, reg, _ = G.compare(base, cur_ok, fp_tolerance=0)
        assert not [s for s in reg if "FP по негативам" in s]
        # к нему добавился ЧУЖОЙ тип -> гейт краснеет и печатает исключение
        cur_bad = dict(base, fp_on_neg_total=316, fp_on_neg={t: 315, "PHONE": 1})
        _, reg2, _ = G.compare(base, cur_bad, fp_tolerance=0)
        line = [s for s in reg2 if "FP по негативам" in s]
        assert line, "рост FP у неисключённого типа прошёл молча"
        assert "вне счёта" in line[0] and t in line[0]

    def test_v2_measurement_does_not_read_the_manifest(self):
        """Корпус v2 меряет тип ПОЛНОСТЬЮ: замерная дорожка v2 про манифест
        исключений не знает вовсе (он живёт в гейте корпуса v1)."""
        src = io.open(os.path.join(ROOT, "tests", "corpus_v2", "measure_v2.py"),
                      encoding="utf-8").read()
        assert "scope_exclusions" not in src


# =========================================================================== #
#  3. Цифра не спрятана
# =========================================================================== #

class TestVisible:

    def test_report_prints_the_excluded_count_as_its_own_line(self, capsys):
        t = _V1_LINE_A[0]
        cur_prec = {"per_type": {t: {"tp": 138, "fp_neg": 315}}}
        cur_agg = {"fp_on_neg": {t: 315}}
        G.print_scope_exclusions(cur_prec, cur_agg)
        out = capsys.readouterr().out
        assert "исключено из точности v1: %s (315 расхождений разметки)" % t in out
        assert "обоснование:" in out
        assert "корпусе v2" in out  # сказано, что там тип меряется полностью

    def test_report_flags_a_changed_count(self, capsys):
        """Если число разошлось с тем, под которое давалось обоснование, отчёт
        обязан сказать это вслух: обоснование давалось под прежнюю цифру."""
        t = _V1_LINE_A[0]
        G.print_scope_exclusions({"per_type": {t: {"tp": 1, "fp_neg": 900}}},
                                 {"fp_on_neg": {t: 900}})
        out = capsys.readouterr().out
        assert "ИЗМЕНИЛОСЬ" in out
