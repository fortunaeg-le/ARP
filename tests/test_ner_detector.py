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
    """HANDOFF_3, п.1 — ФИО с инициалами одним спаном. ЭТАП B: PER больше НЕ даёт
    detect_ner (Natasha-PER выключена в конфиге, как Natasha-ORG на этапе A) — его
    даёт структурный движок anchor_registry (якорь по форме ФИО «фамилия + инициалы»).
    Инвариант «Иванов И.И. — ОДИН спан» сохранён, но источник сменился."""

    def test_person_span_extended_right_with_initials(self, config_path):
        """'Иванов И.И.' -> PERSON одним спаном с инициалами (структурный якорь)."""
        from anchor_registry import detect_structural
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        persons = [e for e in detect_structural(doc) if e.entity_type == "PERSON"]
        assert len(persons) == 1
        assert persons[0].original_text == "Иванов И.И."

    def test_org_found_alongside_person(self, ner_detector_module, config_path):
        """Этап A/B: ни ORG, ни PER больше не даёт detect_ner (обе метки Natasha
        выключены) — их даёт структурный движок anchor_registry.detect_structural.
        detect_ner на этом абзаце не должен давать НИ ORG, НИ PERSON; оба приходят из
        структуры. Утечки нет — и «ООО «Ромашка»», и «Иванов И.И.» маскируются."""
        from anchor_registry import detect_structural
        doc = _doc("Договор подписал Иванов И.И. от лица ООО «Ромашка».")
        entities = ner_detector_module.detect_ner(doc, config_path)
        assert [e for e in entities if e.entity_type == "ORG"] == []
        assert [e for e in entities if e.entity_type == "PERSON"] == []
        struct = detect_structural(doc)
        orgs = [e for e in struct if e.entity_type == "ORG"]
        pers = [e for e in struct if e.entity_type == "PERSON"]
        assert len(orgs) == 1 and orgs[0].original_text == "ООО «Ромашка»"
        assert any(p.original_text == "Иванов И.И." for p in pers)


class TestDetectNerPersonInitialsLeft:
    """HANDOFF_3, п.2 — инициалы СЛЕВА от фамилии, один спан (этап B: структурный)."""

    def test_person_span_extended_left_with_initials(self, config_path):
        """'П.П. Петров' -> PERSON одним спаном с инициалами слева (структурный якорь)."""
        from anchor_registry import detect_structural
        doc = _doc("Со стороны заказчика — П.П. Петров, директор ЗАО «Вектор».")
        persons = [e for e in detect_structural(doc) if e.entity_type == "PERSON"]
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
    """Этап C — КОНТРАКТ ИЗМЕНЁН относительно волны 2 (якорная модель адреса).

    Было (волна 2): два близких городских LOC-хита в прозе кластеризовались в один
    ADDRESS — «поставка из Москвы в Санкт-Петербург» метилась ADDRESS на голые города.
    Это ровно тот «одиночный топоним → мусорный ADDRESS», который этап C укрощает:
    голое имя города/страны БЕЗ адресного якоря (индекс / улица+дом / ≥2 маркера) —
    НЕ адрес, а топоним в прозе (симметрично «Российская Федерация», «города Москвы»
    из ТЗ этапа C). Точность важнее над-закрытия.

    Почему смена безопасна: голый топоним «Москвы» — НЕ ПДн (не адрес субъекта), его
    открытость не утечка персональных данных, а над-маскирование прозы — потеря
    читаемости. Реальный безмаркерный адрес («Москва, Ленина 5») несёт номер дома и
    ловится house-pattern-якорем (см. tests/test_golden_addresses.py). См.
    docs/archive/reports/HANDOFF_STAGE_C_ADDRESS.md."""

    def test_bare_city_cluster_in_prose_is_not_address(self, ner_detector_module, config_path):
        """Голые города в прозе (без индекса/улицы+дома) — НЕ ADDRESS (этап C, якорь)."""
        text = "поставка из Москвы в Санкт-Петербург"
        doc = _doc(text)
        entities = ner_detector_module.detect_ner(doc, config_path)
        addr_spans = [(e.start, e.end) for e in entities if e.entity_type == "ADDRESS"]
        assert not addr_spans, (
            f"голые города в прозе не должны метиться ADDRESS: "
            f"{[text[s:e] for s, e in addr_spans]}"
        )


class TestDetectNerAddressDoesNotSwallowName:
    """Волна 2, этап A (доп. отсев yargy-ложняков на ФИО).

    yargy иногда принимает ФИО за топоним; раньше кластеризация затягивала имя и метку
    «адрес:» в ADDRESS-токен («Иванов И.И., адрес: г. Москва…»), человек не получал
    своего PERSON-токена. Отсев подозрительных yargy-спанов (перекрывают PER/ORG без
    LOC/маркера) это чинит, не роняя полноту адреса (см. GOLDEN)."""

    def test_address_does_not_absorb_preceding_person_and_label(self, ner_detector_module, config_path):
        # ЭТАП B: ФИО даёт структурный движок, он же служит барьером адресу (per_entities).
        from anchor_registry import detect_structural
        doc = _doc("в лице директора Иванов И.И., адрес: г. Москва, ул. Ленина, д. 5")
        struct = detect_structural(doc)
        per = [e for e in struct if e.entity_type == "PERSON"]
        entities = ner_detector_module.detect_ner(doc, config_path, per_entities=per)
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        # ФИО осталось отдельным PERSON, а не затянулось в адрес
        assert any("Иванов" in p.original_text for p in per)
        assert all("Иванов" not in a.original_text for a in addresses)
        # адрес закрыт целиком и начинается с города (метка «адрес:» не проглочена)
        assert any(a.original_text == "г. Москва, ул. Ленина, д. 5" for a in addresses), (
            [a.original_text for a in addresses]
        )


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
