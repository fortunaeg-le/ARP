# -*- coding: utf-8 -*-
"""ЭТАП T2 — контракты детекторов ПРОЦЕНТА (PERCENT) и СРОКА (TERM).

Отчёт этапа — docs/archive/reports/T2_REPORT.md. Здесь закреплено то, что цифрами корпуса не
видно и что легко потерять правкой паттерна:

  1. ФОРМЫ значения, ради которых тип заведён (по документам, не по реестру
     форм генератора — «СТЕНА» задания этапа);
  2. ГРАНИЦА ХВОСТА. И у процента, и у срока значение продолжается основанием
     («от цены Договора», «с момента подписания акта»). Хвост обязан упираться
     в знак препинания и НЕ содержать цифр — иначе он тянет прозу и, хуже,
     побеждает пересечение с реквизитом ПО ДЛИНЕ, гася его маску;
  3. ЧЕГО ЭТИ ТИПЫ НЕ БЕРУТ: номер договора, дату документа, номер пункта —
     решения по ним владелец не принимал (в корпусе v2 они типизированные
     негативы);
  4. ВОССТАНОВЛЕНИЕ новых токенов — круговой тест;
  5. АРБИТРАЖ: оба типа в _REGEX_PRIORITY; красное состояние — рядом.
"""
import os

import pytest

import tokenizer as TOK
import type_policy
from models import SourceDocument, TextSegment
from pipeline import run_detection
from tokenizer import ArbitrationContractError, assert_priority_contract, tokenize

_CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")


def _doc(text, source_format="txt"):
    """Один сегмент — как строка txt-документа."""
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    return SourceDocument(segments=[seg], source_format=source_format,
                          source_path="<repro>")


def spans(text, entity_type):
    """Тексты спанов заданного типа, найденных ПРОДУКТОВЫМ путём детекции."""
    doc = _doc(text)
    return [e.original_text for e in run_detection(doc, _CFG)
            if e.entity_type == entity_type]


def masked(text):
    """(анонимный текст, [(тип, значение)]) — то, что реально ляжет маской."""
    doc = _doc(text)
    ents = run_detection(doc, _CFG)
    anon, kept = tokenize(doc, ents, _CFG, enabled_types=type_policy.MAXIMUM)
    return anon, [(e.entity_type, e.original_text) for e in kept]


# =========================================================================== #
#  1. Формы значения
# =========================================================================== #

class TestPercentForms:

    @pytest.mark.parametrize("text, expect", [
        # знак процента, с базой и без
        ("в т.ч. НДС 18%.", "18%"),
        ("Агентское вознаграждение: 0,1 % от общей суммы счета, но не более.",
         "0,1 % от общей суммы счета"),
        # слово «процент» вместо знака
        ("Размер неустойки: 20 (двадцать) процентов за каждый день просрочки.",
         "20 (двадцать) процентов за каждый день просрочки"),
        ("Предельный размер: 0,5 процента от цены Договора.",
         "0,5 процента от цены Договора"),
        # пропись в скобках — часть значения, как её пишут в договоре
        ("Ставка 0,2 (две десятых процента) % от цены Договора.",
         "0,2 (две десятых процента) % от цены Договора"),
        # доля ключевой ставки: процентом число делает именно эта формула
        ("Налог: 1/300 ключевой ставки ЦБ РФ годовых.",
         "1/300 ключевой ставки ЦБ РФ годовых"),
        ("Пеня 1/150 ключевой ставки ЦБ РФ от стоимости неоплаченных работ.",
         "1/150 ключевой ставки ЦБ РФ от стоимости неоплаченных работ"),
    ])
    def test_form_is_detected_whole(self, text, expect):
        assert spans(text, "PERCENT") == [expect]

    def test_percent_after_vat_does_not_swallow_the_amount_after_it(self):
        """«включая НДС 20% в размере 2 130 002 руб.» — процент кончается на
        знаке, сумма остаётся СВОЕЙ сущностью. Хвост основания начинается только
        со слов «годовых»/«за каждый…»/«от …», и «в размере» под них не подходит."""
        text = "Цена: 461 000,3 ₽, включая НДС 20% в размере 2 130 002 руб."
        assert spans(text, "PERCENT") == ["20%"]
        assert "2 130 002 руб." in spans(text, "SUM")


