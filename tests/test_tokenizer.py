"""Тесты блока 4 — tokenizer.py (tokenize, build_plain_text)."""
import uuid

import pytest

from models import Entity, SourceDocument, TextSegment
from tokenizer import tokenize, build_plain_text


def _entity(segment_id, start, end, original_text, entity_type, detector, confidence=1.0):
    return Entity(
        id=str(uuid.uuid4()),
        segment_id=segment_id,
        start=start,
        end=end,
        original_text=original_text,
        entity_type=entity_type,
        detector=detector,
        confidence=confidence,
    )


def _seg(seg_id, text, source_type="docx_paragraph", metadata=None):
    if metadata is None:
        metadata = {"paragraph_index": 0, "style": "Normal"}
    return TextSegment(id=seg_id, text=text, source_type=source_type, metadata=metadata)


class TestTokenizeReusesTokenOnExactMatch:
    """HANDOFF_4, Данные для тестов, п.1 — приёмка: переиспользование токена."""

    def test_two_identical_org_mentions_get_same_token(self, config_path):
        """Спека, блок 4, Приёмка: 2 упоминания одной организации в одинаковом написании -> один и тот же токен [ORG_1]."""
        p0 = _seg("p0", "Договор с ООО «Ромашка», ИНН 7707083893.")
        p1 = _seg("p1", "Сторона ООО «Ромашка» подтверждает.")
        doc = SourceDocument(segments=[p0, p1], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 11, 24, "ООО «Ромашка»", "ORG", "ner"),
            _entity("p0", 29, 39, "7707083893", "INN", "regex"),
            _entity("p1", 8, 21, "ООО «Ромашка»", "ORG", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        org_tokens = {e.token for e in result if e.entity_type == "ORG"}
        assert org_tokens == {"[ORG_1]"}

    def test_inn_gets_its_own_token(self, config_path):
        """HANDOFF_4, п.1: ИНН из того же документа получает токен [INN_1]."""
        p0 = _seg("p0", "Договор с ООО «Ромашка», ИНН 7707083893.")
        p1 = _seg("p1", "Сторона ООО «Ромашка» подтверждает.")
        doc = SourceDocument(segments=[p0, p1], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 11, 24, "ООО «Ромашка»", "ORG", "ner"),
            _entity("p0", 29, 39, "7707083893", "INN", "regex"),
            _entity("p1", 8, 21, "ООО «Ромашка»", "ORG", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        inn = [e for e in result if e.entity_type == "INN"]
        assert inn[0].token == "[INN_1]"

    def test_result_length_matches_input_when_no_overlaps(self, config_path):
        """HANDOFF_4, п.1: len(result)==3, ни один Entity не потерян (нет пересечений)."""
        p0 = _seg("p0", "Договор с ООО «Ромашка», ИНН 7707083893.")
        p1 = _seg("p1", "Сторона ООО «Ромашка» подтверждает.")
        doc = SourceDocument(segments=[p0, p1], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 11, 24, "ООО «Ромашка»", "ORG", "ner"),
            _entity("p0", 29, 39, "7707083893", "INN", "regex"),
            _entity("p1", 8, 21, "ООО «Ромашка»", "ORG", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        assert len(result) == 3

    def test_anonymized_text_contains_no_original_values(self, config_path):
        """Спека, блок 4, Приёмка: финальный текст не содержит оригинальных значений."""
        p0 = _seg("p0", "Договор с ООО «Ромашка», ИНН 7707083893.")
        p1 = _seg("p1", "Сторона ООО «Ромашка» подтверждает.")
        doc = SourceDocument(segments=[p0, p1], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 11, 24, "ООО «Ромашка»", "ORG", "ner"),
            _entity("p0", 29, 39, "7707083893", "INN", "regex"),
            _entity("p1", 8, 21, "ООО «Ромашка»", "ORG", "ner"),
        ]
        anon_text, _ = tokenize(doc, entities, config_path)
        assert "ООО «Ромашка»" not in anon_text
        assert "7707083893" not in anon_text

    def test_different_original_text_gets_different_token(self, config_path):
        """Спека, блок 4: токен переиспользуется только при точном посимвольном совпадении original_text."""
        p0 = _seg("p0", "ООО «Ромашка» и ООО РОМАШКА оба упомянуты.")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 0, 13, "ООО «Ромашка»", "ORG", "ner"),
            _entity("p0", 16, 27, "ООО РОМАШКА", "ORG", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        tokens = {e.token for e in result}
        assert len(tokens) == 2


class TestTokenizeOverlapResolutionKppBik:
    """HANDOFF_4, Данные для тестов, п.2 — KPP/BIK конфликт (граничный)."""

    def test_bik_wins_over_kpp_at_equal_length(self, config_path):
        """HANDOFF_4, п.2: KPP и BIK равной длины на одном интервале -> побеждает BIK (раньше в приоритете)."""
        seg = _seg("p0", "БИК 044525225")
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 4, 13, "044525225", "KPP", "regex"),
            _entity("p0", 4, 13, "044525225", "BIK", "regex"),
        ]
        _, result = tokenize(doc, entities, config_path)
        assert len(result) == 1
        assert result[0].entity_type == "BIK"

    def test_only_one_token_appears_in_text_for_overlapping_kpp_bik(self, config_path):
        """HANDOFF_4, п.2: в тексте ровно один токен [BIK_1] на этом месте, не два наложенных."""
        seg = _seg("p0", "БИК 044525225")
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 4, 13, "044525225", "KPP", "regex"),
            _entity("p0", 4, 13, "044525225", "BIK", "regex"),
        ]
        anon_text, _ = tokenize(doc, entities, config_path)
        assert anon_text == "БИК [BIK_1]"


