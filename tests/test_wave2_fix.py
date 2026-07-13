# -*- coding: utf-8 -*-
"""Волна 2, ФИКС W2-D1/D2/D3 — приёмочные тесты сверх пришпиливающих (overcapture).

Здесь:
  • путь (б) — обрезка проигравшей сущности при пересечении, СОЗДАННОМ НЕ расширением
    адреса (доказательство, что страховка не мёртвый код);
  • round-trip сценария D1 — оба поля токенизированы и восстановлены посимвольно;
  • негативы D3 БЕЗ союза «и» — юкстапозиция «лицо + организация» не сливается в ORG.
"""
import os
import uuid

import pytest

from models import Entity, SourceDocument, TextSegment
from regex_detector import detect_regex
from ner_detector import detect_ner
from syntax_compound import merge_compound_entities
from tokenizer import tokenize
from session_store import save_session
from detokenizer import detokenize

_CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _entity(seg_id, s, e, text, etype, detector, conf=1.0):
    return Entity(
        id=str(uuid.uuid4()), segment_id=seg_id, start=s, end=e,
        original_text=text, entity_type=etype, detector=detector, confidence=conf,
    )


# --------------------------------------------------------------------------- #
# Путь (б): пересечение, созданное НЕ расширением адреса -> проигравший ОБРЕЗАН,
# а не выброшен. Расширение адреса тут ни при чём — сущности заданы напрямую.
# --------------------------------------------------------------------------- #
def test_pathb_trim_when_overlap_not_from_address_expansion():
    """PERSON, частично перекрытый regex-INN (пересечение НЕ от адреса). regex
    побеждает (правило 1), но непересекающийся ХВОСТ PERSON обязан сохранить свой
    токен, а не утечь открытым текстом. Если бы (б) был мёртвым кодом — «Иванов»
    (левее INN) остался бы в анонимном тексте открытым."""
    text = "Иванов1234567890конец"
    seg = TextSegment(id="p0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<b>")
    # PERSON [0,10] = "Иванов1234" пересекает INN [6,16] = "1234567890"
    ents = [
        _entity("p0", 0, 10, text[0:10], "PERSON", "ner"),
        _entity("p0", 6, 16, text[6:16], "INN", "regex"),
    ]
    anon, final = tokenize(doc, ents, _CFG)

    # INN победил и покрыл [6,16]; PERSON обрезан до [0,6] = "Иванов", свой токен есть.
    persons = [e for e in final if e.entity_type == "PERSON"]
    assert persons, "проигравший PERSON выброшен целиком — путь (б) мёртв"
    assert persons[0].original_text == "Иванов"
    assert (persons[0].start, persons[0].end) == (0, 6)
    # «Иванов» НЕ утёк открытым текстом (он под токеном), и наложений нет
    assert "Иванов" not in anon
    assert any(e.entity_type == "INN" for e in final)


def test_pathb_winner_inside_loser_splits_into_two():
    """Победитель ВНУТРИ проигравшего -> проигравший обрезается в ДВА фрагмента
    (левый и правый), оба сохраняют токены. Крайний случай обрезки."""
    text = "ЛевоеСЛОВО12345Правое"
    seg = TextSegment(id="p0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<b>")
    # ORG [0,21] целиком, INN [10,15] = "12345" внутри
    ents = [
        _entity("p0", 0, 21, text, "ORG", "ner"),
        _entity("p0", 10, 15, text[10:15], "INN", "regex"),
    ]
    _, final = tokenize(doc, ents, _CFG)
    orgs = sorted(((e.start, e.end) for e in final if e.entity_type == "ORG"))
    assert orgs == [(0, 10), (15, 21)], orgs  # два непересекающихся куска
    assert any(e.entity_type == "INN" for e in final)


# --------------------------------------------------------------------------- #
# Round-trip сценария D1: адрес + ИНН на одной строке -> оба поля токенизированы
# и восстановлены ПОСИМВОЛЬНО.
# --------------------------------------------------------------------------- #
def test_d1_address_plus_inn_roundtrip_charwise(tmp_path):
    text = "г. Казань, ул. Баумана, д. 44, ИНН 7707083893"
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<d1>")

    regex_entities = detect_regex(doc, _CFG)
    ner_entities = detect_ner(doc, _CFG, regex_entities=regex_entities)
    ents = merge_compound_entities(doc, regex_entities + ner_entities)
    anon, final = tokenize(doc, ents, _CFG)

    types = {e.entity_type for e in final}
    assert "ADDRESS" in types, f"адрес не токенизирован: {[(e.entity_type,e.original_text) for e in final]}"
    assert "INN" in types, "ИНН не токенизирован"
    # идентифицирующие фрагменты не утекли открытым текстом
    assert "Казань" not in anon and "Баумана" not in anon
    assert "7707083893" not in anon

    # round-trip через сессию: восстановление посимвольно точно
    storage = tmp_path / "sessions"
    session_id = save_session(final, storage_dir=str(storage))
    restored, unresolved = detokenize(anon, session_id, storage_dir=str(storage))
    assert unresolved == [], f"неразрешённые токены: {unresolved}"
    assert restored == text, f"round-trip разошёлся:\n ждали:   {text!r}\n получили: {restored!r}"


