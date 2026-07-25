# -*- coding: utf-8 -*-
"""Этап E'' — тесты-стражи ДЕТЕРМИНИЗМА детекции.

ОБЪЕДИНЁННЫЙ ФАЙЛ (этап S2, 2026-07-25). Две ветки независимо завели файл с этим
именем, и версии оказались НЕ конкурирующими, а ВЗАИМОДОПОЛНЯЮЩИМИ: они ловят
РАЗНЫЕ дефекты на РАЗНЫХ фикстурах. Поэтому при мерже взято объединение — ни один
страж не потерян.

--------------------------------------------------------------------------------
СТРАЖ 1 (фикстура synthetic_many_styles.docx, 284 стиля) — дефект `_CapsResolver`
--------------------------------------------------------------------------------
`_CapsResolver` в `src/extractor.py` ключевал memo-кеш и защиту от циклов ЧИСЛОМ
`id(style._element)`, НЕ удерживая ссылку на объект. `paragraph.style`/`run.style`
в python-docx — не кешированные свойства (новый прокси на каждое обращение), прокси
lxml живёт только пока на него есть ссылка, а CPython переиспользует освободившийся
адрес. Итог: ДРУГОЙ стиль получал тот же `id()` и возвращал ЧУЖОЙ вердикт капса
(ложное попадание), либо `seen` ложно опознавал цикл и обход цепочки обрывался. Всё
это зависит от момента сборки мусора => результат детекции менялся МЕЖДУ ПРОГОНАМИ
В ОДНОМ ПРОЦЕССЕ при идентичном входе.

На реальном договоре (1097 стилей): 1290 ложных попаданий на 2396 вызовов, 4 разных
detection_text на 4 попытки. На фикстуре (284 стиля) СТАРАЯ реализация даёт 2660
ложных попаданий и 4 разных sha, новая — 0 и 1.

ПОЧЕМУ ОТДЕЛЬНАЯ ФИКСТУРА, А НЕ КОРПУС: документы `tests/corpus/` имеют ВСЕГО 2 стиля
и вызывают `_style_chain` НОЛЬ раз — они физически неспособны воспроизвести дефект.
Именно структурная бедность синтетики прятала его от всех предыдущих этапов, поэтому
страж обязан жить на документе с ДЕСЯТКАМИ стилей.

--------------------------------------------------------------------------------
СТРАЖ 2 (фикстура synthetic_corporate_large.docx, ~5000 сегментов) — Eprime-A/BLAS
--------------------------------------------------------------------------------
Находка Eprime-A (docs/FINDINGS.md): на РЕАЛЬНОМ документе (~2500 сегментов, в
репозитории не хранится — политика владельца) детекция давала разные результаты между
прогонами В ОДНОМ ПРОЦЕССЕ (239/226/226 масок). На синтетическом двойнике эффект НЕ
воспроизвёлся ни разу — это само по себе не доказывает отсутствие дефекта на реальных
документах. Раз проблему нельзя показать на этой фикстуре, здесь страхуется РЕГРЕССИЯ:
если в код детекции просочится глобальное мутируемое состояние (реестр/кэш, не
сбрасываемый между документами) или недетерминированная сортировка, тест обязан
покраснеть первым, а не через полгода на проде.

Защитная мера (не chasing bug): `src/ner_detector.py` и `src/syntax_compound.py`
фиксируют число потоков BLAS (OMP/OPENBLAS/MKL/NUMEXPR=1) до импорта natasha и
прогревают модели фиктивным текстом при импорте модуля.

ВАЖНО (обновление 2026-07-24, данные владельца): гипотеза «холодный BLAS» была
ОПРОВЕРГНУТА — разброс на реальном документе оказался СЛУЧАЙНЫМ, а не «первый ≠
остальные». Находка Eprime-A остаётся ОТКРЫТОЙ; эти тесты — страж от регрессии, а
не доказательство её отсутствия.
"""
import hashlib
import os

import pytest

from extractor import extract, _CapsResolver
from pipeline import run_detection
from tokenizer import tokenize

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG = os.path.join(_ROOT, "entity_types.yaml")

# Две РАЗНЫЕ фикстуры под два разных класса дефекта — не взаимозаменяемы.
_FIXTURE_STYLES = os.path.join(_ROOT, "tests", "fixtures", "synthetic_many_styles.docx")
_FIXTURE_LARGE = os.path.join(_ROOT, "tests", "fixtures", "synthetic_corporate_large.docx")