class TestTokenizeOverlapResolutionChain:
    """HANDOFF_4, Данные для тестов, п.3 — цепочка A-B-C, A∩B, B∩C, A∩C=∅."""

    def test_middle_regex_winner_trims_both_neighbors(self, config_path):
        """W2-D1(б): победитель B (INN, regex) пересекается с A и C. РАНЬШЕ обе соседние
        сущности выбрасывались целиком (остаётся только INN) — это была мина: если сосед
        нёс ПДн вне зоны пересечения (адрес слева от ИНН), он утекал открытым текстом.
        ТЕПЕРЬ проигравший ОБРЕЗАЕТСЯ до непересекающейся части: INN покрывает [4,12],
        PERSON сохраняет свой хвост [0,4], ORG — [12,16]. Наложений не остаётся."""
        seg = _seg("p0", "AAAAAAAAAAAAAAAA")  # текст не важен для алгоритма пересечений
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 0, 6, "AAAAAA", "PERSON", "ner"),
            _entity("p0", 4, 12, "AAAAAAAA", "INN", "regex"),
            _entity("p0", 10, 16, "AAAAAA", "ORG", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        spans = sorted((e.start, e.end, e.entity_type) for e in result)
        assert spans == [(0, 4, "PERSON"), (4, 12, "INN"), (12, 16, "ORG")]
        # ни одной пересекающейся пары среди выживших
        assert not any(
            max(a[0], b[0]) < min(a[1], b[1])
            for i, a in enumerate(spans) for b in spans[i + 1:]
        )

    def test_non_overlapping_winner_lets_the_third_entity_survive(self, config_path):
        """HANDOFF_4, п.3 (обратный случай): если победитель C не пересекается с A напрямую, A обязан выжить."""
        seg = _seg("p0", "AAAAAAAAAAAAAAAAAAAA")
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="d.docx")
        # A=[0,6) PERSON(ner), B=[4,10) ORG(ner) пересекается с A, C=[12,18) ADDRESS(ner) не пересекается ни с кем
        entities = [
            _entity("p0", 0, 6, "AAAAAA", "PERSON", "ner"),
            _entity("p0", 4, 10, "AAAAAA", "ORG", "ner"),
            _entity("p0", 12, 18, "AAAAAA", "ADDRESS", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        types = {e.entity_type for e in result}
        # ADDRESS не пересекается ни с кем -> обязан выжить и получить токен
        assert "ADDRESS" in types


class TestTokenizeTableAssembly:
    """HANDOFF_4, Данные для тестов, п.4 — сборка таблицы (build_plain_text)."""

    def test_build_plain_text_assembles_heading_table_tail(self):
        """HANDOFF_4, п.4: 'Заголовок\\nA1 | B1 B2\\nC1 | D1\\nХвост' — многострочная ячейка B1\\nB2 склеена пробелом."""
        heading = _seg("p0", "Заголовок", metadata={"paragraph_index": 0, "style": "Normal"})
        t_r0_c0 = _seg("t0_r0_c0", "A1", source_type="docx_table_cell", metadata={"table_index": 0, "row_index": 0, "col_index": 0})
        t_r0_c1 = _seg("t0_r0_c1", "B1\nB2", source_type="docx_table_cell", metadata={"table_index": 0, "row_index": 0, "col_index": 1})
        t_r1_c0 = _seg("t0_r1_c0", "C1", source_type="docx_table_cell", metadata={"table_index": 0, "row_index": 1, "col_index": 0})
        t_r1_c1 = _seg("t0_r1_c1", "D1", source_type="docx_table_cell", metadata={"table_index": 0, "row_index": 1, "col_index": 1})
        tail = _seg("p1", "Хвост", metadata={"paragraph_index": 1, "style": "Normal"})
        doc = SourceDocument(segments=[heading, t_r0_c0, t_r0_c1, t_r1_c0, t_r1_c1, tail], source_format="docx", source_path="d.docx")
        result = build_plain_text(doc)
        assert result == "Заголовок\nA1 | B1 B2\nC1 | D1\nХвост"

    def test_build_plain_text_has_no_trailing_newline(self):
        """Спека, блок 4: между верхнеуровневыми единицами ровно один '\\n', без хвостового '\\n'."""
        p0 = _seg("p0", "Первый")
        p1 = _seg("p1", "Второй")
        doc = SourceDocument(segments=[p0, p1], source_format="docx", source_path="d.docx")
        result = build_plain_text(doc)
        assert not result.endswith("\n")


class TestTokenizeInvariants:
    """HANDOFF_4, Инварианты выходных данных."""

    def test_all_result_entities_have_nonempty_token(self, config_path):
        """Спека, блок 4: all(e.token for e in result) — каждый возвращённый Entity имеет непустой token."""
        seg = _seg("p0", "ИНН 7707083893")
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="d.docx")
        entities = [_entity("p0", 4, 14, "7707083893", "INN", "regex")]
        _, result = tokenize(doc, entities, config_path)
        assert all(e.token for e in result)

    def test_result_sorted_by_segment_index_then_start(self, config_path):
        """Спека, блок 4: список отсортирован по (индекс сегмента, start)."""
        p0 = _seg("p0", "b начало a конец", metadata={"paragraph_index": 0, "style": "Normal"})
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 10, 11, "a", "PERSON", "ner"),
            _entity("p0", 0, 1, "b", "ORG", "ner"),
        ]
        _, result = tokenize(doc, entities, config_path)
        starts = [e.start for e in result]
        assert starts == sorted(starts)

    def test_token_numbering_is_sequential_per_prefix_starting_at_one(self, config_path):
        """Спека, блок 4: нумерация N сквозная по token_prefix, начиная с 1."""
        p0 = _seg("p0", "ИНН 7707083893 и ОГРН 1027700132195")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 4, 14, "7707083893", "INN", "regex"),
            _entity("p0", 22, 35, "1027700132195", "OGRN", "regex"),
        ]
        _, result = tokenize(doc, entities, config_path)
        by_type = {e.entity_type: e.token for e in result}
        assert by_type["INN"] == "[INN_1]"
        assert by_type["OGRN"] == "[OGRN_1]"

    def test_token_format_uses_token_prefix_not_entity_type(self, config_path):
        """Спека, блок 4: TYPE в токене — token_prefix из конфига (BANK_ACCOUNT -> ACCOUNT), не entity_type."""
        p0 = _seg("p0", "40702810400000012345")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        entities = [_entity("p0", 0, 20, "40702810400000012345", "BANK_ACCOUNT", "regex")]
        _, result = tokenize(doc, entities, config_path)
        assert result[0].token == "[ACCOUNT_1]"

    def test_no_overlapping_intervals_remain_within_a_segment(self, config_path):
        """Спека, блок 4: внутри сегмента у результата нет двух Entity с пересекающимися интервалами."""
        seg = _seg("p0", "БИК 044525225")
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="d.docx")
        entities = [
            _entity("p0", 4, 13, "044525225", "KPP", "regex"),
            _entity("p0", 4, 13, "044525225", "BIK", "regex"),
        ]
        _, result = tokenize(doc, entities, config_path)
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                a, b = result[i], result[j]
                assert max(a.start, b.start) >= min(a.end, b.end)


