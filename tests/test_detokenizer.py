"""Тесты блока 6 — detokenizer.py (detokenize). Использует реальный session_store через tmp_path."""
import uuid

import pytest

from models import Entity
from session_store import save_session, SessionNotFoundError, SessionExpiredError
from detokenizer import detokenize


def _entity(token, entity_type, original_text, segment_id="p0"):
    return Entity(
        id=str(uuid.uuid4()),
        segment_id=segment_id,
        start=0,
        end=len(original_text),
        original_text=original_text,
        entity_type=entity_type,
        detector="ner",
        confidence=1.0,
        token=token,
    )


class TestDetokenizeThreeValidTokens:
    """HANDOFF_6, Данные для тестов, п.1 — 3 валидных токена."""

    def test_all_three_tokens_replaced(self, tmp_path):
        """HANDOFF_6, п.1: 'Договор между [ORG_1] (ИНН [INN_1]) и [PERSON_1].' -> все заменены на исходные значения."""
        entities = [
            _entity("[ORG_1]", "ORG", "ООО «Ромашка»"),
            _entity("[INN_1]", "INN", "7701234567"),
            _entity("[PERSON_1]", "PERSON", "Иванов И.И."),
        ]
        sid = save_session(entities, storage_dir=str(tmp_path))
        restored, unresolved = detokenize(
            "Договор между [ORG_1] (ИНН [INN_1]) и [PERSON_1].", sid, storage_dir=str(tmp_path)
        )
        assert restored == "Договор между ООО «Ромашка» (ИНН 7701234567) и Иванов И.И.."

    def test_unresolved_is_empty_when_all_tokens_known(self, tmp_path):
        """HANDOFF_6, п.1: unresolved пуст, когда все токены найдены в сессии."""
        entities = [
            _entity("[ORG_1]", "ORG", "ООО «Ромашка»"),
            _entity("[INN_1]", "INN", "7701234567"),
            _entity("[PERSON_1]", "PERSON", "Иванов И.И."),
        ]
        sid = save_session(entities, storage_dir=str(tmp_path))
        _, unresolved = detokenize(
            "Договор между [ORG_1] (ИНН [INN_1]) и [PERSON_1].", sid, storage_dir=str(tmp_path)
        )
        assert unresolved == []


class TestDetokenizeValidAndMissingWithRepeat:
    """HANDOFF_6, Данные для тестов, п.2 — валидный + несуществующий, с повтором несуществующего."""

    def test_valid_token_replaced_missing_token_kept(self, tmp_path):
        """HANDOFF_6, п.2: 'Компания [ORG_1], ссылка [ORG_99] и снова [ORG_99].' -> [ORG_1] заменён, [ORG_99] остался."""
        sid = save_session([_entity("[ORG_1]", "ORG", "ООО «Ромашка»")], storage_dir=str(tmp_path))
        restored, _ = detokenize(
            "Компания [ORG_1], ссылка [ORG_99] и снова [ORG_99].", sid, storage_dir=str(tmp_path)
        )
        assert restored == "Компания ООО «Ромашка», ссылка [ORG_99] и снова [ORG_99]."

    def test_missing_token_appears_once_in_unresolved_despite_repeat(self, tmp_path):
        """HANDOFF_6, п.2: несуществующий токен встретился дважды в тексте, но в unresolved попадает один раз."""
        sid = save_session([_entity("[ORG_1]", "ORG", "ООО «Ромашка»")], storage_dir=str(tmp_path))
        _, unresolved = detokenize(
            "Компания [ORG_1], ссылка [ORG_99] и снова [ORG_99].", sid, storage_dir=str(tmp_path)
        )
        assert unresolved == ["[ORG_99]"]


class TestDetokenizeNoTokens:
    """HANDOFF_6, Данные для тестов, п.3 — текст без единого токена."""

    def test_text_without_tokens_is_unchanged(self, tmp_path):
        """HANDOFF_6, п.3: 'Просто текст без токенов.' -> тот же текст без изменений."""
        sid = save_session([], storage_dir=str(tmp_path))
        restored, unresolved = detokenize("Просто текст без токенов.", sid, storage_dir=str(tmp_path))
        assert restored == "Просто текст без токенов."
        assert unresolved == []


