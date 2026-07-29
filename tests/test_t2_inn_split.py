# -*- coding: utf-8 -*-
"""ЭТАП T2-INN — разделение ИНН на два типа: организации (10 цифр) и человека (12).

Зеркала этапа. Каждое пришпиливает одно утверждение отчёта:

  1. ДЕТЕКЦИЯ. 10 цифр -> `INN`, 12 цифр -> `INN_PERSON`, токены разные, значения
     не перепутаны; чужая длина не попадает ни в тот, ни в другой тип.
  2. КОНТРОЛЬНАЯ СУММА. У длин РАЗНЫЕ алгоритмы, и каждый тип зовёт свой; роль КС
     (сигнал под якорем, фильтр без якоря — инвариант этапа 4) не изменилась ни у
     одного из двух типов.
  3. ЯКОРЬ ЧЕЛОВЕКА. Голая фамилия рядом с ИНН-12 по-прежнему детектируется как
     PERSON. Это единственный механизм, который разделение типов могло сломать
     МОЛЧА: `anchor_registry` фильтрует реквизиты по имени типа, и потеря
     `INN_PERSON` в этом фильтре не покраснела бы нигде, кроме полноты по людям.
  4. НАБОРЫ. «Только персональные данные» маскирует ИНН человека и НЕ маскирует
     ИНН организации — на одном тексте, где есть оба.
  5. ВОССТАНОВЛЕНИЕ. Новый вид токена `[INN_PER_N]` возвращается в текст
     побайтно.
  6. ЭТАЛОН. В `gold.json` нет записи `INN` с 12 цифрами и записи `INN_PER` с 10.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_CORPUS = os.path.join(_ROOT, "tests", "corpus")
for _p in (_SRC, _CORPUS):
    if _p not in sys.path:
        sys.path.insert(1, _p)

from models import SourceDocument, TextSegment  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
from detokenizer import detokenize  # noqa: E402
from regex_detector import inn10_checksum, inn12_checksum  # noqa: E402
import type_policy  # noqa: E402

_CFG = os.path.join(_ROOT, "entity_types.yaml")

#: Валидные по КС значения (см. tests/test_regex_detector.py — те же эталонные
#: номера ФНС, что проверяют сам алгоритм).
INN10_VALID = "7707083893"
INN10_BAD = "7707083894"
INN12_VALID = "500100732259"
INN12_BAD = "500100732258"


def _doc(text):
    segs = [
        TextSegment(id=f"l{i}", text=line, source_type="txt_line", metadata={})
        for i, line in enumerate(text.split("\n")) if line.strip()
    ]
    return SourceDocument(segments=segs, source_format="txt", source_path="<t>")


def _run(text, enabled_types=None):
    doc = _doc(text)
    ents = run_detection(doc, _CFG)
    anon, final = tokenize(doc, ents, _CFG, enabled_types=enabled_types)
    return anon, final


def _by_type(final):
    out = {}
    for e in final:
        out.setdefault(e.entity_type, []).append(e.original_text)
    return out


# --------------------------------------------------------------------------- #
# 1. Детекция: длина решает тип
# --------------------------------------------------------------------------- #
def test_ten_digits_is_company_and_twelve_is_person():
    anon, final = _run(
        f"Поставщик: ООО «Восход», ИНН {INN10_VALID}.\n"
        f"Покупатель: ИП Морозов Пётр Ильич, ИНН {INN12_VALID}.\n"
    )
    by = _by_type(final)
    assert by.get("INN") == [INN10_VALID]
    assert by.get("INN_PERSON") == [INN12_VALID]
    assert "[INN_1]" in anon and "[INN_PER_1]" in anon
    assert INN10_VALID not in anon and INN12_VALID not in anon


def test_neither_type_eats_a_number_of_another_length():
    """11 цифр — ни ИНН организации, ни ИНН человека. Прежний ОДИН паттерн вёл
    себя так же (10 или 12, третьего не дано); разделение это не изменило."""
    _, final = _run("ИНН 12345678901 в документе.\n")
    by = _by_type(final)
    assert "INN" not in by and "INN_PERSON" not in by


def test_bank_account_is_not_swallowed_by_the_person_pattern():
    _, final = _run("Расчётный счёт 40702810400001234567 в банке.\n")
    by = _by_type(final)
    assert by.get("BANK_ACCOUNT") == ["40702810400001234567"]
    assert "INN_PERSON" not in by and "INN" not in by


# --------------------------------------------------------------------------- #
# 2. Контрольная сумма: у каждой длины своя
# --------------------------------------------------------------------------- #
def test_each_length_uses_its_own_checksum_algorithm():
    """Значение, валидное по «своему» алгоритму, невалидно по чужому — то есть
    алгоритмы действительно РАЗНЫЕ, а не один на обе длины."""
    assert inn10_checksum(INN10_VALID) and not inn12_checksum(INN10_VALID)
    assert inn12_checksum(INN12_VALID) and not inn10_checksum(INN12_VALID)


@pytest.mark.parametrize("value,etype", [(INN10_BAD, "INN"), (INN12_BAD, "INN_PERSON")])
def test_broken_checksum_under_the_anchor_is_still_masked(value, etype):
    """Инвариант этапа 4 сохранён ДЛЯ ОБОИХ типов: опечатка в реквизите не делает
    его не-ПДн, под якорем «ИНН» значение маскируется и при не сошедшейся КС."""
    _, final = _run(f"ИНН {value} (в документе опечатка).\n")
    assert _by_type(final).get(etype) == [value]


@pytest.mark.parametrize("value", [INN10_BAD, INN12_BAD])
def test_broken_checksum_without_the_anchor_is_not_taken(value):
    """И обратная сторона того же инварианта: без якоря КС по-прежнему фильтр."""
    _, final = _run(f"Порядковый номер {value} в реестре.\n")
    by = _by_type(final)
    assert "INN" not in by and "INN_PERSON" not in by


# --------------------------------------------------------------------------- #
# 3. Якорь человека: ИНН-12 рядом с фамилией
# --------------------------------------------------------------------------- #
def test_inn_of_a_person_still_anchors_a_bare_surname():
    """ГОЛАЯ фамилия («Морозов» без имени и без должности) анкорится только
    внешним маркером, и ИНН физлица — один из них (`PerAnchorDetector`). После
    разделения типов реквизит приезжает под именем `INN_PERSON`; если его забыть
    в фильтре `_requisites_src_by_seg`, этот тест краснеет, а больше нигде
    потеря якоря не видна."""
    _, final = _run(f"Морозов, ИНН {INN12_VALID}, оплатил услуги.\n")
    assert "Морозов" in " ".join(_by_type(final).get("PERSON", []))


def test_inn_of_a_company_does_not_anchor_a_bare_surname():
    """Обратная сторона: 10-значный ИНН — реквизит ЮРЛИЦА и якорем человека не
    был и не стал (иначе тест выше проходил бы по случайной причине)."""
    _, final = _run(f"Морозов, ИНН {INN10_VALID}, оплатил услуги.\n")
    assert "Морозов" not in " ".join(_by_type(final).get("PERSON", []))


# --------------------------------------------------------------------------- #
# 4. Наборы: узкий набор различает два ИНН
# --------------------------------------------------------------------------- #
_BOTH = (
    f"Исполнитель: ООО «Восход», ИНН {INN10_VALID}.\n"
    f"Заказчик: ИП Морозов Пётр Ильич, ИНН {INN12_VALID}.\n"
)


def test_personal_profile_masks_the_person_inn_and_keeps_the_company_one():
    """Ради этого этап и делался: до него набор «только персональные данные»
    оставлял в тексте ОБА ИНН, потому что тип был один."""
    known = type_policy.known_types(_CFG)
    enabled = type_policy.resolve(type_policy.PROFILE_PERSONAL, {}, known=known)
    anon, final = _run(_BOTH, enabled_types=enabled)
    assert INN12_VALID not in anon, "ИНН человека обязан быть замаскирован"
    assert INN10_VALID in anon, "ИНН организации в этом наборе маскировать нельзя"
    tokens = {e.entity_type for e in final if e.token}
    assert "INN_PERSON" in tokens and "INN" not in tokens


def test_requisites_profile_masks_both():
    known = type_policy.known_types(_CFG)
    enabled = type_policy.resolve(type_policy.PROFILE_PERSONAL_REQUISITES, {}, known=known)
    anon, _ = _run(_BOTH, enabled_types=enabled)
    assert INN10_VALID not in anon and INN12_VALID not in anon


# --------------------------------------------------------------------------- #
# 5. Восстановление
# --------------------------------------------------------------------------- #
def test_round_trip_through_the_new_token(tmp_path):
    """Полный путь encrypt -> сессия -> decrypt. Хранилище — только tmp_path."""
    from session_store import save_session
    from tokenizer import build_plain_text

    doc = _doc(_BOTH)
    ents = run_detection(doc, _CFG)
    anon, final = tokenize(doc, ents, _CFG)
    assert "[INN_PER_1]" in anon, "новый вид токена обязан появиться в тексте"

    sid = save_session(final, storage_dir=str(tmp_path))
    restored, unresolved = detokenize(anon, sid, storage_dir=str(tmp_path))

    assert restored == build_plain_text(doc)
    assert unresolved == [], "токен [INN_PER_N] не разобран восстановлением"


# --------------------------------------------------------------------------- #
# 6. Эталон
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def gold():
    with open(os.path.join(_CORPUS, "gold.json"), encoding="utf-8") as f:
        return json.load(f)


def _digits(text):
    from corpus_lib import CYR2DIGIT, INVISIBLES
    seps = set("   \n\r\t/") | set("-‐‑‒–—") | set(INVISIBLES)
    return "".join(CYR2DIGIT.get(ch, ch) for ch in text if ch not in seps)


def test_gold_has_no_length_mixed_up_between_the_two_inn_types(gold):
    """Переразметка (шаг 2 этапа) не оставила ни одной записи не на своём месте.
    Длина считается ТАК ЖЕ, как её считал скрипт переноса: разделители сняты,
    кириллические омоглифы сведены к цифрам — иначе мутированные записи корпуса
    выглядели бы короче, чем есть."""
    wrong = []
    for d in gold:
        for e in d["entities"]:
            if e["type"] not in ("INN", "INN_PER"):
                continue
            n = len(_digits(e["text"]))
            want = 10 if e["type"] == "INN" else 12
            if n != want:
                wrong.append((d["doc_id"], e["type"], e["text"], n))
    assert not wrong, "записи эталона не на своём типе: %r" % wrong[:10]


def test_gold_keeps_both_inn_types_present(gold):
    """Страховка от «переразметили всё подряд»: обе половины на месте."""
    counts = {"INN": 0, "INN_PER": 0}
    for d in gold:
        for e in d["entities"]:
            if e["type"] in counts:
                counts[e["type"]] += 1
    assert counts == {"INN": 369, "INN_PER": 675}
