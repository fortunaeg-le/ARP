"""Этап 3 (BREAKING) — направление 1: УТЕЧКА ПДн МИМО ТОКЕНИЗАЦИИ.

Ищем тексты, где чувствительное значение остаётся в итоговом анонимизированном
тексте в открытом (или искажённом, но восстановимом) виде, хотя по смыслу должно
было токенизироваться.

Находки:
  B2 (КРИТИЧНО): структурные реквизиты, записанные С ПРОБЕЛАМИ ВНУТРИ — расчётный
     счёт "40702 810 4 0000 1234567", ИНН "7707 083893" — не матчатся паттернами
     (\\b\\d{20}\\b / \\b\\d{10}\\b требуют слитных цифр) и утекают целиком. Счета
     в договорах РФ группируют пробелами почти всегда — это не экзотика.
  B3 (КРИТИЧНО): сущность, разорванная границей сегмента (перенос абзаца/строки/
     ячейки), детекторами не находится — они работают посегментно. Телефон,
     разорванный переносом, утекает целиком; название организации — частично.
  B4 (ВАЖНО): телефон с кириллическим омоглифом (З вместо 3, О вместо 0 и т.п.,
     частый артефакт копипаста/OCR) не матчится \\d — остальные реальные цифры
     номера утекают.
  B5 (ВАЖНО, новый путь к симптому закрытой находки #7): .txt в UTF-16 БЕЗ BOM
     декодируется как utf-8-sig в моджибейк (utf-8 не падает на этих байтах,
     отката на cp1251 нет), ПДн не детектируются и утекают искажёнными. Фикс #7
     закрыл ТОЛЬКО UTF-16 c BOM.

Контроль-подтверждение устойчивости (PASS): эмодзи/не-BMP символы НЕ сдвигают
смещения (Python работает по кодовым точкам) — ИНН/телефон после эмодзи находятся.

Код не чинится. xfail(strict) — репродукция подтверждённого дефекта корректного
поведения; PASS(pins) — фиксация фактического (утекающего) поведения как есть.
"""
import pytest

from extractor import extract
from regex_detector import detect_regex
from ner_detector import detect_ner
from anchor_registry import detect_org, suppress_conflicts
from tokenizer import tokenize


def _full_pipeline(doc, config_path):
    # Этап A': ПОЛНЫЙ конвейер, включая структурный ORG-движок и арбитраж типов.
    # detect_org считается ДО detect_ner и передаётся туда барьером адреса (A-4).
    rx = detect_regex(doc, config_path)
    org = detect_org(doc, regex_entities=rx)
    ner = detect_ner(doc, config_path, regex_entities=rx, org_entities=org)
    ner = suppress_conflicts(org, ner)
    return rx + ner + org


def _anonymize_txt(text, tmp_path, config_path, *, filename="d.txt", raw_bytes=None):
    """Полный конвейер encrypt (без сессии) над .txt; возвращает анонимизированный текст."""
    p = tmp_path / filename
    if raw_bytes is not None:
        p.write_bytes(raw_bytes)
    else:
        p.write_text(text, encoding="utf-8")
    doc = extract(str(p))
    anon, _ = tokenize(doc, _full_pipeline(doc, config_path), config_path)
    return anon


def _anonymize_doc(doc, config_path):
    anon, _ = tokenize(doc, _full_pipeline(doc, config_path), config_path)
    return anon


