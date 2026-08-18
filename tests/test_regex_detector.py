"""Тесты блока 2 — regex_detector.py (detect_regex, inn_checksum, ogrn_checksum, VALIDATORS)."""
import sys

import pytest

from models import SourceDocument, TextSegment
from regex_detector import detect_regex, inn_checksum, ogrn_checksum, VALIDATORS


def _doc(*texts):
    """Строит SourceDocument из списка строк, по одному txt_line сегменту на строку."""
    segments = [
        TextSegment(id=f"l{i}", text=t, source_type="txt_line", metadata={"line_index": i, "encoding": "utf-8-sig"})
        for i, t in enumerate(texts)
    ]
    return SourceDocument(segments=segments, source_format="txt", source_path="dummy.txt")


class TestInnChecksum:
    """Спека, блок 2: inn_checksum(value) по алгоритму ФНС (10 и 12 знаков)."""

    def test_valid_10_digit_inn_passes(self):
        """HANDOFF_2, Данные для тестов: '7707083893' — валидный 10-значный ИНН."""
        assert inn_checksum("7707083893") is True

    def test_invalid_10_digit_inn_fails(self):
        """HANDOFF_2, Данные для тестов: '7707083894' — чек-сумма не сходится."""
        assert inn_checksum("7707083894") is False

    def test_valid_12_digit_inn_passes(self):
        """HANDOFF_2, Данные для тестов: 12-значный ИНН '500100732259' валиден."""
        assert inn_checksum("500100732259") is True

    def test_invalid_12_digit_inn_fails(self):
        """HANDOFF_2, Данные для тестов: 12-значный ИНН '500100732258' невалиден."""
        assert inn_checksum("500100732258") is False

    def test_non_digit_value_fails(self):
        """Спека, блок 2: валидатор принимает m.group(0); нецифровая строка не проходит проверку."""
        assert inn_checksum("abcdefghij") is False

    def test_wrong_length_fails(self):
        """Спека, блок 2: ИНН только 10 или 12 знаков; иная длина — не валидна."""
        assert inn_checksum("12345") is False


class TestOgrnChecksum:
    """Спека, блок 2: ogrn_checksum(value) — 13 знаков mod 11, 15 знаков (ОГРНИП) mod 13."""

    def test_valid_13_digit_ogrn_passes(self):
        """HANDOFF_2, Данные для тестов: 'ОГРН 1027700132195' — валидный ОГРН."""
        assert ogrn_checksum("1027700132195") is True

    def test_invalid_13_digit_ogrn_fails(self):
        """HANDOFF_2, Данные для тестов: 'ОГРН 1027700132196' — чек-сумма не сходится."""
        assert ogrn_checksum("1027700132196") is False

    def test_valid_15_digit_ogrnip_passes(self):
        """HANDOFF_2, Данные для тестов: 'ОГРНИП 304500116000157' — валидный ОГРНИП."""
        assert ogrn_checksum("304500116000157") is True

    def test_wrong_length_fails(self):
        """Спека, блок 2: ОГРН/ОГРНИП только 13 или 15 знаков; иная длина невалидна."""
        assert ogrn_checksum("123") is False


class TestValidatorsRegistry:
    """Спека, блок 2: VALIDATORS — реестр {'inn_checksum': ..., 'ogrn_checksum': ...}."""

    def test_registry_contains_both_validators(self):
        """Спека, блок 2: VALIDATORS резолвит имена inn_checksum/ogrn_checksum из конфига.
        ЭТАП T2-INN: к ним добавились inn10_checksum/inn12_checksum — прибитые к
        длине входы в тот же алгоритм ФНС (типов ИНН стало два, и каждый обязан
        звать СВОЙ). Множество проверяется целиком: незамеченное появление
        валидатора значило бы, что конфиг может сослаться на непроверенное имя."""
        assert set(VALIDATORS.keys()) == {
            "inn_checksum", "inn10_checksum", "inn12_checksum", "ogrn_checksum"}

    def test_length_pinned_validators_reject_the_other_length(self):
        """ЭТАП T2-INN: 10-значный валидатор обязан отвергать 12-значное значение
        и наоборот — даже когда КС по «своему» алгоритму сошлась бы. Это защита
        от будущего ослабления паттерна, а не украшение."""
        from regex_detector import inn10_checksum, inn12_checksum
        assert inn10_checksum("7707083893") is True
        assert inn10_checksum("500100732259") is False   # валидный ИНН физлица
        assert inn12_checksum("500100732259") is True
        assert inn12_checksum("7707083893") is False     # валидный ИНН юрлица

    def test_registry_functions_match_module_level_functions(self):
        """Спека, блок 2: VALIDATORS['inn_checksum'] — та же функция, что и inn_checksum."""
        assert VALIDATORS["inn_checksum"] is inn_checksum
        assert VALIDATORS["ogrn_checksum"] is ogrn_checksum


