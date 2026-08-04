# -*- coding: utf-8 -*-
"""ЭТАП T-ARB — контракт арбитража пересечений типов.

Карта дефекта и контракт — docs/archive/reports/T_ARB_REPORT.md. Три группы тестов:

  1. TestContractCompleteness — assert_priority_contract: каждый тип конфига с
     token_prefix обязан быть в списке приоритетов своего механизма; забытый
     тип роняет сборку с ЕГО ИМЕНЕМ в сообщении, а не молча становится слабее
     всех перечисленных. Красное состояние приёмки — здесь же (монки-патч
     списка эмулирует «убрали тип», без правки файла).
  2. TestIntersectionClasses / TestAnchorStrengthOverridesLength — по классу
     пересечений (regex-regex/ner-ner/regex-vs-ner/новое правило T-ARB) —
     ожидаемый победитель, напрямую через tokenizer._winner.
  3. TestBAddrHomonymIntegration — сквозной пайплайн на минимальном репро
     находки B-ADDR-HOMONYM (воспроизведён на labor_0006__m2718_linebreak,
     живой на HEAD этапа T-ARB ДО правки).
"""
import os

import pytest
import yaml

import tokenizer as TOK
from models import Entity, SourceDocument, TextSegment
from pipeline import run_detection
from tokenizer import (
    ArbitrationContractError,
    assert_priority_contract,
    resolve_for_masking,
    tokenize,
)

_CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _entity(entity_type, text, start=0, detector="ner", confidence=1.0):
    return Entity(
        id="x", segment_id="s0", start=start, end=start + len(text),
        original_text=text, entity_type=entity_type, detector=detector,
        confidence=confidence,
    )


def _write_cfg(tmp_path, entity_types: dict) -> str:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        yaml.safe_dump({"entity_types": entity_types}, allow_unicode=True),
        encoding="utf-8",
    )
    return str(p)


# =========================================================================== #
#  1. Полнота контракта + красное состояние
# =========================================================================== #

class TestContractCompleteness:

    def test_real_config_satisfies_contract(self):
        """На боевом entity_types.yaml контракт держится сегодня без исключений —
        все 15 типов корпуса (оба механизма) на месте (Шаг 0в: «молчаливых
        проигравших» сейчас нет ни одного, есть только МЕХАНИЗМ, который не
        поймает следующий забытый тип без этого стража)."""
        assert_priority_contract(_CFG)  # не бросает

    def test_missing_regex_type_raises_and_names_it(self, tmp_path):
        cfg = _write_cfg(tmp_path, {
            "GHOST": {"method": "regex", "pattern": r"\d+", "token_prefix": "GHOST"},
        })
        with pytest.raises(ArbitrationContractError) as exc:
            assert_priority_contract(cfg)
        assert "GHOST" in str(exc.value)

    def test_missing_anchor_registry_type_raises_and_names_it(self, tmp_path):
        cfg = _write_cfg(tmp_path, {
            "GHOST_PLACE": {
                "method": "anchor_registry", "anchor_type": "GHOST_PLACE",
                "token_prefix": "GHOST_PLACE",
            },
        })
        with pytest.raises(ArbitrationContractError) as exc:
            assert_priority_contract(cfg)
        assert "GHOST_PLACE" in str(exc.value)

    def test_missing_ner_type_raises_and_names_it(self, tmp_path):
        cfg = _write_cfg(tmp_path, {
            "GHOST_NER": {
                "method": "ner", "ner_label": "GHOST", "token_prefix": "GHOST_NER",
            },
        })
        with pytest.raises(ArbitrationContractError) as exc:
            assert_priority_contract(cfg)
        assert "GHOST_NER" in str(exc.value)

    def test_type_without_token_prefix_is_not_checked(self, tmp_path):
        """Тип без token_prefix не маскируется — арбитраж его не касается
        (DATE с enabled: false — тот же случай на боевом конфиге)."""
        cfg = _write_cfg(tmp_path, {"NOT_MASKED": {"method": "regex", "pattern": r"x"}})
        assert_priority_contract(cfg)  # не бросает

    def test_disabled_type_without_token_prefix_is_ignored(self, tmp_path):
        cfg = _write_cfg(tmp_path, {
            "MAYBE": {"method": "regex", "pattern": r"x", "enabled": False},
        })
        assert_priority_contract(cfg)  # не бросает — нечем маскировать

    def test_red_state_removing_real_type_from_ner_list_breaks_the_build(self, monkeypatch):
        """КРАСНОЕ СОСТОЯНИЕ приёмки (п.3 задания): убрать тип из списка —
        сборка падает и называет тип."""
        monkeypatch.setattr(
            TOK, "_NER_PRIORITY", [t for t in TOK._NER_PRIORITY if t != "PERSON"]
        )
        with pytest.raises(ArbitrationContractError) as exc:
            assert_priority_contract(_CFG)
        assert "PERSON" in str(exc.value)

    def test_red_state_removing_real_type_from_regex_list_breaks_the_build(self, monkeypatch):
        monkeypatch.setattr(
            TOK, "_REGEX_PRIORITY", [t for t in TOK._REGEX_PRIORITY if t != "EMAIL"]
        )
        with pytest.raises(ArbitrationContractError) as exc:
            assert_priority_contract(_CFG)
        assert "EMAIL" in str(exc.value)

    def test_resolve_for_masking_enforces_the_contract_live(self, monkeypatch):
        """Страж реально висит на боевом пути (resolve_for_masking), а не
        только в собственном тесте — красное состояние доходит до пайплайна
        ДО первого пересечения."""
        monkeypatch.setattr(
            TOK, "_REGEX_PRIORITY", [t for t in TOK._REGEX_PRIORITY if t != "EMAIL"]
        )
        seg = TextSegment(id="s0", text="a@b.ru", source_type="txt_line", metadata={})
        doc = SourceDocument(segments=[seg], source_format="txt", source_path="<x>")
        with pytest.raises(ArbitrationContractError):
            resolve_for_masking(doc, [], _CFG)


