# -*- coding: utf-8 -*-
"""Волна 2, этап B — составные сущности «ORG + ФИО» (синтаксический проход appos).

На каждое позитивное правило — минимум один негативный тест (риск обратного бага:
ложное склеивание разных сторон в один блоб). Тесты на ОТСУТСТВИЕ УТЕЧКИ не входят
сюда — здесь проверяется СМЫСЛОВАЯ ЦЕЛОСТНОСТЬ, а не утечка ПДн.
"""
import os

from models import SourceDocument, TextSegment
from regex_detector import detect_regex
from ner_detector import detect_ner
from syntax_compound import merge_compound_entities
from tokenizer import tokenize

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _detect_and_merge(text):
    seg = TextSegment(id="s", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<t>")
    ents = detect_regex(doc, _CONFIG) + detect_ner(doc, _CONFIG)
    return doc, merge_compound_entities(doc, ents)


def _orgs(ents):
    return sorted(e.original_text for e in ents if e.entity_type == "ORG")


def _persons(ents):
    return sorted(e.original_text for e in ents if e.entity_type == "PERSON")


# ---------------- ПОЗИТИВНЫЕ: ИП + ФИО -> один ORG ----------------

def test_ip_plus_fio_with_verb_merges_into_one_org():
    """'ИП Пирогова А.С. заключила договор' -> один ORG, отдельного PERSON нет."""
    _, ents = _detect_and_merge("ИП Пирогова А.С. заключила договор")
    assert "ИП Пирогова А.С." in _orgs(ents)
    assert "Пирогова А.С." not in _persons(ents)


def test_ip_plus_fio_bare_cell_merges():
    """Голая строка ячейки 'ИП Пирогова А.С.' (без сказуемого) -> один ORG."""
    _, ents = _detect_and_merge("ИП Пирогова А.С.")
    assert _orgs(ents) == ["ИП Пирогова А.С."]
    assert _persons(ents) == []


def test_ip_plus_fio_genitive_case_merges():
    """Падежная форма 'с ИП Пироговой А.С.' -> один ORG (склейка не зависит от падежа)."""
    _, ents = _detect_and_merge("с ИП Пироговой А.С.")
    assert "ИП Пироговой А.С." in _orgs(ents)
    assert "Пироговой А.С." not in _persons(ents)


def test_spelled_out_ip_form_merges_into_org():
    """Форма ПРОПИСЬЮ 'Индивидуальный предприниматель Сидоров И.И.' -> один ORG.

    Дыра из RECON: NER не даёт ORG-токена (голова аппозиции — существительное),
    поэтому regex создаёт ORG-кандидата, а синтаксис доделывает склейку с ФИО."""
    _, ents = _detect_and_merge("Индивидуальный предприниматель Сидоров И.И.")
    assert "Индивидуальный предприниматель Сидоров И.И." in _orgs(ents)
    assert _persons(ents) == []


# ---------------- НЕГАТИВНЫЕ: ложного склеивания быть не должно ----------------

def test_two_parties_not_glued_into_one_blob():
    """'Стороны: ИП Иванов И.И. и ООО «Ромашка»' -> ДВЕ сущности, не один блоб.

    Сочинение (conj/cc), а не приложение (appos) — стороны РАЗНЫЕ. Ни одна ORG не
    содержит одновременно 'Иванов' и 'Ромашка'."""
    _, ents = _detect_and_merge("Стороны: ИП Иванов И.И. и ООО «Ромашка»")
    orgs = _orgs(ents)
    assert len(orgs) == 2, orgs
    assert not any("Иванов" in o and "Ромашка" in o for o in orgs), orgs


def test_role_plus_fio_not_merged_into_org():
    """'Глава КФХ Петров': appos у Петрова — к слову 'Глава', не к ORG 'КФХ'.

    Роль+ФИО — это НЕ ИП+ФИО: человек здесь представитель, а не организация;
    КФХ и Петров остаются раздельными (осознанная типизация, см. отчёт)."""
    _, ents = _detect_and_merge("Глава КФХ Петров")
    assert "КФХ" in _orgs(ents)
    assert "Петров" in _persons(ents)
    assert not any("Петров" in o for o in _orgs(ents))


def test_standalone_org_without_person_unchanged():
    """ORG без соседнего ФИО не трогается (гейт не срабатывает)."""
    _, ents = _detect_and_merge("ООО «Ромашка» поставляет товар")
    assert _orgs(ents) == ["ООО «Ромашка»"]


def test_standalone_person_without_org_unchanged():
    """PERSON без соседнего ORG не трогается."""
    _, ents = _detect_and_merge("Договор подписал Иванов И.И.")
    assert "Иванов И.И." in _persons(ents)
    assert _orgs(ents) == []


# ---------------- Инварианты и round-trip ----------------

def test_merged_entity_original_text_matches_slice():
    """Инвариант: у составной сущности original_text == seg.text[start:end]."""
    doc, ents = _detect_and_merge("ИП Пирогова А.С. заключила договор")
    seg = doc.segments[0]
    for e in ents:
        assert e.original_text == seg.text[e.start:e.end]


def test_compound_becomes_single_token_and_roundtrips():
    """Сквозной: составная сущность получает ОДИН токен; текст восстановим посимвольно."""
    seg = TextSegment(id="s", text="ИП Пирогова А.С. заключила договор с покупателем.",
                      source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<t>")
    ents = detect_regex(doc, _CONFIG) + detect_ner(doc, _CONFIG)
    ents = merge_compound_entities(doc, ents)
    anon, final = tokenize(doc, ents, _CONFIG)

    org_tokens = [e.token for e in final if e.entity_type == "ORG"]
    assert len(org_tokens) == 1, [(e.entity_type, e.original_text, e.token) for e in final]
    token = org_tokens[0]
    assert token in anon
    # в анонимизированном тексте нет обрывка ФИО в открытом виде
    assert "Пирогова" not in anon

    # восстановление по карте токен->original_text посимвольно точно
    restore = {e.token: e.original_text for e in final}
    rebuilt = anon
    for tok, val in restore.items():
        rebuilt = rebuilt.replace(tok, val)
    assert rebuilt == seg.text