class TestDetectRegexChecksumFiltering:
    """HANDOFF_2, Пример 1 — валидные и испорченные чек-суммы ИНН/ОГРН.

    ЭТАП 4 переопределил роль `validate:` (см. entity_types.yaml, комментарий у
    INN/OGRN, и src/regex_detector.py `_has_anchor`): КС — фильтр только БЕЗ
    якоря. «ИНН 7707083894»/«ОГРН 1027700132196» — якорь «ИНН»/«ОГРН» стоит
    прямо перед значением, поэтому теперь значение МАСКИРУЕТСЯ невзирая на
    невалидную КС (было: отбраковка, HANDOFF_2). Отбраковка по КС осталась
    ЖИВОЙ, но только для голого числа без якоря — см.
    TestDetectRegexChecksumIsFilterOnlyWithoutAnchor ниже."""

    def test_valid_inn_and_kpp_found_with_correct_offsets(self, config_path):
        """HANDOFF_2, Пример 1: 'ИНН 7707083893, КПП 773601001' -> INN[4:14], KPP[20:29]."""
        doc = _doc("ИНН 7707083893, КПП 773601001")
        entities = detect_regex(doc, config_path)
        by_type = {e.entity_type: e for e in entities}
        assert (by_type["INN"].start, by_type["INN"].end) == (4, 14)
        assert (by_type["KPP"].start, by_type["KPP"].end) == (20, 29)

    def test_anchored_invalid_inn_checksum_is_still_masked(self, config_path):
        """ЭТАП 4: 'ИНН 7707083894' (якорь «ИНН», КС не сходится) -> INN найден.
        Инвариант продукта: невалидный по КС номер — всё равно ПДн (STATE §6)."""
        doc = _doc("ИНН 7707083894")
        entities = detect_regex(doc, config_path)
        inn = [e for e in entities if e.entity_type == "INN"]
        assert len(inn) == 1
        assert inn[0].original_text == "7707083894"

    def test_valid_ogrn_and_ogrnip_found(self, config_path):
        """HANDOFF_2, Пример 1: 'ОГРН 1027700132195, ОГРНИП 304500116000157' -> два OGRN-Entity."""
        doc = _doc("ОГРН 1027700132195, ОГРНИП 304500116000157")
        entities = detect_regex(doc, config_path)
        ogrn_values = {e.original_text for e in entities if e.entity_type == "OGRN"}
        assert ogrn_values == {"1027700132195", "304500116000157"}

    def test_anchored_invalid_ogrn_checksum_is_still_masked(self, config_path):
        """ЭТАП 4: 'ОГРН 1027700132196' (якорь «ОГРН», КС не сходится) -> OGRN найден."""
        doc = _doc("ОГРН 1027700132196")
        entities = detect_regex(doc, config_path)
        assert [e.original_text for e in entities] == ["1027700132196"]
        assert entities[0].entity_type == "OGRN"


class TestDetectRegexChecksumIsFilterOnlyWithoutAnchor:
    """ЭТАП 4 — асимметрия «якорь / без якоря» (entity_types.yaml, INN/OGRN/
    BANK_ACCOUNT; src/regex_detector.py `_has_anchor`). Без якоря КС остаётся
    шлагбаумом: голое число, невалидное по КС и без метки-триггера рядом, —
    вероятно не реквизит (номер пункта, КБК, произвольная цифирь в прозе),
    не маскируется."""

    def test_bare_invalid_inn_without_anchor_produces_no_entity(self, config_path):
        """Тот же невалидный ИНН '7707083894', но без слова «ИНН» рядом -> ничего."""
        doc = _doc("В реестре встречается число 7707083894 без пояснений.")
        entities = detect_regex(doc, config_path)
        assert [e for e in entities if e.entity_type == "INN"] == []

    def test_bare_valid_inn_without_anchor_is_still_masked(self, config_path):
        """Зеркало: тот же голый ИНН, но с ВАЛИДНОЙ КС '7707083893' -> маскируется
        и без якоря — КС сама подтверждает реквизит (поведение до этапа 4 не
        меняется на этой ветке)."""
        doc = _doc("В реестре встречается число 7707083893 без пояснений.")
        entities = detect_regex(doc, config_path)
        assert [e.original_text for e in entities if e.entity_type == "INN"] == ["7707083893"]

    def test_bare_invalid_ogrn_without_anchor_produces_no_entity(self, config_path):
        """Тот же невалидный ОГРН '1027700132196', но без слова «ОГРН» рядом -> ничего."""
        doc = _doc("В реестре встречается число 1027700132196 без пояснений.")
        entities = detect_regex(doc, config_path)
        assert [e for e in entities if e.entity_type == "OGRN"] == []

    def test_anchor_from_unrelated_earlier_number_does_not_leak_across(self, config_path):
        """Якорь одного реквизита не защищает СОСЕДНЕЕ, никак с ним не связанное
        число (КБК/произвольный 20-значный код рядом со счётом) — см. _has_anchor:
        якорное окно не пересекает цифру предыдущего значения."""
        doc = _doc("р/с 40702810160368220597, КБК 18210101011011000110")
        entities = detect_regex(doc, config_path)
        accounts = [e.original_text for e in entities if e.entity_type == "BANK_ACCOUNT"]
        assert accounts == ["40702810160368220597"]