class TestTermForms:

    @pytest.mark.parametrize("text, expect", [
        ("Срок — в течение 10 рабочих дней.", "в течение 10 рабочих дней"),
        ("Оплата в течение 3 (Трёх) месяцев с момента подписания акта.",
         "в течение 3 (Трёх) месяцев с момента подписания акта"),
        # количество прописью, в том числе из двух слов
        ("Уведомление в течение пяти банковских дней с момента выставления счета.",
         "в течение пяти банковских дней с момента выставления счета"),
        ("Возврат в течение сорока пяти календарных дней со дня предъявления требования.",
         "в течение сорока пяти календарных дней со дня предъявления требования"),
        # вторая форма: предельная дата
        ("Срок уведомления — не позднее 08.12.2025.", "не позднее 08.12.2025"),
    ])
    def test_form_is_detected_whole(self, text, expect):
        assert spans(text, "TERM") == [expect]

    def test_preposition_of_the_basis_does_not_cut_the_span_short(self):
        """Регресс, живший до правки: «в течение трех месяцев СО ДНЯ …» —
        предлог «со» вставал на место уточнения («трех месяцев со» + «дня» как
        единица), и спан обрывался на «со дня». Слова короче трёх букв
        количеством/уточнением не считаются."""
        text = "Возврат в течение трех месяцев со дня предъявления требования."
        assert spans(text, "TERM") == [
            "в течение трех месяцев со дня предъявления требования"]


# =========================================================================== #
#  2. Граница хвоста — главный контракт этапа
# =========================================================================== #

class TestTailBoundary:

    def test_percent_tail_that_does_not_end_at_punctuation_is_dropped_whole(self):
        """Проза старого корпуса: «0,1 % от суммы задолженности за каждый день
        просрочки». Хвост «от …» ограничен тремя словами И обязан упереться в
        знак препинания; здесь не упирается ни на трёх, ни на двух, ни на одном
        слове — значит ветвь хвоста отваливается ЦЕЛИКОМ, и маска = «0,1 %».
        Обрубок «от суммы задолженности за» с висящим предлогом не годится: это
        уже кусок прозы, а не значение."""
        text = ("За нарушение срока оплаты начисляется неустойка в размере "
                "0,1 % от суммы задолженности за каждый день просрочки.")
        assert spans(text, "PERCENT") == ["0,1 %"]

    def test_percent_tail_is_taken_when_it_does_end_at_punctuation(self):
        text = "но не более 10 % от суммы обязательства."
        assert spans(text, "PERCENT") == ["10 % от суммы обязательства"]

    def test_term_tail_stops_at_punctuation(self):
        text = ("Срок ответа — в течение 7 рабочих дней с момента поступления "
                "денежных средств на расчетный счет, далее по соглашению.")
        assert spans(text, "TERM") == [
            "в течение 7 рабочих дней с момента поступления денежных средств "
            "на расчетный счет"]

    def test_term_tail_never_swallows_a_requisite(self):
        """ЗАЧЕМ ЗАПРЕТ ЦИФР В ХВОСТЕ. `tokenizer._winner` правило 2 отдаёт
        победу БОЛЕЕ ДЛИННОМУ спану, и только потом смотрит на тип. Хвост срока
        длиннее любого реквизита, поэтому дотянувшись до счёта он бы ПОГАСИЛ его
        маску — то есть создал утечку. Цифра в именной группе основания и есть
        признак «вышли за конструкцию»."""
        text = ("Оплата в течение 5 рабочих дней с момента поступления средств "
                "на счет 40702810751916376892 в ПАО Сбербанк.")
        anon, kept = masked(text)
        types = {t for t, _ in kept}
        assert "BANK_ACCOUNT" in types, kept
        assert "40702810751916376892" not in anon, anon
        # хвост основания («с момента поступления средств на счет …») отброшен
        # ЦЕЛИКОМ, потому что упирается в цифры реквизита
        assert [v for t, v in kept if t == "TERM"] == ["в течение 5 рабочих дней"], kept


# =========================================================================== #
#  3. Чего эти типы не берут (в корпусе v2 — типизированные негативы)
# =========================================================================== #

class TestNotTaken:

    def test_document_number_is_not_a_percent_or_a_term(self):
        text = "АГЕНТСКИЙ ДОГОВОР № 59Ф-62780/14-129Д"
        assert spans(text, "PERCENT") == []
        assert spans(text, "TERM") == []

    def test_document_date_alone_is_not_a_term(self):
        """Дата документа становится сроком ТОЛЬКО под якорем «не позднее».
        Голая дата в шапке — не срок (решение по датам документа не принято)."""
        assert spans("г. Пермь     19.03.2023", "TERM") == []
        assert spans("Срок — не позднее 19.03.2023", "TERM") == ["не позднее 19.03.2023"]

    def test_clause_number_is_not_a_percent(self):
        assert spans("1.3. Агентское вознаграждение", "PERCENT") == []

    def test_day_of_month_deadline_without_a_date_is_not_a_term(self):
        """«не позднее 10-го числа текущего месяца» — предельной ДАТЫ здесь нет,
        и подставлять маску не на что."""
        assert spans("Плата вносится не позднее 10-го числа текущего месяца.",
                     "TERM") == []

    def test_a_period_without_a_time_unit_is_not_a_term(self):
        assert spans("Действует в течение срока действия договора.", "TERM") == []


