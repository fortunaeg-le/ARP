# -*- coding: utf-8 -*-
"""Этап E'' — тест-страж ДЕТЕРМИНИЗМА детекции.

Закрывает дефект Eprime-A: `_CapsResolver` в `src/extractor.py` ключевал memo-кеш
и защиту от циклов ЧИСЛОМ `id(style._element)`, НЕ удерживая ссылку на объект.
`paragraph.style`/`run.style` в python-docx — не кешированные свойства (новый прокси
на каждое обращение), прокси lxml живёт только пока на него есть ссылка, а CPython
переиспользует освободившийся адрес. Итог: ДРУГОЙ стиль получал тот же `id()` и
возвращал ЧУЖОЙ вердикт капса (ложное попадание), либо `seen` ложно опознавал цикл и
обход цепочки обрывался. Всё это зависит от момента сборки мусора => результат
детекции менялся МЕЖДУ ПРОГОНАМИ В ОДНОМ ПРОЦЕССЕ при идентичном входе.

На реальном договоре (1097 стилей): 1290 ложных попаданий на 2396 вызовов,
4 разных detection_text на 4 попытки. На фикстуре `synthetic_many_styles.docx`
(284 стиля) СТАРАЯ реализация даёт 2660 ложных попаданий и 4 разных sha, новая — 0 и 1.

ПОЧЕМУ ОТДЕЛЬНАЯ ФИКСТУРА, А НЕ КОРПУС: документы `tests/corpus/` имеют ВСЕГО 2 стиля
и вызывают `_style_chain` НОЛЬ раз — они физически неспособны воспроизвести дефект.
Именно структурная бедность синтетики прятала его от всех предыдущих этапов, поэтому
страж обязан жить на документе с ДЕСЯТКАМИ стилей.
"""
import hashlib
import os

import pytest

from extractor import extract, _CapsResolver
from pipeline import run_detection
from tokenizer import tokenize

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "synthetic_many_styles.docx")
_CONFIG = os.path.join(_ROOT, "entity_types.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(_FIXTURE),
    reason="нет фикстуры synthetic_many_styles.docx "
           "(создать: experiments/stage_eprime_determinism/make_many_styles_fixture.py)",
)


def _detection_text_sha(doc):
    rows = [(s.id,
             hashlib.sha256(s.text.encode("utf-8")).hexdigest(),
             hashlib.sha256((s.metadata.get("detection_text") or "").encode("utf-8")).hexdigest())
            for s in doc.segments]
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def test_caps_resolver_cache_is_not_keyed_by_id():
    """СТРУКТУРНЫЙ страж: ключ кеша — ОБЪЕКТ элемента стиля, а не int из id().

    Ловит регрессию на уровне кода, не дожидаясь, пока она проявится статистически:
    если кто-то снова напишет `key = (id(style._element), attr)`, ключ станет int."""
    doc_path = _FIXTURE
    from docx import Document
    document = Document(doc_path)
    resolver = _CapsResolver(document)
    for para in document.paragraphs[:50]:
        resolver.paragraph_baseline(para, None)

    assert resolver._chain_cache, "кеш пуст — фикстура не задействует цепочки стилей"
    for key in resolver._chain_cache:
        element = key[0]
        assert not isinstance(element, int), (
            "кеш _CapsResolver снова ключуется по id() — вернулся недетерминизм "
            "Eprime-A (ложные попадания при переиспользовании адреса)"
        )


def test_detection_text_stable_across_repeated_extract():
    """detection_text (metadata) обязан быть побайтно одинаковым на повторных extract().

    Именно ЭТО плавало: segment.text оставался стабильным, поэтому дефект не видели,
    а детекторы читают detection_text (per_search_view/anchor_search_view — ORG/PER;
    detection_view — ADDRESS)."""
    shas = {_detection_text_sha(extract(_FIXTURE)) for _ in range(5)}
    assert len(shas) == 1, (
        "detection_text различается между прогонами extract() (%d вариантов) — "
        "вернулся недетерминизм _CapsResolver" % len(shas)
    )


@pytest.mark.slow
def test_same_process_repeated_detection_is_byte_identical():
    """Полный конвейер на одном документе N раз в ОДНОМ процессе — побайтно одинаково."""
    results = []
    for _ in range(3):
        doc = extract(_FIXTURE)
        entities = run_detection(doc, _CONFIG)
        anon, kept = tokenize(doc, entities, _CONFIG)
        sig = tuple(sorted(
            (e.entity_type, e.segment_id, e.start, e.end, e.token) for e in kept
        ))
        results.append((len(kept), hashlib.sha256(anon.encode("utf-8")).hexdigest(), sig))

    assert len({r[0] for r in results}) == 1, \
        "число масок разошлось между прогонами: %r" % [r[0] for r in results]
    assert len({r[1] for r in results}) == 1, "anon-текст разошёлся между прогонами"
    assert len({r[2] for r in results}) == 1, "состав сущностей разошёлся между прогонами"