# --------------------------------------------------------------------------- #
# B2 — КРИТИЧНО: реквизиты с пробелами внутри утекают
# --------------------------------------------------------------------------- #
class TestSpacedRequisitesLeak:
    """Расчётный счёт и ИНН, записанные группами через пробел — реальный формат
    договоров — не токенизируются и остаются в открытом тексте. Ожидаемо-корректно
    было бы их анонимизировать; сейчас утекают => xfail(strict)."""

    def test_bank_account_with_spaces_is_tokenized(self, tmp_path, config_path):
        """B2-фикс: счёт, сгруппированный обычными пробелами, теперь матчится
        (\\b\\d(?:[ ]?\\d){19}\\b) и токенизируется — не утекает."""
        acct_spaced = "40702 810 4 0000 1234567"  # тот же счёт, что слитно даёт ACCOUNT_1
        anon = _anonymize_txt(f"Расчётный счёт {acct_spaced} в банке", tmp_path, config_path)
        assert acct_spaced not in anon, "номер счёта утёк в открытом виде"

    def test_bank_account_with_nbsp_is_tokenized(self, tmp_path, config_path):
        """B2-фикс, вариант с НЕРАЗРЫВНЫМ пробелом (U+00A0) — как вставляет Word при
        группировке чисел: тот же счёт также должен токенизироваться."""
        acct_nbsp = "40702 810 4 0000 1234567"
        anon = _anonymize_txt(f"Расчётный счёт {acct_nbsp} в банке", tmp_path, config_path)
        assert acct_nbsp not in anon, "счёт с неразрывными пробелами утёк"

    def test_inn_with_inner_space_is_tokenized(self, tmp_path, config_path):
        """B2-фикс: ИНН с обычным пробелом внутри токенизируется; чек-сумма считается
        по значению БЕЗ пробелов (разделители снимаются внутри inn_checksum)."""
        anon = _anonymize_txt("ИНН 7707 083893 организации", tmp_path, config_path)
        assert "7707 083893" not in anon, "ИНН утёк в открытом виде"

    def test_inn_with_nbsp_is_tokenized(self, tmp_path, config_path):
        """B2-фикс, вариант с неразрывным пробелом внутри ИНН."""
        inn_nbsp = "7707 083893"
        anon = _anonymize_txt(f"ИНН {inn_nbsp} организации", tmp_path, config_path)
        assert inn_nbsp not in anon, "ИНН с неразрывным пробелом утёк"

    def test_contiguous_requisites_pin_current_ok(self, tmp_path, config_path):
        """Контроль: те же реквизиты СЛИТНО токенизируются — значит дело именно
        в пробелах, а не в самих значениях."""
        anon = _anonymize_txt(
            "Счёт 40702810400001234567 ИНН 7707083893", tmp_path, config_path
        )
        assert "40702810400001234567" not in anon
        assert "7707083893" not in anon
        assert "[ACCOUNT_1]" in anon and "[INN_1]" in anon


# --------------------------------------------------------------------------- #
# B3 — КРИТИЧНО: сущность на границе сегментов утекает
# --------------------------------------------------------------------------- #
class TestEntitySplitAcrossSegmentBoundary:
    """Детекторы работают посегментно (по строкам .txt / абзацам / ячейкам docx).
    Значение, разорванное границей сегмента, не находится ни в одном из них."""

    def test_phone_split_across_txt_lines_is_tokenized(self, tmp_path, config_path):
        # номер разорван переносом строки между кодом и остатком
        anon = _anonymize_txt("Звоните +7 (495)\n123-45-67 в будни", tmp_path, config_path)
        # если бы находился — обе части исчезли бы; проверяем, что цифры номера не в тексте
        assert "123-45-67" not in anon, "хвост телефона утёк"

    def test_phone_split_across_table_cells_is_tokenized(self, tmp_path, config_path, docx_factory):
        # две соседние ячейки: код в одной, остаток в другой
        path = docx_factory(
            "split.docx",
            table_rows=[["+7 (495)", "123-45-67"]],
        )
        doc = extract(path)
        anon = _anonymize_doc(doc, config_path)
        assert "123-45-67" not in anon, "хвост телефона утёк через границу ячейки"

    @pytest.mark.xfail(strict=True, reason=(
        "Этап A: B3-реконструкция ORG через границу сегмента опиралась на Natasha-ORG "
        "в граничном окне (ner_label ORG). После отключения Natasha-ORG B3 ORG не даёт, "
        "а структурный движок (anchor_registry) — ПОСЕГМЕНТНЫЙ и разрыв «ООО\\nРомашка» "
        "не сшивает. Это РЕАЛЬНАЯ дыра покрытия (частичная утечка «Ромашка» через "
        "перенос), а не косметика: cross-segment ORG для structure-first ORG не "
        "реализован. Находка этапа A, см. HANDOFF_STAGE_A_ORG.")
    )
    def test_org_split_across_lines_partial_leak_pin(self, tmp_path, config_path):
        """B3-fix: организация 'ООО\\nРомашка', разорванная переносом абзаца, теперь
        ловится граничным окном детекции — отличительное имя 'Ромашка' больше НЕ
        остаётся в открытом тексте (раньше здесь фиксировалась частичная утечка)."""
        anon = _anonymize_txt("Договор с ООО\nРомашка на поставку", tmp_path, config_path)
        assert "Ромашка" not in anon  # отличительная часть имени организации токенизирована


