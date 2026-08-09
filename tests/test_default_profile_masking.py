# -*- coding: utf-8 -*-
"""Набор ПО УМОЛЧАНИЮ не должен открывать персональные данные
(`MFIX-PROFILE-IP-PER`, решение владельца).

ЧТО БЫЛО. Арбитраж пересечений считается один раз и не зависит от набора типов.
Если пересечение выигрывал тип, которого в наборе пользователя НЕТ, он маски не
получал — и территория оставалась открытой вместе с вытесненным кандидатом.
«ИП Беляев Олег Олегович» выигрывал как `ORG`; в наборе «Только персональные
данные» `ORG` выключен, и имя человека уходило в LLM открытым текстом.
Гейт этого не видел ПО ПОСТРОЕНИЮ: он всегда меряет на МАКСИМУМЕ.

ЧТО СТАЛО. Кандидат включённого типа, вытесненный ВЫКЛЮЧЕННЫМ, возвращается под
маску (`tokenizer._recover_shadowed`). Ниже — обе стороны: возврат работает И
не трогает набор «Максимум» ни на символ.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from models import SourceDocument, TextSegment  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize, resolve_for_masking, apply_masking  # noqa: E402
import type_policy  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")

IP_TEXT = ("Индивидуальный предприниматель Беляев Олег Олегович, именуемый в "
           "дальнейшем «Исполнитель», с одной стороны, заключили договор.")


def _doc(text=IP_TEXT):
    return SourceDocument(
        segments=[TextSegment(id="p0", text=text, source_type="txt_line", metadata={})],
        source_format="txt", source_path="probe.txt",
    )


@pytest.fixture(scope="module")
def resolved():
    doc = _doc()
    return doc, resolve_for_masking(doc, run_detection(doc, CONFIG), CONFIG)


def test_person_inside_ip_name_is_masked_in_default_profile(resolved):
    """СУТЬ НАХОДКИ. ФИО внутри имени ИП закрыто набором по умолчанию."""
    doc, kept = resolved
    anon, _ = apply_masking(doc, kept, CONFIG, type_policy.PERSONAL)
    assert "Беляев" not in anon, anon
    assert "[PERSON_" in anon


def test_maximum_profile_still_masks_the_whole_compound(resolved):
    """ЗЕРКАЛО 1. На максимуме склейка «ИП + ФИО» по-прежнему ОДНА ORG-маска —
    ради смысловой целостности она и заведена (иначе LLM видит «Компания ИП»)."""
    doc, kept = resolved
    anon, _ = apply_masking(doc, kept, CONFIG, None)
    assert "[ORG_1]" in anon
    assert "[PERSON_" not in anon
    assert "Беляев" not in anon


def test_recovery_is_inert_on_maximum(resolved):
    """ЗЕРКАЛО 2. При `enabled_types=None` возврат не делает НИЧЕГО: именно
    поэтому гейт и все прежние замеры (они меряют на максимуме) не сдвигаются.
    Проверяем сам механизм, а не только его следствие в тексте."""
    from tokenizer import _recover_shadowed, _barrier_types
    _doc_, kept = resolved
    assert _recover_shadowed(kept, None, _barrier_types(CONFIG)) == ([], [])


def test_suppressed_by_negative_class_is_NOT_recovered():
    """ЗЕРКАЛО 3, важнейшее. Победа ОТРИЦАТЕЛЬНОГО класса территорию не
    открывает: там победитель говорит «это не сущность». Вернуть подавленного
    значило бы отменить этап T4 руками."""
    from tokenizer import _recover_shadowed, _barrier_types
    from models import Entity

    barrier = _barrier_types(CONFIG)
    assert barrier, "в конфиге нет отрицательных классов — тест не о том"
    negative = sorted(barrier)[0]
    victim = Entity(id="v", segment_id="p0", start=0, end=5, original_text="Иванов",
                    entity_type="PERSON", detector="ner", confidence=1.0)
    winner = Entity(id="w", segment_id="p0", start=0, end=5, original_text="Иванов",
                    entity_type=negative, detector="ner", confidence=1.0,
                    shadowed=[victim])
    got, _drop = _recover_shadowed([winner], frozenset(type_policy.PERSONAL), barrier)
    assert got == []


def test_recovered_entity_round_trips():
    """Возвращённая маска обязана восстанавливаться: маска без обратимости —
    порча документа, а не защита."""
    import tempfile
    from session_store import save_session
    from detokenizer import detokenize

    doc = _doc()
    anon, kept = tokenize(doc, run_detection(doc, CONFIG), CONFIG,
                          enabled_types=type_policy.PERSONAL)
    storage = tempfile.mkdtemp(prefix="shifrator_defprof_")
    sid = save_session(kept, session_id=None, ttl_hours=1, storage_dir=storage)
    restored, unresolved = detokenize(anon, sid, storage_dir=storage, mode="exact")
    assert not unresolved
    assert "Беляев Олег Олегович" in restored
