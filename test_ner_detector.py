"""Тесты блока 3 — ner_detector.py (detect_ner). Использует реальную natasha (тяжёлый импорт,
через session-scope фикстуру ner_detector_module — см. conftest.py)."""
import pytest

from models import SourceDocument, TextSegment


def _doc(*texts):
    segments = [
        TextSegment(id=f"p{i}", text=t, source_type="docx_paragraph", metadata={"paragraph_index": i, "style": "Normal"})
        for i, t in enumerate(texts)
    ]
    return SourceDocument(segments=segments, source_format="docx", source_path="dummy.docx")


class TestDetectNerPersonInitialsRight:
    """HANDOFF_3, Данные для тестов, п.1 — расширение спана PERSON вправо инициалами."""

    def test_person_span_extended_right_with_initials(self, ner_detector_module, config_path):
        """HANDOFF_3, п.1: 'Иванов И.И.' -> PERSON [17,28) с инициалами включёнными вправо."""
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        persons = [e for e in entities if e.entity_type == "PERSON"]
        assert len(persons) == 1
        assert persons[0].original_text == "Иванов И.И."

    def test_org_found_alongside_person(self, ner_detector_module, config_path):
        """HANDOFF_3, п.1: тот же абзац также даёт ORG 'ООО «Ромашка»'."""
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        orgs = [e for e in entities if e.entity_type == "ORG"]
        assert len(orgs) == 1
        assert orgs[0].original_text == "ООО «Ромашка»"


class TestDetectNerPersonInitialsLeft:
    """HANDOFF_3, Данные для тестов, п.2 — расширение спана PERSON влево инициалами."""

    def test_person_span_extended_left_with_initials(self, ner_detector_module, config_path):
        """HANDOFF_3, п.2: 'П.П. Петров' -> PERSON с инициалами включёнными слева."""
        doc = _doc("Со стороны заказчика — П.П. Петров, директор ЗАО «Вектор».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        persons = [e for e in entities if e.entity_type == "PERSON"]
        assert any(e.original_text == "П.П. Петров" for e in persons)


class TestDetectNerAddressGluing:
    """HANDOFF_3, Данные для тестов, п.3 — склейка Match'ей AddrExtractor в единый ADDRESS."""

    def test_address_glues_city_street_house_office_into_one_span(self, ner_detector_module, config_path):
        """HANDOFF_3, п.3: 'г. Москва, ул. Ленина, д. 5, оф. 12' -> один ADDRESS [7,42)."""
        doc = _doc("Адрес: г. Москва, ул. Ленина, д. 5, оф. 12.")
        entities = ner_detector_module.detect_ner(doc, config_path)
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        combined = [e for e in addresses if e.original_text == "г. Москва, ул. Ленина, д. 5, оф. 12"]
        assert len(combined) == 1


class TestDetectNerAddressGlueBoundary:
    """HANDOFF_3, Данные для тестов, п.4 — склейка НЕ выполняется через разрыв с буквой."""

    def test_letter_gap_between_cities_prevents_gluing(self, ner_detector_module, config_path):
        """HANDOFF_3, п.4: 'поставка из Москвы в Санкт-Петербург' -> два раздельных ADDRESS (разрыв ' в ' содержит букву)."""
        doc = _doc("поставка из Москвы в Санкт-Петербург")
        entities = ner_detector_module.detect_ner(doc, config_path)
        addresses = {e.original_text for e in entities if e.entity_type == "ADDRESS"}
        assert "Москвы" in addresses
        assert "Санкт-Петербург" in addresses
        assert "Москвы в Санкт-Петербург" not in addresses


class TestDetectNerEmptySegment:
    """HANDOFF_3, Данные для тестов, п.5 — пустой сегмент не создаёт Entity."""

    def test_empty_segment_produces_no_entities(self, ner_detector_module, config_path):
        """HANDOFF_3, п.5: сегмент с text='' -> ни одного Entity (сегмент пропускается)."""
        doc = _doc("")
        entities = ner_detector_module.detect_ner(doc, config_path)
        assert entities == []


class TestDetectNerInvariants:
    """HANDOFF_3, Инварианты выходных данных."""

    def test_all_entities_have_detector_ner(self, ner_detector_module, config_path):
        """HANDOFF_3, Инварианты: у каждого Entity detector == 'ner'."""
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        assert all(e.detector == "ner" for e in entities)

    def test_all_entities_have_confidence_one(self, ner_detector_module, config_path):
        """HANDOFF_3, Инварианты: у каждого Entity confidence == 1.0 (Natasha не даёт score)."""
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        assert all(e.confidence == 1.0 for e in entities)

    def test_all_entities_have_token_none(self, ner_detector_module, config_path):
        """HANDOFF_3, Инварианты: у каждого Entity token is None (заполняется в блоке 4)."""
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        assert all(e.token is None for e in entities)

    def test_original_text_matches_segment_slice_invariant(self, ner_detector_module, config_path):
        """Спека, блок 3: инвариант entity.original_text == segment.text[start:end] для каждого Entity."""
        doc = _doc("Адрес: г. Москва, ул. Ленина, д. 5, оф. 12.", "Со стороны заказчика — П.П. Петров, директор ЗАО «Вектор».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        by_id = {s.id: s for s in doc.segments}
        for e in entities:
            seg = by_id[e.segment_id]
            assert e.original_text == seg.text[e.start:e.end]


class TestDetectNerConfigErrors:
    """HANDOFF_3, Публичный интерфейс — исключения detect_ner."""

    def test_missing_config_file_raises_file_not_found_error(self, ner_detector_module):
        """HANDOFF_3, Публичный интерфейс: FileNotFoundError, если config_path не существует."""
        doc = _doc("текст")
        with pytest.raises(FileNotFoundError):
            ner_detector_module.detect_ner(doc, "нет_такого_файла.yaml")

    def test_config_without_entity_types_key_raises_key_error(self, ner_detector_module, tmp_path):
        """HANDOFF_3, Публичный интерфейс: KeyError, если YAML не содержит верхнего ключа entity_types."""
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("something_else: {}\n", encoding="utf-8")
        doc = _doc("текст")
        with pytest.raises(KeyError):
            ner_detector_module.detect_ner(doc, str(cfg))