# =========================================================================== #
#  2. Классы пересечений — ожидаемый победитель (tokenizer._winner напрямую)
# =========================================================================== #

class TestIntersectionClasses:

    def test_regex_beats_ner_unconditionally_even_when_shorter(self):
        a = _entity("PHONE", "89261234567", start=0, detector="regex")
        b = _entity("ADDRESS", "г. Москва, ул. Ленина, д. 1, кв. 2222222", start=5,
                    detector="ner")
        assert len(b.original_text) > len(a.original_text)
        assert TOK._winner(a, b) is a
        assert TOK._winner(b, a) is a

    def test_regex_vs_regex_longer_span_wins(self):
        a = _entity("PHONE", "89261234567", start=0, detector="regex")   # 11
        b = _entity("EMAIL", "x@y.ru", start=0, detector="regex")        # 6
        assert TOK._winner(a, b) is a
        assert TOK._winner(b, a) is a

    def test_regex_vs_regex_tie_uses_priority_list(self):
        # одинаковая длина (9) -> решает _REGEX_PRIORITY: SNILS раньше KPP
        a = _entity("SNILS", "123456789", start=0, detector="regex")
        b = _entity("KPP", "987654321", start=3, detector="regex")
        assert TOK._winner(a, b) is a
        assert TOK._winner(b, a) is a

    def test_regex_vs_regex_unlisted_type_loses_to_any_listed_type(self):
        """Документирует ПАДЕНИЕ ПО УМОЛЧАНИЮ, от которого страхует
        assert_priority_contract: тип вне списка слабее ВСЕХ перечисленных,
        даже более короткого спана. На боевом конфиге такого типа сегодня
        нет (см. TestContractCompleteness) — это тест механизма-фолбэка."""
        ghost = _entity("GHOST", "12", start=0, detector="regex")            # короче
        kpp = _entity("KPP", "987654321", start=5, detector="regex")          # длиннее
        assert TOK._winner(ghost, kpp) is kpp
        # даже при РАВНОЙ длине с перечисленным типом — вне списка не выигрывает
        ghost2 = _entity("GHOST", "987654321", start=0, detector="regex")
        assert TOK._winner(ghost2, kpp) is kpp

    def test_priority_index_is_compared_as_a_number_not_as_a_string(self):
        """ЭТАП T2, найденный здесь дефект. `_regex_rank` возвращал `str(index)`,
        и второй элемент кортежа сравнивался ЛЕКСИКОГРАФИЧЕСКИ: '10' < '2'.
        Пока в списке было девять с небольшим имён, порядок случайно совпадал с
        задуманным; с ростом списка хвост переворачивался — тип с индексом 10+
        становился СИЛЬНЕЕ типа с индексом 2..9, то есть ровно наоборот
        комментарию у _REGEX_PRIORITY. Тест держит СВОЙСТВО (индекс — число), а
        не конкретную пару типов: список ещё будет расти."""
        assert len(TOK._REGEX_PRIORITY) > 10, "тест имеет смысл только на длинном списке"
        early = TOK._REGEX_PRIORITY[2]     # индекс 2 -> '2'
        late = TOK._REGEX_PRIORITY[10]     # индекс 10 -> '10' < '2' при строковом сравнении
        assert TOK._regex_rank(early) < TOK._regex_rank(late)
        a = _entity(early, "XXXXXXXX", start=0, detector="regex")
        b = _entity(late, "YYYYYYYY", start=3, detector="regex")   # та же длина
        assert TOK._winner(a, b) is a
        assert TOK._winner(b, a) is a

    def test_ner_vs_ner_longer_span_wins(self):
        a = _entity("ORG", "ООО Ромашка Космос", start=0, detector="ner")     # 18
        b = _entity("PERSON", "Иванов Иван", start=5, detector="ner")         # 11
        assert TOK._winner(a, b) is a

    def test_ner_vs_ner_tie_uses_priority_list_org_over_person(self):
        a = _entity("ORG", "XXXXXXXX", start=0, detector="ner")     # 8
        b = _entity("PERSON", "YYYYYYYY", start=3, detector="ner")  # 8, разный start
        assert TOK._winner(a, b) is a
        assert TOK._winner(b, a) is a


