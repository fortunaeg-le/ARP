# -*- coding: utf-8 -*-
"""ЭТАП T4 — отрицательные классы CLAUSE_REF/ROLE_TERM/COLLECTIVE.

Карта механизмов, контракт классов, приёмка — docs/T4_REPORT.md. Группы:

  1. TestClauseRef — regex-форма: номер пункта/раздела/приложения; контроль,
     что дата (4-значная группа) НЕ матчится (иначе барьер полез бы на
     BIRTHDATE/DATE).
  2. TestRoleTermAndCollective — формализация M2/M1b (anchor_registry):
     «именуемое в дальнейшем «Роль»» -> ROLE_TERM; org-форма + собирательная
     вершина -> COLLECTIVE. Контроль байт-в-байт: ORG/PERSON не изменились.
  3. TestNeverMasked — отрицательные классы никогда не получают токен, не
     попадают в kept/сессию.
  4. TestToggle — «разворот»: suppress_masking: false ИЛИ enabled: false
     убирает барьер целиком, маски соседних типов возвращаются.
  5. TestSuppressionMirror — зеркало подавления: событие пишется с нужными
     полями; КРАСНОЕ СОСТОЯНИЕ приёмки — искусственный пример, где барьер
     задевает эталонное ФИО, показан через measure_lib.suppressed_gold_entities
     (тот же путь, что использует гейт).
"""
import os

import pytest
import yaml

import tokenizer as TOK
from models import Entity, SourceDocument, TextSegment
from pipeline import run_detection
from tokenizer import tokenize

_CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _doc(text, source_type="p"):
    seg = TextSegment(id="p0", text=text, source_type=source_type, metadata={})
    return SourceDocument(segments=[seg], source_format="docx", source_path="<t4>")


def _detect(text):
    return run_detection(_doc(text), _CFG)


def _types_at(text):
    return sorted((e.entity_type, e.original_text) for e in _detect(text))


