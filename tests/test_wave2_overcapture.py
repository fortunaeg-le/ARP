# -*- coding: utf-8 -*-
"""Волна 2, независимая верификация — обратная асимметрия расширения адреса.

КРАСНЫЕ тесты (документируют дефект, НЕ чинят его). Обнаружены проверяющим при
проверке пункта ТЗ «ПРОВЕРЬ АСИММЕТРИЮ В ОБРАТНУЮ СТОРОНУ»: консервативное правое
расширение адреса (`_expand_addr_right` / `_classify_addr_token`) жадно тянет ЛЮБОЙ
токен с заглавной буквы или цифрой до первого слова нижнего регистра, НЕ проверяя,
не принадлежит ли захватываемый хвост уже найденной PER/ORG/regex-сущности.

Golden-набор этого не ловит: там каждый адрес — изолированная строка (после адреса
ничего нет). В реальном сегменте, где за адресом на той же строке идёт ФИО / телефон /
ИНН / ORG, расширение поглощает соседнее поле.

Guard `_filter_suspect_yargy` защищает ТОЛЬКО левое направление (ФИО ПЕРЕД адресом) и
только для yargy-ложняков; правое расширение в следующую сущность не прикрыто.

Два следствия:
  1. СЕМАНТИЧЕСКАЯ ПОРЧА — следующая PER/ORG всасывается в ADDRESS, теряет свой токен
     (тот же класс, ради которого делался этап B и фикс _filter_suspect_yargy).
  2. УТЕЧКА ПДн — если поглощённый хвост оказывается regex-сущностью выше приоритетом
     (PHONE/INN/SUM/EMAIL), разрешение пересечений (regex > ner) выкидывает ВЕСЬ
     ADDRESS-токен, и адрес остаётся в открытом виде в анонимизированном тексте.
"""
import os

import pytest

from models import SourceDocument, TextSegment
from regex_detector import detect_regex
from ner_detector import detect_ner
from anchor_registry import detect_org, suppress_conflicts
from syntax_compound import merge_compound_entities
from tokenizer import tokenize

_CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _anonymize(text: str):
    # Этап A': ПОЛНЫЙ конвейер, включая структурный ORG-движок (detect_org) и арбитраж
    # типов — как cmd_encrypt. detect_org считается ДО detect_ner и передаётся туда
    # барьером расширения адреса (A-4) — иначе «ООО «Ромашка»» (Natasha-ORG выключена)
    # не виден адресному расширению и over-capture'ится.
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<oc>")
    rx = detect_regex(doc, _CFG)
    org = detect_org(doc, regex_entities=rx)
    ner = detect_ner(doc, _CFG, regex_entities=rx, org_entities=org)
    ner = suppress_conflicts(org, ner)
    ents = merge_compound_entities(doc, rx + ner + org)
    anon, final = tokenize(doc, ents, _CFG)
    return anon, final


def _addr_original(final):
    return [e.original_text for e in final if e.entity_type == "ADDRESS"]


# --- Дефект 2: УТЕЧКА (наиболее тяжёлый) — адрес + regex-хвост на одной строке ---

@pytest.mark.parametrize("text,leaked", [
    ("г. Казань, ул. Баумана, д. 44, ИНН 7707083893", "Казань"),
    ("Адрес: г. Казань, ул. Баумана, д. 44 Телефон 8-900-123-45-67", "Баумана"),
])
def test_address_not_leaked_when_followed_by_regex_field(text, leaked):
    """Адрес НЕ должен утекать открытым текстом из-за поглощения следующего regex-поля.

    Жадное правое расширение всасывает «ИНН …»/«Телефон …» в ADDRESS-спан; regex-
    сущность (ИНН/PHONE) перекрывает его и как regex побеждает при разрешении, ADDRESS
    выкидывается целиком — идентифицирующий адрес остаётся в анонимном тексте.
    КРАСНЫЙ: сейчас `leaked` присутствует в анонимизированном выводе.
    """
    anon, _ = _anonymize(text)
    assert leaked not in anon, (
        f"АДРЕС УТЁК открытым текстом: {leaked!r} в анонимном выводе {anon!r}"
    )


# --- Дефект 1: СЕМАНТИЧЕСКАЯ ПОРЧА — следующая PER/ORG поглощена адресом ---

def test_following_person_keeps_its_own_token():
    """ФИО после адреса не должно всасываться в ADDRESS-токен (теряя свой PERSON).

    КРАСНЫЙ: ADDRESS сейчас = 'г. Уфа … д. 8 Иванов Иван Иванович', PERSON отсутствует.
    """
    text = "г. Уфа, ул. Ленина, д. 8 Иванов Иван Иванович подписал договор"
    _, final = _anonymize(text)
    addr = _addr_original(final)
    assert addr, f"адрес не найден в {text!r}"
    assert not any("Иванов" in a for a in addr), (
        f"ФИО поглощено адресом: {addr!r}"
    )


def test_following_org_keeps_its_own_token():
    """ORG после адреса не должна всасываться в ADDRESS-токен.

    КРАСНЫЙ: ADDRESS сейчас = 'г. Пермь … 50 ООО «Ромашка»', ORG отдельного токена нет.
    """
    text = "г. Пермь, ул. Ленина, 50 ООО «Ромашка» является поставщиком"
    _, final = _anonymize(text)
    addr = _addr_original(final)
    assert addr, f"адрес не найден в {text!r}"
    assert not any("Ромашка" in a for a in addr), (
        f"ORG поглощена адресом: {addr!r}"
    )


# --- Дефект 3: составные — ложное СЛИЯНИЕ PERSON+ORG для РАЗНЫХ сторон ---
#
# merge_compound_entities (этап B) склеивает ЛЮБУЮ PER↔ORG-пару, связанную appos, если
# PER идёт ПЕРЕД ORG в пределах _COMPOUND_MAX_GAP — не только заявленную «ИП + ФИО», но и
# полноценную «ООО «...»». Негативный контроль исполнителя проверял лишь разделитель «и»
# (conj). Юкстапозиция без союза (свидетель + организация) синтаксис читает как аппозицию
# и сливает разные стороны в один ORG-токен: ФИО перестаёт быть отдельной сущностью.

def test_witness_person_not_merged_into_following_org():
    """«Свидетель Иванов И.И. ООО «Ромашка»» — свидетель (отдельное лицо) не должен
    сливаться в ORG организации.

    КРАСНЫЙ: сейчас после склейки ORG='Иванов И.И. ООО «Ромашка»', PERSON исчез.
    """
    text = "Свидетель Иванов И.И. ООО «Ромашка» подтвердил"
    _, final = _anonymize(text)
    orgs = [e.original_text for e in final if e.entity_type == "ORG"]
    assert not any("Иванов" in o for o in orgs), (
        f"ФИО отдельной стороны слито в ORG организации: {orgs!r}"
    )