# --------------------------------------------------------------------------- #
# D3 негативы БЕЗ союза «и»: юкстапозиция «лицо + организация» не сливается в ORG.
# Синтаксический критерий (направление appos: ORG-вершина => склейка), а не список.
# --------------------------------------------------------------------------- #
def _orgs(text):
    seg = TextSegment(id="s", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<n>")
    ents = detect_regex(doc, _CFG) + detect_ner(doc, _CFG)
    ents = merge_compound_entities(doc, ents)
    return [e.original_text for e in ents if e.entity_type == "ORG"]


@pytest.mark.parametrize("text,surname", [
    ("Директор Сидоров С.С. ООО «Вектор» подтвердил", "Сидоров"),      # роль «директор»
    ("Представитель Козлов К.К. АО «Мир» сообщил", "Козлов"),          # иная роль
    ("Бухгалтер Смирнова А.А. ЗАО «Спектр» составила", "Смирнова"),    # ещё одна роль
])
def test_d3_role_person_before_org_not_merged(text, surname):
    """«роль ФИО ORG» без союза: ORG приложена К человеку (ORG-зависимый) -> НЕ склейка.
    Ни одна ORG-сущность не должна содержать фамилию отдельной стороны."""
    orgs = _orgs(text)
    assert not any(surname in o for o in orgs), f"ФИО слито в ORG: {orgs!r}"


def test_d3_no_role_word_person_before_org_not_merged():
    """Без ролевого слова: «Пирогова А.С. ООО «Ромашка»» — два разных объекта рядом.
    ORG не должна поглотить ФИО (проверяем именно отсутствие ложной склейки)."""
    orgs = _orgs("Пирогова А.С. ООО «Ромашка» отгрузила товар")
    assert not any("Пирогова" in o for o in orgs), f"ФИО слито в ORG: {orgs!r}"


def test_d3_separate_sentences_person_and_org_not_merged():
    """Разные предложения: «Подписал Иванов И.И. Поставщик — ООО «Ромашка».»
    Разрыв предложением исключает склейку тем более."""
    orgs = _orgs("Подписал Иванов И.И. Поставщик — ООО «Ромашка».")
    assert not any("Иванов" in o for o in orgs), f"ФИО слито в ORG: {orgs!r}"


def test_d3_adjacent_table_cells_person_and_org_not_merged():
    """Соседние ячейки строки таблицы: ФИО в одной, ORG в другой. Разные сегменты —
    appos-склейка (внутрисегментная) не применяется; ORG не содержит ФИО."""
    cells = [
        TextSegment(id="c0", text="Свидетель Иванов И.И.", source_type="docx_table_cell",
                    metadata={"table_index": 0, "row_index": 0, "col_index": 0}),
        TextSegment(id="c1", text="ООО «Ромашка»", source_type="docx_table_cell",
                    metadata={"table_index": 0, "row_index": 0, "col_index": 1}),
    ]
    doc = SourceDocument(segments=cells, source_format="docx", source_path="<t>")
    ents = detect_regex(doc, _CFG) + detect_ner(doc, _CFG)
    ents = merge_compound_entities(doc, ents)
    orgs = [e.original_text for e in ents if e.entity_type == "ORG"]
    assert not any("Иванов" in o for o in orgs), f"ФИО слито в ORG: {orgs!r}"


def test_d3_line_break_between_person_and_org_not_merged():
    """Перенос строки между ФИО и ORG (разные txt_line-сегменты): склейки нет."""
    segs = [
        TextSegment(id="l0", text="Свидетель Иванов И.И.", source_type="txt_line",
                    metadata={"line_index": 0}),
        TextSegment(id="l1", text="ООО «Ромашка»", source_type="txt_line",
                    metadata={"line_index": 1}),
    ]
    doc = SourceDocument(segments=segs, source_format="txt", source_path="<t>")
    ents = detect_regex(doc, _CFG) + detect_ner(doc, _CFG)
    ents = merge_compound_entities(doc, ents)
    orgs = [e.original_text for e in ents if e.entity_type == "ORG"]
    assert not any("Иванов" in o for o in orgs), f"ФИО слито в ORG: {orgs!r}"