def _cfg_with_override(tmp_path, type_name, **overrides):
    """Копия боевого entity_types.yaml с точечной правкой ОДНОГО типа (флаги
    suppress_masking/enabled) — для теста разворота, не трогая сам файл."""
    with open(_CFG, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["entity_types"][type_name] = {**config["entity_types"][type_name], **overrides}
    p = tmp_path / "cfg.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True)
    return str(p)


# =========================================================================== #
#  1. CLAUSE_REF
# =========================================================================== #

class TestClauseRef:

    @pytest.mark.parametrize("text,expected", [
        ("Стороны обязуются исполнить п. 3.2 договора.", "3.2"),
        ("Согласно пункту 7.1.4 настоящего договора.", "7.1.4"),
        ("Приложение № 1 является неотъемлемой частью.", "Приложение № 1"),
        ("В соответствии с разделом 5 договора.", "разделом 5"),
    ])
    def test_plus_matches_clause_forms(self, text, expected):
        found = [e.original_text for e in _detect(text) if e.entity_type == "CLAUSE_REF"]
        assert any(expected.lower() in f.lower() or f.lower() in expected.lower()
                   for f in found), (text, found)

    def test_minus_does_not_match_inside_a_birthdate(self):
        """(−) КРИТИЧНО: дата (4-значный год) не должна матчиться CLAUSE_REF —
        иначе барьер начал бы конкурировать с BIRTHDATE/DATE (ENTITY_SPEC §4.1:
        различитель именно «нет группы ≥4 цифр»)."""
        text = "Дата рождения: 21.01.1990."
        found = [e for e in _detect(text) if e.entity_type == "CLAUSE_REF"]
        assert not found, found
        # BIRTHDATE сам при этом детектируется как обычно (не задет барьером).
        assert any(e.entity_type == "BIRTHDATE" for e in _detect(text))

    def test_minus_does_not_swallow_a_three_part_date(self):
        text = "Документ подписан 15.03.2024 года."
        found = [e for e in _detect(text) if e.entity_type == "CLAUSE_REF"
                 and "2024" in e.original_text]
        assert not found, found

    def test_clause_ref_has_no_token_prefix(self):
        with open(_CFG, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert "token_prefix" not in config["entity_types"]["CLAUSE_REF"]


# =========================================================================== #
#  2. ROLE_TERM / COLLECTIVE
# =========================================================================== #

class TestRoleTermAndCollective:

    def test_role_term_at_introduction_construction(self):
        text = ('ООО «Ромашка», именуемое в дальнейшем «Заказчик», с одной '
                'стороны, и Петров Пётр Петрович, именуемый в дальнейшем '
                '«Исполнитель», с другой стороны, заключили договор.')
        found = {e.original_text for e in _detect(text) if e.entity_type == "ROLE_TERM"}
        assert any("Заказчик" in f for f in found), found
        assert any("Исполнитель" in f for f in found), found
        # Название организации И ФИО остаются собой, не проглочены барьером.
        types = _types_at(text)
        assert ("ORG", "ООО «Ромашка»") in types
        assert any(t == "PERSON" and "Петров Пётр Петрович" in txt for t, txt in types)

    def test_minus_role_term_does_not_fire_on_bare_prose_mention(self):
        """(−) Класс сознательно узкий (см. диалог этапа): голое упоминание
        роли в теле документа, БЕЗ конструкции введения, не эмитится вовсе —
        сегодня там и так никто не пытается что-то анкорить."""
        text = "Заказчик обязуется принять и оплатить работы в течение 10 дней."
        found = [e for e in _detect(text) if e.entity_type == "ROLE_TERM"]
        assert not found, found

    def test_collective_after_org_form_plural_head(self):
        """Реальный класс дефекта C′ (ENTITY_SPEC §4.3): «...Закона об АО
        Акционеры договорились...» — «АО» отсылает к закону, «Акционеры»
        начинает новую клаузу; org-форма + собирательная вершина -> COLLECTIVE."""
        text = "В соответствии с положениями Закона об АО Акционеры утвердили решение."
        found = [e.original_text for e in _detect(text) if e.entity_type == "COLLECTIVE"]
        assert any("Акционеры" in f for f in found), found

    def test_minus_collective_does_not_fire_on_singular_org_name(self):
        """(−) Сингулярное имя за той же формой — обычная организация, не
        собирательное; барьер не должен на неё покушаться."""
        text = "В соответствии с положениями Закона об АО Восход заключён договор."
        found = [e for e in _detect(text) if e.entity_type == "COLLECTIVE"]
        assert not found, found

    def test_role_term_and_collective_have_no_token_prefix(self):
        with open(_CFG, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert "token_prefix" not in config["entity_types"]["ROLE_TERM"]
        assert "token_prefix" not in config["entity_types"]["COLLECTIVE"]


# =========================================================================== #
#  3. Никогда не маскируются
# =========================================================================== #

class TestNeverMasked:

    def test_negative_class_entities_never_receive_a_token(self):
        text = ('ООО «Ромашка», именуемое в дальнейшем «Заказчик». '
                'См. п. 3.2 договора.')
        entities = _detect(text)
        anon, kept = tokenize(_doc(text), entities, _CFG)
        assert all(e.token is not None for e in kept)
        assert not any(e.entity_type in ("CLAUSE_REF", "ROLE_TERM", "COLLECTIVE")
                       for e in kept)
        assert "[CLAUSE_REF" not in anon
        assert "[ROLE_TERM" not in anon
        assert "[COLLECTIVE" not in anon

    def test_apply_masking_does_not_crash_on_barrier_types(self):
        """Регресс-страж: apply_masking раньше делало token_prefixes[entity_type]
        БЕЗУСЛОВНО — тип без token_prefix кидал бы KeyError."""
        text = "См. п. 3.2 договора."
        entities = _detect(text)
        assert any(e.entity_type == "CLAUSE_REF" for e in entities)
        tokenize(_doc(text), entities, _CFG)  # не должно бросать

    def test_genuinely_unknown_type_still_raises_key_error(self):
        """РЕГРЕСС-СТРАЖ (пойман на этом этапе, живой): первая версия фильтра
        отсекала «entity_type НЕ в token_prefixes» ЦЕЛИКОМ — это заодно тихо
        глотало ЛЮБОЙ по-настоящему неизвестный тип (опечатка/дефект в
        конвейере), а не только объявленные CLAUSE_REF/ROLE_TERM/COLLECTIVE.
        Именно такой тихий пропуск НЕОБЪЯВЛЕННОГО типа — молчаливое исчезновение
        маски, от которого этап обязан защищать, а не создавать (сломал
        существовавший до T4 tests/test_tokenizer.py::TestTokenizeErrors::
        test_unknown_entity_type_raises_key_error). Фильтр обязан быть точечным
        — по `_barrier_types` (объявленным классам), не по «нет token_prefix»."""
        from tokenizer import apply_masking

        bogus = Entity(id="z", segment_id="p0", start=0, end=4,
                       original_text="текс", entity_type="TOTALLY_UNKNOWN_TYPE",
                       detector="regex", confidence=1.0)
        with pytest.raises(KeyError):
            apply_masking(_doc("текст документа"), [bogus], _CFG)


# =========================================================================== #
#  4. Разворот
# =========================================================================== #

class TestToggle:

    def test_suppress_masking_false_removes_the_barrier(self, tmp_path):
        cfg = _cfg_with_override(tmp_path, "CLAUSE_REF", suppress_masking=False)
        found = [e for e in run_detection(_doc("См. п. 3.2 договора."), cfg)
                 if e.entity_type == "CLAUSE_REF"]
        assert not found, found

    def test_enabled_false_also_removes_the_barrier(self, tmp_path):
        cfg = _cfg_with_override(tmp_path, "ROLE_TERM", enabled=False)
        text = 'ООО «Ромашка», именуемое в дальнейшем «Заказчик».'
        found = [e for e in run_detection(_doc(text), cfg) if e.entity_type == "ROLE_TERM"]
        assert not found, found

    def test_toggle_off_does_not_affect_other_classes(self, tmp_path):
        """Выключатель точечный — по одному типу, не по всему механизму."""
        cfg = _cfg_with_override(tmp_path, "ROLE_TERM", suppress_masking=False)
        text = "В соответствии с положениями Закона об АО Акционеры утвердили решение."
        found = [e for e in run_detection(_doc(text), cfg) if e.entity_type == "COLLECTIVE"]
        assert found, "COLLECTIVE погас вместе с выключенным ROLE_TERM"


# =========================================================================== #
#  5. Зеркало подавления
# =========================================================================== #

class TestSuppressionMirror:

    def test_mirror_records_event_with_required_fields(self):
        """Прямой юнит-тест МЕХАНИЗМА ЗАПИСИ (не полагается на то, что реальные
        классы сегодня с чем-то реально сталкиваются — на корпусе их 0, см.
        docs/T4_REPORT.md §0). Кто именно победит пересечение, решают ОБЫЧНЫЕ
        правила _winner (длина и т.д., см. docs/T_ARB_REPORT.md) — здесь барьер
        сделан заведомо длиннее, чтобы изолированно проверить, что запись
        появляется, когда он побеждает, а не переоткрывать сам арбитраж."""
        barrier = Entity(id="x", segment_id="s0", start=0, end=20,
                         original_text="Заказчик-XXXXXXXXXXX", entity_type="ROLE_TERM",
                         detector="ner", confidence=1.0)
        real = Entity(id="y", segment_id="s0", start=5, end=15,
                      original_text="X-Петров П", entity_type="PERSON",
                      detector="ner", confidence=1.0)
        log = []
        TOK._resolve_overlaps([barrier, real], frozenset({"ROLE_TERM"}), log)
        assert len(log) == 1, log
        entry = log[0]
        assert entry["suppressed_type"] == "PERSON"
        assert entry["suppressor_type"] == "ROLE_TERM"
        assert entry["segment_id"] == "s0"
        assert (entry["start"], entry["end"]) == (5, 15)   # пересечение, не весь loser

    def test_mirror_direction_matters_real_type_winning_is_not_suppression(self):
        """Обратное направление (маскируемый тип побеждает барьерный) — это
        обычный арбитраж T-ARB, не T4-подавление: запись НЕ пишется."""
        barrier = Entity(id="x", segment_id="s0", start=0, end=5,
                         original_text="Закaз", entity_type="ROLE_TERM",
                         detector="ner", confidence=1.0)
        real = Entity(id="y", segment_id="s0", start=0, end=20,
                      original_text="Петров Пётр Петрович", entity_type="PERSON",
                      detector="ner", confidence=1.0)
        log = []
        TOK._resolve_overlaps([barrier, real], frozenset({"ROLE_TERM"}), log)
        assert log == []

    def test_red_state_suppressed_gold_entity_is_detected_by_the_mirror(self):
        """ПРИЁМКА п.1 — искусственный пример, где класс задевает эталонное
        ФИО: measure_lib.suppressed_gold_entities (тот же путь, что зовёт
        gate.py, линия «ж») обязана найти пересечение и не быть пустой."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "corpus"))
        import measure_lib as ML

        suppressions_global = [{
            "suppressed_type": "PERSON", "suppressor_type": "ROLE_TERM",
            "segment_id": "p0", "start": 5, "end": 12,
            "gstart": 105, "gend": 112, "seg_ok": True,
        }]
        gold_entities = [{"type": "PERSON", "start": 100, "end": 120, "text": "Петров Пётр Петрович"}]

        hits = ML.suppressed_gold_entities(suppressions_global, gold_entities)

        assert len(hits) == 1, "Красное состояние не обнаружено — зеркало слепо к эталону"
        assert hits[0]["gold_type"] == "PERSON"
        assert hits[0]["suppressor_type"] == "ROLE_TERM"

    def test_control_non_overlapping_suppression_is_not_flagged(self):
        """Контроль к red-state выше: подавление, НЕ пересекающееся ни с одним
        эталоном, не должно порождать ложную тревогу."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "corpus"))
        import measure_lib as ML

        suppressions_global = [{
            "suppressed_type": "PERSON", "suppressor_type": "ROLE_TERM",
            "segment_id": "p0", "start": 5, "end": 12,
            "gstart": 500, "gend": 507, "seg_ok": True,
        }]
        gold_entities = [{"type": "PERSON", "start": 100, "end": 120, "text": "Петров Пётр Петрович"}]

        hits = ML.suppressed_gold_entities(suppressions_global, gold_entities)
        assert hits == []