# --------------------------------------------------------------------------- #
# B4 — ВАЖНО: телефон с кириллическим омоглифом
# --------------------------------------------------------------------------- #
class TestPhoneHomoglyphLeak:
    def test_phone_with_cyrillic_digit_lookalike_is_tokenized(self, tmp_path, config_path):
        # 'З' (U+0417) вместо '3' — частый артефакт копипаста из PDF/OCR.
        # Этап 2 (нормализация перед детекцией) сводит омоглиф-цифру внутри
        # числового токена, телефон детектируется и токенизируется. Ранее —
        # xfail(strict): \d рвался на кириллице, реальные цифры утекали.
        anon = _anonymize_txt("Тел: +7 (495) 12З-45-67 звоните", tmp_path, config_path)
        assert "45-67" not in anon, "реальные цифры номера утекли рядом с омоглифом"


# --------------------------------------------------------------------------- #
# B5 — ВАЖНО (новый путь к симптому закрытой находки #7): UTF-16 БЕЗ BOM
# --------------------------------------------------------------------------- #
class TestUtf16NoBomSilentCorruption:
    """Фикс #7 детектирует UTF-16 только по BOM (FF FE / FE FF). UTF-16-LE без BOM
    (файл, сгенерированный программно или с обрезанным BOM) утилита декодирует как
    utf-8-sig: на этих байтах utf-8 НЕ падает (нет UnicodeDecodeError), отката на
    cp1251 не происходит, получается моджибейк с нулевыми байтами внутри цифр —
    ИНН/телефон не детектируются. Тот же класс тихой порчи + утечки, что #7."""

    def test_utf16_no_bom_raises_instead_of_silent_corruption(self, tmp_path, config_path):
        """B5-фикс: надёжного автоопределения кодировки без BOM не существует, поэтому
        мы НЕ угадываем UTF-16-LE эвристикой по байтам (это дало бы ложные срабатывания
        на обычном UTF-8). Вместо тихой порчи (моджибейк -> ПДн не детектятся и утекают
        искажёнными) extract теперь поднимает явную ValueError с инструкцией пересохранить
        в UTF-8. Лучше внятный отказ, чем тихая утечка ПДн в LLM."""
        raw = "ИНН 7707083893 ООО Ромашка".encode("utf-16-le")  # без BOM
        p = tmp_path / "nobom.txt"
        p.write_bytes(raw)
        with pytest.raises(ValueError, match="кодировк"):
            extract(str(p))

    def test_utf16_no_bom_does_not_leak_pii_silently(self, tmp_path, config_path):
        """Прямая проверка симптома находки: раньше ИНН/ФИО молча утекали искажёнными
        (0 сущностей, «успех»). Теперь путь «тихо принять моджибейк» закрыт — попытка
        обработать такой файл завершается ошибкой, а не мнимым успехом с утечкой."""
        raw = "ИНН 7707083893 ООО Ромашка".encode("utf-16-le")
        p = tmp_path / "nobom2.txt"
        p.write_bytes(raw)
        with pytest.raises(ValueError):
            extract(str(p))


# --------------------------------------------------------------------------- #
# Контроль устойчивости (PASS): эмодзи/не-BMP не сдвигают смещения
# --------------------------------------------------------------------------- #
class TestEmojiOffsetsRobust:
    def test_inn_after_emoji_still_tokenized(self, tmp_path, config_path):
        anon = _anonymize_txt(
            "Клиент \U0001F600 Ivan ООО Ромашка ИНН 7707083893 конец", tmp_path, config_path
        )
        assert "7707083893" not in anon
        assert "[INN_1]" in anon

    def test_phone_after_emoji_still_tokenized(self, tmp_path, config_path):
        anon = _anonymize_txt("\U0001F4DE\U0001F600 контакт +7 (495) 123-45-67 ok", tmp_path, config_path)
        assert "123-45-67" not in anon
        assert "[PHONE_1]" in anon