class TestDetectRegexPassportAnchor:
    """HANDOFF_2, Пример 2 — паспорт: якорь обязателен."""

    def test_passport_with_anchor_produces_single_entity_without_anchor(self, config_path):
        """ЭТАП DEBTS, долг 0c-B: якорь ОБЯЗАТЕЛЕН для поиска и ЗАПРЕЩЁН в спане.

        Прежняя редакция этого теста пришпиливала ровно обратное («якорь включён
        в спан») — она фиксировала сам дефект 0c-B, а не контракт: слово
        «паспорт» уходило под маску вместе со значением, и на корпусе v1 это
        стоило 428 сущностей / 3441 символ перебора границ (линия «е» гейта).
        Контракт перевёрнут по заданию владельца; ослаблением теста это не
        является — требование «без якоря паспорта нет» ниже осталось прежним.
        """
        doc = _doc("паспорт серия 45 12 345678 выдан ОВД")
        entities = detect_regex(doc, config_path)
        passport = [e for e in entities if e.entity_type == "PASSPORT"]
        assert len(passport) == 1
        assert passport[0].original_text == "45 12 345678"
        assert (passport[0].start, passport[0].end) == (14, 26)

    def test_random_10_digit_number_without_anchor_produces_no_passport(self, config_path):
        """HANDOFF_2, Пример 2: 'случайное число 4512345678 без якоря' -> ни одного PASSPORT (и ИНН тоже не сходится)."""
        doc = _doc("случайное число 4512345678 без якоря")
        entities = detect_regex(doc, config_path)
        assert [e for e in entities if e.entity_type == "PASSPORT"] == []
        assert [e for e in entities if e.entity_type == "INN"] == []


class TestDetectRegexKppBikOverlap:
    """HANDOFF_2, Пример 3 — пересечение KPP/BIK, не разрешается блоком 2 (зона блока 4)."""

    def test_nine_digit_starting_with_04_produces_both_kpp_and_bik(self, config_path):
        """HANDOFF_2, Пример 3: девятизначное число вида БИК даёт И KPP, И BIK на
        ОДНОМ интервале [38:47] — блок 2 это пересечение не разрешает (зона блока 4).

        ЭТАП A5 — ИСПРАВЛЕН КОНТРАКТ, НЕ ОСЛАБЛЕН. До этапа A4 у KPP не было
        якоря, и слово-носитель в тексте было безразлично: тест ставил «БИК
        044525225» и получал KPP «просто потому, что девять цифр». Этап A4 дал
        KPP якорь (?i)кпп-шлагбаум (реальный договор без единого слова «КПП» до
        того давал 10 ложных KPP из 10) — и старая формулировка стала кодировать
        снятое поведение как контракт. Утверждение теста прежнее и не ослаблено:
        те же два типа, тот же один интервал, та же непринадлежность разрешения
        блоку 2; изменился лишь носитель — число стоит под своим якорем «КПП»
        (длина слова та же, интервал [38:47] не сдвинулся), и BIK по-прежнему
        берёт его безъякорно по форме 04+7.

        ЭТАП NEG — ТО ЖЕ САМОЕ ПОВТОРИЛОСЬ С BIK, И ПО ТОЙ ЖЕ ПРИЧИНЕ. Пять
        разных видов случая (накладная, каталожный номер, заводской номер, код
        цели, номер заявки) дали 690 ложных масок BIK из 690 возможных, и BIK
        получил якорь-шлагбаум `(?i)\\bбик\\b` — ровно тем приёмом, каким его
        получил KPP в A4. Хвост докстринга «BIK по-прежнему берёт его
        безъякорно» описывал СНЯТОЕ поведение и потому здесь опровергнут.
        Утверждение теста снова не ослаблено: те же два типа, тот же один
        интервал [38:47], та же непринадлежность разрешения блоку 2 — сменился
        только носитель, теперь число стоит под ОБОИМИ своими якорями, а длина
        левой части подобрана так, что интервал не сдвинулся."""
        doc = _doc("р/с 40702810400000012345, КПП и БИК № 044525225")
        entities = detect_regex(doc, config_path)
        types_at_same_span = {e.entity_type for e in entities if (e.start, e.end) == (38, 47)}
        assert types_at_same_span == {"KPP", "BIK"}

    def test_bank_account_found_alongside_kpp_bik_overlap(self, config_path):
        """HANDOFF_2, Пример 3: тот же вход также даёт BANK_ACCOUNT('40702810400000012345', [4:24])."""
        doc = _doc("р/с 40702810400000012345 в банке, БИК 044525225")
        entities = detect_regex(doc, config_path)
        accounts = [e for e in entities if e.entity_type == "BANK_ACCOUNT"]
        assert len(accounts) == 1
        assert (accounts[0].original_text, accounts[0].start, accounts[0].end) == ("40702810400000012345", 4, 24)


