"""Этап 2 — проверка блока 4 (tokenizer.py) на "грязных" пересечениях сущностей.
НЕ дублирует test_tokenizer.py этапа 1 (нумерация токенов, 3-звенная цепочка A-B-C,
KPP/BIK-конфликт уже покрыты там).

Фокус этапа:
  - более длинные цепочки пересечений (4-5 звеньев) — устойчивость инварианта
    "не удалять сущность без прямого пересечения с победителем";
  - идентичные интервалы разного типа — детерминированность выбора;
  - entity, выходящий за границы segment.text — предусловие, которое блок НЕ проверяет
    (спека: "нарушение = тихая порча текста, не исключение"); фиксируем масштаб потери.
"""
import pytest

from tokenizer import _resolve_overlaps, _render_segment, tokenize
from models import Entity, TextSegment, SourceDocument


def _e(start, end, entity_type, detector, confidence=1.0):
    return Entity(
        id=f"{start}_{end}_{entity_type}",
        segment_id="p0",
        start=start,
        end=end,
        original_text="x" * (end - start),
        entity_type=entity_type,
        detector=detector,
        confidence=confidence,
    )


def _overlap(a, b):
    return max(a.start, b.start) < min(a.end, b.end)


def _no_overlapping_survivors(survivors):
    return not any(
        _overlap(a, b)
        for i, a in enumerate(survivors)
        for b in survivors[i + 1 :]
    )


# --------------------------------------------------------------------------- #
# C2 — зелёный: длинные цепочки пересечений разрешаются без наложений
# --------------------------------------------------------------------------- #
class TestLongOverlapChains:
    """Реальный "грязный" сегмент: несколько NER-спанов идут внахлёст цепочкой
    (ФИО пересекается с адресом, адрес — со следующей организацией и т.д.).
    Инвариант спеки: после разрешения не остаётся ни одной пересекающейся пары,
    и никто не удалён без прямого пересечения с зафиксированным победителем.
    Этап 1 проверил цепочку из 3; здесь — 4 и 5 звеньев."""

    def test_four_entity_chain_no_overlapping_survivors(self):
        # A[0,4) B[3,7) C[6,10) D[9,13): соседи пересекаются, через один — нет.
        chain = [
            _e(0, 4, "PERSON", "ner"),
            _e(3, 7, "ORG", "ner"),
            _e(6, 10, "PERSON", "ner"),
            _e(9, 13, "ADDRESS", "ner"),
        ]
        survivors = _resolve_overlaps(chain)
        assert _no_overlapping_survivors(survivors)
        # A и C (PERSON) не пересекаются напрямую с итоговыми победителями и оба выбывают
        # только через прямое пересечение — итог детерминирован.
        assert sorted((s.start, s.end) for s in survivors) == [(3, 7), (9, 13)]

    def test_five_entity_chain_no_overlapping_survivors(self):
        chain = [
            _e(0, 4, "PERSON", "ner"),
            _e(3, 7, "ORG", "ner"),
            _e(6, 10, "ADDRESS", "ner"),
            _e(9, 13, "ORG", "ner"),
            _e(12, 16, "PERSON", "ner"),
        ]
        survivors = _resolve_overlaps(chain)
        assert _no_overlapping_survivors(survivors)

    def test_regex_cutting_two_ner_neighbours_removes_both(self):
        """Короткий regex-спан, пересекающий сразу двух соседних NER-сущностей,
        удаляет обе (правило 1 regex>ner). Это осознанное ограничение MVP —
        подтверждаем детерминированный итог: остаётся только regex."""
        entities = [
            _e(0, 6, "PERSON", "ner"),
            _e(5, 9, "INN", "regex"),   # пересекает и PERSON, и ORG
            _e(8, 14, "ORG", "ner"),
        ]
        survivors = _resolve_overlaps(entities)
        assert [s.entity_type for s in survivors] == ["INN"]


# --------------------------------------------------------------------------- #
# C2b — зелёный: идентичные интервалы разного типа -> один детерминированный токен
# --------------------------------------------------------------------------- #
class TestIdenticalSpanDifferentType:
    """9-значное число, начинающееся на "04", матчится и как KPP, и как BIK —
    идентичные интервалы, оба regex, равная длина. Спека (правило 3): приоритет
    BIK > KPP. Подтверждаем, что остаётся ровно один, и это BIK."""

    def test_kpp_bik_identical_span_bik_wins(self):
        survivors = _resolve_overlaps(
            [_e(0, 9, "KPP", "regex"), _e(0, 9, "BIK", "regex")]
        )
        assert [s.entity_type for s in survivors] == ["BIK"]


# --------------------------------------------------------------------------- #
# F8 — НЕЗНАЧИТЕЛЬНО / пробел в спеке: entity за границами segment.text
#      -> тихая потеря куска текста, не исключение
# --------------------------------------------------------------------------- #
class TestEntityOutOfSegmentBounds:
    """Спека блока 4 объявляет `0 <= start < end <= len(segment.text)` предусловием,
    которое блок НЕ проверяет: "нарушение = тихая порча текста, не исключение".
    В MVP апстрим (regex/ner) даёт корректные оффсеты, поэтому практического
    инцидента нет — но фиксируем масштаб тихой потери, чтобы фаза 2 знала цену
    отсутствия защитной проверки. Тест-документация (проходит, ничего не чинит)."""

    def test_end_beyond_length_silently_drops_tail(self):
        seg = TextSegment(id="p0", text="Иванов", source_type="docx_paragraph", metadata={})
        bad = _e(0, 100, "PERSON", "ner")
        bad.token = "[PERSON_1]"
        # text[:0] + token + text[100:] => хвост "анов"/весь остаток пропадает молча.
        rendered = _render_segment(seg, [bad])
        assert rendered == "[PERSON_1]"  # исходный текст сегмента исчез без ошибки

    def test_start_inside_end_beyond_loses_middle(self):
        seg = TextSegment(id="p0", text="Договор с Ивановым", source_type="docx_paragraph", metadata={})
        bad = _e(10, 100, "PERSON", "ner")  # end за границей (len=18)
        bad.token = "[PERSON_1]"
        rendered = _render_segment(seg, [bad])
        # Всё после позиции 10 подменяется токеном, "Ивановым" утрачено без исключения.
        assert rendered == "Договор с [PERSON_1]"