# =========================================================================== #
#  4. Восстановление новых токенов — круговой тест
# =========================================================================== #

class TestRoundTrip:

    def test_new_tokens_restore_byte_for_byte(self, tmp_path):
        from detokenizer import detokenize
        from session_store import save_session
        from tokenizer import build_plain_text

        text = ("2.2. Налог: 1/130 ключевой ставки ЦБ РФ годовых. "
                "2.3. Срок оплаты — в течение 3 (Трёх) месяцев с момента "
                "подписания акта. 4.2. Неустойка: 20 (двадцать) процентов "
                "за каждый день просрочки, но не более 18%.")
        doc = _doc(text)
        ents = run_detection(doc, _CFG)
        anon, kept = tokenize(doc, ents, _CFG, enabled_types=type_policy.MAXIMUM)

        assert "[PERCENT" in anon and "[TERM" in anon, anon
        sid = save_session(kept, session_id=None, ttl_hours=24,
                           storage_dir=str(tmp_path))
        restored, unresolved = detokenize(anon, sid, storage_dir=str(tmp_path),
                                          mode="exact")
        assert unresolved == []
        assert restored == build_plain_text(doc)


# =========================================================================== #
#  5. Арбитраж и наборы
# =========================================================================== #

class TestArbitrationAndProfiles:

    def test_both_types_are_in_the_priority_contract(self):
        assert_priority_contract(_CFG)  # не бросает
        assert "PERCENT" in TOK._REGEX_PRIORITY
        assert "TERM" in TOK._REGEX_PRIORITY

    @pytest.mark.parametrize("t", ["PERCENT", "TERM"])
    def test_red_state_removing_the_new_type_breaks_the_build(self, monkeypatch, t):
        monkeypatch.setattr(TOK, "_REGEX_PRIORITY",
                            [x for x in TOK._REGEX_PRIORITY if x != t])
        with pytest.raises(ArbitrationContractError) as exc:
            assert_priority_contract(_CFG)
        assert t in str(exc.value)

    @pytest.mark.parametrize("t", ["PERCENT", "TERM"])
    def test_new_types_are_weaker_than_any_requisite_on_equal_length(self, t):
        """Место в конце списка — не «дописали в хвост», а решение: у реквизита
        есть КС/жёсткая форма/якорь, у процента и срока — только конструкция
        прозы вокруг числа."""
        assert TOK._regex_rank(t) > TOK._regex_rank("INN")
        assert TOK._regex_rank(t) > TOK._regex_rank("SUM")

    @pytest.mark.parametrize("t", ["PERCENT", "TERM"])
    def test_new_types_live_in_the_money_profile_and_in_maximum(self, t):
        assert t in type_policy.WITH_MONEY
        assert t not in type_policy.PERSONAL_REQUISITES
        assert t in type_policy.known_types(_CFG)
        # «Максимум» — сентинел None, фильтр не применяется вовсе
        assert type_policy.resolve(type_policy.PROFILE_MAXIMUM) is None

    @pytest.mark.parametrize("t", ["PERCENT", "TERM"])
    def test_turning_the_type_off_removes_its_mask_but_not_its_detection(self, t):
        """Контракт T1: выключенный тип НЕ отключается в детекции — он остаётся
        барьером соседям, просто не получает токен."""
        text = ("Неустойка 20 (двадцать) процентов за каждый день просрочки, "
                "оплата в течение 10 рабочих дней.")
        doc = _doc(text)
        ents = run_detection(doc, _CFG)
        assert any(e.entity_type == t for e in ents)
        known = type_policy.known_types(_CFG)
        enabled = type_policy.resolve(type_policy.PROFILE_MAXIMUM, {t: False},
                                      known=known)
        anon, kept = tokenize(doc, ents, _CFG, enabled_types=enabled)
        assert "[%s" % t not in anon, anon
        assert all(e.entity_type != t for e in kept)


class TestUiLabels:
    """Экран настроек берёт состав у сервера (`known_types`), а подпись — у
    `core.TYPE_LABELS`. Тип без подписи вышел бы к юристу кодом."""

    @pytest.mark.parametrize("t", ["PERCENT", "TERM"])
    def test_type_has_a_human_label_on_the_settings_screen(self, t):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        import core

        view = core.settings_view(_CFG)
        row = next((r for r in view["types"] if r["type"] == t), None)
        assert row is not None, [r["type"] for r in view["types"]]
        assert row["label"] != t and row["label"].strip()