class TestTokenizeErrors:
    """HANDOFF_4, Публичный интерфейс — исключения tokenize."""

    def test_missing_config_file_raises_file_not_found_error(self):
        """HANDOFF_4, Публичный интерфейс: FileNotFoundError, если config_path не существует."""
        p0 = _seg("p0", "текст")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        with pytest.raises(FileNotFoundError):
            tokenize(doc, [], "нет_такого_файла.yaml")

    def test_unknown_entity_type_raises_key_error(self, config_path):
        """HANDOFF_4, Публичный интерфейс: KeyError, если entity_type Entity отсутствует в конфиге (нет token_prefix)."""
        p0 = _seg("p0", "текст")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        entities = [_entity("p0", 0, 4, "текс", "UNKNOWN_TYPE", "regex")]
        with pytest.raises(KeyError):
            tokenize(doc, entities, config_path)

    def test_build_plain_text_raises_no_exception_on_valid_document(self):
        """HANDOFF_4, Публичный интерфейс: build_plain_text исключений при корректном SourceDocument не кидает."""
        p0 = _seg("p0", "текст")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        build_plain_text(doc)  # не должно бросить исключение


class TestTokenizeEmptyInput:
    """Граничный случай: пустой список entities."""

    def test_empty_entities_list_returns_untouched_text_and_empty_result(self, config_path):
        """Граничный случай: tokenize(doc, [], config_path) -> текст без изменений, пустой result."""
        p0 = _seg("p0", "Просто текст без сущностей.")
        doc = SourceDocument(segments=[p0], source_format="docx", source_path="d.docx")
        anon_text, result = tokenize(doc, [], config_path)
        assert anon_text == "Просто текст без сущностей."
        assert result == []