class TestDetectRegexSum:
    """HANDOFF_2, Пример 4 — SUM и правая граница."""

    def test_sum_with_nbsp_and_ruб_suffix_matched(self, config_path):
        """HANDOFF_2, Пример 4: '1 500 000,00 руб.' и '500 000 ₽' распознаются как SUM."""
        doc = _doc("Цена договора: 1 500 000,00 руб. без НДС; аванс 500 000 ₽")
        entities = detect_regex(doc, config_path)
        sums = {e.original_text for e in entities if e.entity_type == "SUM"}
        assert "1 500 000,00 руб." in sums
        assert "500 000 ₽" in sums

    def test_sum_right_boundary_does_not_eat_next_word(self, config_path):
        """HANDOFF_2, Пример 4: '5 рубашек' -> ничего (правая граница не должна съедать 'ашек')."""
        doc = _doc("куплено 5 рубашек")
        entities = detect_regex(doc, config_path)
        assert [e for e in entities if e.entity_type == "SUM"] == []


class TestDetectRegexDisabledType:
    """HANDOFF_2, Пример 5 — выключенный тип (enabled: false).

    ЭТАП DATE-ON. Примером выключенного типа здесь служил боевой DATE — с
    первого коммита проекта и до 2026-08-18. Владелец дату ВКЛЮЧИЛ, и проверка
    механизма переехала на СВОЙ временный конфиг (как у соседнего теста про
    незнакомый validate): механизм «enabled: false» никуда не делся и обязан
    охраняться независимо от того, пользуется ли им сегодня хоть один боевой
    тип. Рядом — зеркало на боевом конфиге, доказывающее, что дата теперь
    находится: без него зелёный тест на временном конфиге не отличал бы
    «механизм работает» от «дата снова замолчала».
    """

    def test_disabled_type_produces_no_entity(self, tmp_path):
        """HANDOFF_2, Пример 5: тип с enabled: false не даёт ни одной сущности."""
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            "entity_types:\n"
            "  DATE:\n"
            "    method: regex\n"
            "    pattern: '\\d{1,2}[.\\/]\\d{1,2}[.\\/]\\d{2,4}'\n"
            "    enabled: false\n"
            "    token_prefix: DATE\n",
            encoding="utf-8",
        )
        doc = _doc("дата 12.07.2026")
        assert detect_regex(doc, str(cfg)) == []

    def test_same_type_enabled_in_the_same_config_does_produce_it(self, tmp_path):
        """Красное состояние проверки выше: тот же тип, тот же текст, тот же
        код — без `enabled: false` сущность появляется. Иначе зелёный тест
        нельзя отличить от «паттерн просто не совпал»."""
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            "entity_types:\n"
            "  DATE:\n"
            "    method: regex\n"
            "    pattern: '\\d{1,2}[.\\/]\\d{1,2}[.\\/]\\d{2,4}'\n"
            "    token_prefix: DATE\n",
            encoding="utf-8",
        )
        doc = _doc("дата 12.07.2026")
        assert [e.original_text for e in detect_regex(doc, str(cfg))] == ["12.07.2026"]

    def test_date_is_no_longer_disabled_in_the_shipping_config(self, config_path):
        """ЭТАП DATE-ON, решение владельца «дату включить во всех наборах»:
        на БОЕВОМ конфиге дата обязана находиться — и цифрой, и словом."""
        doc = _doc("Договор от 12.07.2026 г.", "Составлен «12» июля 2026 года",
                   "подписан двенадцатого июля 2026 года")
        found = sorted(e.original_text for e in detect_regex(doc, config_path)
                       if e.entity_type == "DATE")
        assert found == ["12.07.2026 г.", "«12» июля 2026 года",
                         "двенадцатого июля 2026 года"]


