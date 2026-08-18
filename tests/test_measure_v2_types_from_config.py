# -*- coding: utf-8 -*-
"""ЭТАП DEBT-1, часть 2: состав форменной таблицы измерителя v2 берётся из
СОБРАННОГО КОНФИГА, а не из списка внутри измерителя.

Находка MEASURE-NOW: типы, заведённые фабрикой после этапа T2 (CONTRACT_NO,
REG_ID, REG_NUMBER, SHARE_PCT, TRANCHE), в форменную таблицу не попадали —
детектировались, но в отчёте их не было. Пять сейчас, двадцать потом.

Стражи здесь — про МЕХАНИЗМ, а не про сегодняшний список: живая проба заводит
НЕСУЩЕСТВУЮЩИЙ тип в копии конфига и требует, чтобы он появился в составе сам.
"""
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus_v2"))

import measure_v2 as M  # noqa: E402


def _gold():
    return M.load_gold()


def test_config_types_are_superset_of_hardcoded_list():
    """Прежний зашитый состав целиком внутри вычисленного — состав не сузился."""
    types = M.measured_types()
    for t in M.T2_TYPES:
        assert t in types, f"{t} выпал из состава таблицы"


def test_factory_types_after_t2_are_in_the_table():
    """Ровно те пять типов, которых таблица не видела (регресс-якорь находки)."""
    types = M.measured_types()
    for t in ("CONTRACT_NO", "REG_ID", "REG_NUMBER", "SHARE_PCT", "TRANCHE"):
        assert t in types, f"фабричный тип {t} снова не попал в форменную таблицу"


def test_typed_negatives_stay_out():
    """Типизированный негатив — своя таблица (маска на нём = ложное
    срабатывание), в форменную он не попадает. После DATE-ON состав пуст:
    проверка стала бы пустой, поэтому рядом стоит ЖИВАЯ проба на выдуманном
    негативе — механизм обязан работать и без сегодняшних участников."""
    types = M.measured_types()
    for t in M.TYPED_NEGATIVES:
        assert t not in types

    gold = [{"doc_id": "probe", "entities": [{"type": "SUM"}, {"type": "GHOST_NEG"}]}]
    src = yaml.safe_load(open(M.CONFIG, encoding="utf-8"))
    src["entity_types"]["GHOST_NEG"] = {
        "method": "regex", "pattern": r"\bпризрак-\d+\b",
        "token_prefix": "GHOST", "rank": 1, "enabled": True,
        "measure": {"leak_class": "text"},
    }
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as fh:
        yaml.safe_dump(src, fh, allow_unicode=True)
        cfg = fh.name
    assert "GHOST_NEG" in M.measured_types(cfg, gold=gold)
    saved = M.TYPED_NEGATIVES
    try:
        M.TYPED_NEGATIVES = ("GHOST_NEG",)
        assert "GHOST_NEG" not in M.measured_types(cfg, gold=gold)
    finally:
        M.TYPED_NEGATIVES = saved
    os.unlink(cfg)


def test_date_moved_into_the_table_after_date_on():
    """ЭТАП DATE-ON — регресс-якорь реклассификации: дата больше не
    типизированный негатив, а величина, и обязана меряться формой наравне с
    остальными (полнота/границы/утечка), а не считаться ложным срабатыванием."""
    assert "DATE" not in M.TYPED_NEGATIVES
    assert "DATE" in M.measured_types()


def test_manual_types_are_not_pulled_in():
    """PERSON/ORG/ADDRESS фабрикой не заводились (нет блока measure:) —
    в таблицу v2 их состав не тянет: у них свой прибор в гейте v1."""
    types = M.measured_types()
    for t in ("PERSON", "ORG", "ADDRESS", "CLAUSE_REF", "ROLE_TERM"):
        assert t not in types


def test_new_factory_type_appears_by_itself(tmp_path):
    """ЖИВАЯ ПРОБА: новый тип заведён в конфиге — появился в составе САМ,
    без единой правки measure_v2.py."""
    src = yaml.safe_load(open(M.CONFIG, encoding="utf-8"))
    src["entity_types"]["DEBT1_PROBE"] = {
        "method": "regex", "pattern": r"\bпроба-\d+\b",
        "token_prefix": "PROBE", "rank": 1, "enabled": True,
        "measure": {"leak_class": "text"},
    }
    cfg = tmp_path / "entity_types_probe.yaml"
    cfg.write_text(yaml.safe_dump(src, allow_unicode=True), encoding="utf-8")

    gold = [{"doc_id": "probe", "entities": [{"type": "DEBT1_PROBE"}]}]
    assert "DEBT1_PROBE" in M.measured_types(str(cfg), gold=gold)
    # и без разметки его в таблице нет — считать нечего
    assert "DEBT1_PROBE" not in M.measured_types(str(cfg), gold=[])


def test_markup_name_differing_from_config_key_is_resolved():
    """Ключ конфига BANK_ACCOUNT, имя в разметке — ACCOUNT (token_prefix).
    Состав обязан отдавать ИМЯ РАЗМЕТКИ, иначе таблица будет пустой строкой."""
    types = M.measured_types()
    assert "ACCOUNT" in types and "BANK_ACCOUNT" not in types


@pytest.mark.parametrize("t", ["PERCENT", "TERM", "SUM"])
def test_table_builds_for_every_declared_type(t):
    """type_table не падает и принимает вычисленный состав целиком."""
    assert t in M.measured_types()