class TestDetokenizeSessionErrors:
    """HANDOFF_6, Данные для тестов, п.4 — просроченная сессия; исключения не перехватываются."""

    def test_expired_session_raises_session_expired_error(self, tmp_path):
        """HANDOFF_6, п.4: expires_at сессии в прошлом -> detokenize кидает SessionExpiredError, не перехватывая."""
        sid = save_session([], ttl_hours=-1, storage_dir=str(tmp_path))
        with pytest.raises(SessionExpiredError):
            detokenize("любой текст", sid, storage_dir=str(tmp_path))

    def test_nonexistent_session_raises_session_not_found_error(self, tmp_path):
        """Спека, блок 6: detokenize не перехватывает SessionNotFoundError, пробрасывает наверх."""
        with pytest.raises(SessionNotFoundError):
            detokenize("любой текст", str(uuid.uuid4()), storage_dir=str(tmp_path))


class TestDetokenizeMalformedTokens:
    """HANDOFF_6, Данные для тестов, п.5 — искажённые LLM токены (известное ограничение)."""

    def test_malformed_tokens_are_not_replaced_and_not_in_unresolved(self, tmp_path):
        """HANDOFF_6, п.5: искажённые варианты ([org_1], ORG_1, [ORG 1]) не заменяются и не попадают в unresolved."""
        sid = save_session([_entity("[ORG_1]", "ORG", "Тест")], storage_dir=str(tmp_path))
        restored, unresolved = detokenize(
            "lower [org_1] noBrackets ORG_1 spaced [ORG 1] valid [ORG_1]", sid, storage_dir=str(tmp_path)
        )
        assert "[org_1]" in restored
        assert "ORG_1" in restored
        assert "[ORG 1]" in restored
        assert unresolved == []

    def test_only_the_well_formed_token_gets_replaced(self, tmp_path):
        """HANDOFF_6, п.5: единственный корректно оформленный токен [ORG_1] в конце строки заменяется на 'Тест'."""
        sid = save_session([_entity("[ORG_1]", "ORG", "Тест")], storage_dir=str(tmp_path))
        restored, _ = detokenize(
            "lower [org_1] noBrackets ORG_1 spaced [ORG 1] valid [ORG_1]", sid, storage_dir=str(tmp_path)
        )
        assert restored.endswith("valid Тест")


class TestDetokenizeSinglePassNoCascade:
    """Спека, блок 6: замена — один проход re.sub, без повторного скана заменённого текста."""

    def test_original_text_containing_token_like_substring_is_not_rescanned(self, tmp_path):
        """Спека, блок 6: если original_text одной сущности содержит подстроку токена, каскадной замены не происходит."""
        entities = [
            _entity("[ORG_1]", "ORG", "содержит [INN_1] внутри"),
            _entity("[INN_1]", "INN", "СЕКРЕТ"),
        ]
        sid = save_session(entities, storage_dir=str(tmp_path))
        restored, unresolved = detokenize("[ORG_1]", sid, storage_dir=str(tmp_path))
        assert restored == "содержит [INN_1] внутри"
        assert unresolved == []


class TestDetokenizeInvariants:
    """HANDOFF_6, Инварианты выходных данных."""

    def test_unresolved_order_is_first_appearance_in_text(self, tmp_path):
        """Спека, блок 6: unresolved в порядке первого появления в тексте (не в сессии)."""
        sid = save_session([], storage_dir=str(tmp_path))
        _, unresolved = detokenize("[B_2] текст [A_1] и снова [B_2]", sid, storage_dir=str(tmp_path))
        assert unresolved == ["[B_2]", "[A_1]"]

    def test_return_type_is_tuple_of_str_and_list(self, tmp_path):
        """HANDOFF_6, Формат данных на выходе: detokenize возвращает tuple[str, list[str]]."""
        sid = save_session([], storage_dir=str(tmp_path))
        result = detokenize("текст", sid, storage_dir=str(tmp_path))
        assert isinstance(result, tuple)
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)
