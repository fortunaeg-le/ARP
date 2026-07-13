# -*- coding: utf-8 -*-
"""Этап 0a — контракт encrypt: НИКОГДА не бросает необработанное исключение
(TypeError/AttributeError/IndexError) на документах корпуса.

Регресс на баг src/syntax_compound.py:137 ('o.end()' вызывался как метод на
int-атрибуте Entity.end) — крешил encrypt на 31/324 документах корпуса
(9.6 %), из-за чего документ не анонимизировался вообще и {sid}.txt не
создавался. См. tests/test_syntax_compound.py::
test_real_org_plus_spelled_ip_form_plus_person_does_not_crash для юнит-уровня.

Быстрый набор (по умолчанию): по 2 документа каждого contract_type + все 31
документ, ранее падавший (tests/corpus/results_04452df.json). Полный прогон
по всем 324 документам — под @pytest.mark.slow.

encrypt МОЖЕТ вернуть типизированную обработанную ошибку (FileNotFoundError,
ValueError и т.п. — см. cmd_encrypt в shifrator.py) — это не нарушение
контракта. Нарушение — необработанные TypeError/AttributeError/IndexError,
означающие, что в логике детекции/склейки/токенизации спутаны типы или
перепутаны индексы.
"""
import json
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORPUS = os.path.join(_ROOT, "tests", "corpus")
_DOCS = os.path.join(_CORPUS, "docs")
_GOLD_PATH = os.path.join(_CORPUS, "gold.json")
_CONFIG = os.path.join(_ROOT, "entity_types.yaml")

# Документы, крешившие на коммите 04452df (см. tests/corpus/results_04452df.json,
# записи с полем "error" вместо "n_entities") — обязательная часть быстрого набора.
_PREVIOUSLY_CRASHED = [
    "lease_0004", "lease_0004__m1337_combo", "lease_0004__m2718_homoglyph",
    "lease_0008", "lease_0008__m1337_case", "lease_0008__m2718_linebreak",
    "lease_0010", "lease_0010__m1337_linebreak", "lease_0010__m2718_cell_split",
    "loan_0002", "loan_0002__m2718_digit_spaces",
    "loan_0004", "loan_0004__m1337_digit_spaces",
    "loan_0010", "loan_0010__m1337_case", "loan_0010__m2718_linebreak",
    "sale_0006__m1337_invisible",
    "sale_0010", "sale_0010__m1337_combo", "sale_0010__m2718_homoglyph",
    "supply_0014", "supply_0014__m2718_linebreak",
    "supply_0016", "supply_0016__m1337_linebreak", "supply_0016__m2718_cell_split",
    "works_0004", "works_0004__m1337_invisible", "works_0004__m2718_digit_spaces",
    "works_0010", "works_0010__m1337_homoglyph", "works_0010__m2718_case",
]

_UNHANDLED_EXC = (TypeError, AttributeError, IndexError)


def _load_gold():
    with open(_GOLD_PATH, encoding="utf-8") as f:
        gold = json.load(f)
    gold.sort(key=lambda d: d["doc_id"])
    return gold


def _fast_doc_ids():
    gold = _load_gold()
    by_id = {d["doc_id"]: d for d in gold}

    # по 2 документа каждого contract_type (base-документы, детерминированный
    # выбор — первые два в отсортированном порядке).
    seen_per_type = {}
    picked = []
    for d in gold:
        if d["source"] != "base":
            continue
        ct = d["contract_type"]
        n = seen_per_type.get(ct, 0)
        if n < 2:
            picked.append(d["doc_id"])
            seen_per_type[ct] = n + 1

    for doc_id in _PREVIOUSLY_CRASHED:
        assert doc_id in by_id, f"{doc_id} отсутствует в gold.json"
        if doc_id not in picked:
            picked.append(doc_id)

    return sorted(set(picked)), {d["doc_id"]: d["format"] for d in gold}


def _all_doc_ids():
    gold = _load_gold()
    return sorted(d["doc_id"] for d in gold), {d["doc_id"]: d["format"] for d in gold}


def _run_encrypt_pipeline(path, storage_dir):
    """Прогоняет тот же пайплайн, что cmd_encrypt в shifrator.py, до записи
    файла (запись в изолированный storage_dir, не ~/.shifrator)."""
    from extractor import extract
    from regex_detector import detect_regex
    from ner_detector import detect_ner
    from syntax_compound import merge_compound_entities
    from tokenizer import tokenize
    from session_store import save_session

    doc = extract(path)
    regex_entities = detect_regex(doc, _CONFIG)
    ner_entities = detect_ner(doc, _CONFIG, regex_entities=regex_entities)
    entities = regex_entities + ner_entities
    entities = merge_compound_entities(doc, entities)
    anon_text, final_entities = tokenize(doc, entities, _CONFIG)
    session_id = save_session(final_entities, session_id=None, ttl_hours=24, storage_dir=str(storage_dir))
    return session_id, anon_text


def _assert_no_unhandled_crash(doc_ids, formats, tmp_path):
    failures = []
    for doc_id in doc_ids:
        path = os.path.join(_DOCS, doc_id + "." + formats[doc_id])
        try:
            session_id, anon_text = _run_encrypt_pipeline(path, tmp_path / "storage")
        except _UNHANDLED_EXC as exc:
            failures.append((doc_id, repr(exc)))
        else:
            assert session_id
            assert isinstance(anon_text, str)
    assert not failures, (
        f"{len(failures)} документ(ов) упали с необработанным TypeError/"
        f"AttributeError/IndexError: {failures}"
    )


def test_fast_set_encrypt_never_crashes(tmp_path):
    """Быстрый набор: по 2 документа каждого contract_type + все 31 ранее
    падавших документ. Контракт: ни один не бросает необработанное
    TypeError/AttributeError/IndexError."""
    doc_ids, formats = _fast_doc_ids()
    _assert_no_unhandled_crash(doc_ids, formats, tmp_path)


@pytest.mark.slow
def test_full_corpus_encrypt_never_crashes(tmp_path):
    """Полный корпус — все 324 документа. Контракт: ни один не бросает
    необработанное TypeError/AttributeError/IndexError."""
    doc_ids, formats = _all_doc_ids()
    _assert_no_unhandled_crash(doc_ids, formats, tmp_path)