# =========================================================================== #
#  ЭТАП T-ARB — единственное место, где сила якоря решает раньше длины
# =========================================================================== #

class TestAnchorStrengthOverridesLength:
    """B-ADDR-HOMONYM: ADDRESS (существование = подтверждённый _addr_span_strong
    якорь) побеждает ОДНОСЛОВНЫЙ PERSON (без своей формы ФИО) даже когда
    PERSON длиннее. Многословный/с точкой инициала PERSON (само-якорь ФИО) под
    исключение не попадает — решает как раньше (защита реальных ФИО)."""

    def test_address_beats_longer_bare_person(self):
        addr = _entity("ADDRESS", "ул. Гагарина", start=0, detector="ner")           # 12
        per = _entity("PERSON", "Гагаринаааааааааааааа", start=4, detector="ner")    # 22, длиннее
        assert len(per.original_text) > len(addr.original_text)
        assert TOK._winner(addr, per) is addr
        assert TOK._winner(per, addr) is addr

    def test_address_beats_shorter_bare_person_too(self):
        """Не только защита от переворота длины — правило безусловно для
        однословного PERSON, а не «пока не длиннее»."""
        addr = _entity("ADDRESS", "ул. Мира, д. 5", start=0, detector="ner")
        per = _entity("PERSON", "Мира", start=4, detector="ner")
        assert TOK._winner(addr, per) is addr

    def test_multiword_fullname_person_keeps_ordinary_length_rule(self):
        addr = _entity("ADDRESS", "ул. Мира", start=0, detector="ner")               # 8
        per = _entity("PERSON", "Иванов Иван Иванович", start=0, detector="ner")     # 21, длиннее
        assert TOK._winner(addr, per) is per
        assert TOK._winner(per, addr) is per

    def test_dotted_initials_person_keeps_ordinary_length_rule(self):
        addr = _entity("ADDRESS", "ул. Мира", start=0, detector="ner")               # 8
        per = _entity("PERSON", "Иванов И.И.аааааа", start=0, detector="ner")        # длиннее, с точкой
        assert TOK._winner(addr, per) is per

    def test_rule_does_not_apply_to_org_vs_bare_person(self):
        """Класс из находки — ТОЛЬКО ADDRESS↔PERSON. ORG против однословного
        PERSON решается как раньше (длина/приоритет), никакого расширения."""
        org = _entity("ORG", "АО", start=0, detector="ner")                          # 2
        per = _entity("PERSON", "Гагаринаааааа", start=5, detector="ner")            # длиннее
        assert TOK._winner(org, per) is per  # длина решает как раньше


# =========================================================================== #
#  3. Сквозной пайплайн — минимальный репро находки
# =========================================================================== #

class TestBAddrHomonymIntegration:
    """Репро воспроизведено на живом HEAD этапа T-ARB (labor_0006__m2718_linebreak,
    измерение этапа C: measure_stagec.txt, «Полев\\nая» cross=ADDRESS). ДО правки
    единственной сущностью было PERSON 'Полев\\nая' — номер дома «21» оставался
    ОТКРЫТЫМ ТЕКСТОМ (утечка адреса, не только неверный тип)."""

    def test_linebreak_split_street_homonym_is_masked_as_address(self):
        text = "3.1. Место исполнения обязательств: Тверь, Полев\nая 21."
        seg = TextSegment(id="p0", text=text, source_type="p", metadata={})
        doc = SourceDocument(segments=[seg], source_format="docx", source_path="<repro>")
        entities = run_detection(doc, _CFG)
        anon, kept = tokenize(doc, entities, _CFG)

        assert "[PERSON" not in anon, anon
        assert "21" not in anon, anon  # номер дома дальше не течёт открытым текстом
        assert any(
            e.entity_type == "ADDRESS" and "Полев" in e.original_text for e in kept
        ), kept

    def test_no_weak_person_spans_means_zero_extra_work_and_same_result(self):
        """Когда слабых (однословных) PER-спанов в сегменте нет —
        _recover_address_over_weak_per возвращает [] на первой проверке,
        поведение побайтно то же, что до этапа T-ARB."""
        import ner_detector as ND
        assert ND._recover_address_over_weak_per(
            "текст без ФИО вообще", [], [], [], [], [], [],
        ) == []