class TestDetectRegexUnknownValidate:
    """Спека, блок 2: незнакомый validate в конфиге -> предупреждение в stderr, тип пропущен целиком."""

    def test_unknown_validate_prints_warning_and_skips_type_entirely(self, tmp_path, capsys):
        """HANDOFF_2, Пример 5: validate 'snils_checksum' не в VALIDATORS -> предупреждение в stderr, по SNILS ни одного Entity."""
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            "entity_types:\n"
            "  SNILS:\n"
            "    method: regex\n"
            "    pattern: '\\d{11}'\n"
            "    validate: snils_checksum\n"
            "    token_prefix: SNILS\n",
            encoding="utf-8",
        )
        doc = _doc("12345678901")
        entities = detect_regex(doc, str(cfg))
        captured = capsys.readouterr()
        assert entities == []
        assert "snils_checksum" in captured.err

    def test_other_types_still_work_when_one_type_has_unknown_validate(self, tmp_path):
        """Спека, блок 2: незнакомый validate у одного типа не мешает остальным типам конфига работать."""
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            "entity_types:\n"
            "  SNILS:\n"
            "    method: regex\n"
            "    pattern: '\\d{11}'\n"
            "    validate: snils_checksum\n"
            "    token_prefix: SNILS\n"
            "  EMAIL:\n"
            "    method: regex\n"
            "    pattern: '[\\w.-]+@[\\w.-]+\\.\\w+'\n"
            "    token_prefix: EMAIL\n",
            encoding="utf-8",
        )
        doc = _doc("test@example.com")
        entities = detect_regex(doc, str(cfg))
        assert [e.entity_type for e in entities] == ["EMAIL"]


class TestDetectRegexConfigErrors:
    """HANDOFF_2, Публичный интерфейс — исключения detect_regex."""

    def test_missing_config_file_raises_file_not_found_error(self):
        """HANDOFF_2, Пример 5: detect_regex(doc, 'нет_такого.yaml') -> FileNotFoundError."""
        doc = _doc("текст")
        with pytest.raises(FileNotFoundError):
            detect_regex(doc, "нет_такого_файла.yaml")

    def test_config_without_entity_types_key_raises_key_error(self, tmp_path):
        """HANDOFF_2, Публичный интерфейс: KeyError — если в конфиге нет верхнеуровневого ключа entity_types."""
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("something_else: {}\n", encoding="utf-8")
        doc = _doc("текст")
        with pytest.raises(KeyError):
            detect_regex(doc, str(cfg))


class TestDetectRegexInvariants:
    """HANDOFF_2, Инварианты выходных данных."""

    def test_all_entities_have_detector_regex(self, config_path):
        """HANDOFF_2, Инварианты: у каждого Entity detector == 'regex'."""
        doc = _doc("ИНН 7707083893")
        entities = detect_regex(doc, config_path)
        assert all(e.detector == "regex" for e in entities)

    def test_all_entities_have_confidence_one(self, config_path):
        """HANDOFF_2, Инварианты: у каждого Entity confidence == 1.0."""
        doc = _doc("ИНН 7707083893")
        entities = detect_regex(doc, config_path)
        assert all(e.confidence == 1.0 for e in entities)

    def test_all_entities_have_token_none(self, config_path):
        """HANDOFF_2, Инварианты: у каждого Entity token is None (заполняется в блоке 4)."""
        doc = _doc("ИНН 7707083893")
        entities = detect_regex(doc, config_path)
        assert all(e.token is None for e in entities)

    def test_original_text_matches_segment_slice(self, config_path):
        """HANDOFF_2, Инварианты: entity.original_text == segment.text[entity.start:entity.end]."""
        doc = _doc("ИНН 7707083893, КПП 773601001")
        entities = detect_regex(doc, config_path)
        seg_text = doc.segments[0].text
        for e in entities:
            assert e.original_text == seg_text[e.start:e.end]

    def test_empty_segment_text_produces_no_entities(self, config_path):
        """HANDOFF_2, Предусловия: пустой text допустим, совпадений просто нет."""
        doc = _doc("")
        entities = detect_regex(doc, config_path)
        assert entities == []