# skipif ПОТЕСТОВО, а не на модуль: иначе отсутствие одной фикстуры молча погасило бы
# стражей другого класса (модульный pytestmark так и делал в одной из версий).
_needs_styles = pytest.mark.skipif(
    not os.path.isfile(_FIXTURE_STYLES),
    reason="нет фикстуры synthetic_many_styles.docx "
           "(создать: experiments/stage_eprime_determinism/make_many_styles_fixture.py)",
)
_needs_large = pytest.mark.skipif(
    not os.path.isfile(_FIXTURE_LARGE),
    reason="нет фикстуры synthetic_corporate_large.docx "
           "(создать: experiments/stage_eprime_determinism/make_synthetic_large.py)",
)


def _detection_text_sha(doc):
    rows = [(s.id,
             hashlib.sha256(s.text.encode("utf-8")).hexdigest(),
             hashlib.sha256((s.metadata.get("detection_text") or "").encode("utf-8")).hexdigest())
            for s in doc.segments]
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _run_once(doc_path):
    doc = extract(doc_path)
    entities = run_detection(doc, _CONFIG)
    anon, kept = tokenize(doc, entities, _CONFIG)
    sig = tuple(sorted(
        (e.entity_type, e.segment_id, e.start, e.end,
         tuple(tuple(s) for s in (e.spans or [])), e.token)
        for e in kept
    ))
    return len(kept), hashlib.sha256(anon.encode("utf-8")).hexdigest(), sig


# --------------------------------------------------------------------------- #
#                   СТРАЖ 1 — _CapsResolver / many styles                      #
# --------------------------------------------------------------------------- #
@_needs_styles
def test_caps_resolver_cache_is_not_keyed_by_id():
    """СТРУКТУРНЫЙ страж: ключ кеша — ОБЪЕКТ элемента стиля, а не int из id().

    Ловит регрессию на уровне кода, не дожидаясь, пока она проявится статистически:
    если кто-то снова напишет `key = (id(style._element), attr)`, ключ станет int."""
    from docx import Document
    document = Document(_FIXTURE_STYLES)
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


@_needs_styles
def test_detection_text_stable_across_repeated_extract():
    """detection_text (metadata) обязан быть побайтно одинаковым на повторных extract().

    Именно ЭТО плавало: segment.text оставался стабильным, поэтому дефект не видели,
    а детекторы читают detection_text (per_search_view/anchor_search_view — ORG/PER;
    detection_view — ADDRESS)."""
    shas = {_detection_text_sha(extract(_FIXTURE_STYLES)) for _ in range(5)}
    assert len(shas) == 1, (
        "detection_text различается между прогонами extract() (%d вариантов) — "
        "вернулся недетерминизм _CapsResolver" % len(shas)
    )


@_needs_styles
@pytest.mark.slow
def test_many_styles_repeated_detection_is_byte_identical():
    """Полный конвейер на СТИЛЕВОЙ фикстуре N=3 раза в одном процессе.

    Раньше назывался test_same_process_repeated_detection_is_byte_identical и
    сталкивался по имени с одноимённым тестом стража 2 — переименован при
    объединении (этап S2), оба живы."""
    results = [_run_once(_FIXTURE_STYLES) for _ in range(3)]
    assert len({r[0] for r in results}) == 1, \
        "число масок разошлось между прогонами: %r" % [r[0] for r in results]
    assert len({r[1] for r in results}) == 1, "anon-текст разошёлся между прогонами"
    assert len({r[2] for r in results}) == 1, "состав сущностей разошёлся между прогонами"


# --------------------------------------------------------------------------- #
#                   СТРАЖ 2 — Eprime-A / corporate large                       #
# --------------------------------------------------------------------------- #
@_needs_large
@pytest.mark.slow
def test_same_process_repeated_detection_is_byte_identical():
    """N=10 прогонов детекции на одном документе В ОДНОМ ПРОЦЕССЕ обязаны дать
    побайтно идентичный anon-текст и идентичный набор сущностей (тип/сегмент/
    границы/spans/токен) на каждом прогоне."""
    results = [_run_once(_FIXTURE_LARGE) for _ in range(10)]
    counts = [r[0] for r in results]
    hashes = [r[1] for r in results]
    sigs = [r[2] for r in results]

    assert len(set(counts)) == 1, "число масок разошлось между прогонами: %r" % counts
    assert len(set(hashes)) == 1, "anon-текст (sha256) разошёлся между прогонами"
    assert len(set(sigs)) == 1, "состав/границы сущностей разошлись между прогонами"


@_needs_large
def test_two_runs_byte_identical_fast():
    """Быстрая версия (N=2) без @slow — для обычного `pytest -q`."""
    r1 = _run_once(_FIXTURE_LARGE)
    r2 = _run_once(_FIXTURE_LARGE)
    assert r1 == r2
