"""Этап 2 — проверка блока 6 (detokenizer.py) на искажениях токенов, которые вносит
реальный LLM-ответ. НЕ дублирует test_detokenizer.py этапа 1 (валидные токены,
unresolved, изменённый регистр/скобки/пробел уже проверены там).

Задача этапа: убедиться, что для искажений, которые спека помечает как "Известное
ограничение" (блок 6), РЕАЛЬНОЕ поведение СОВПАДАЕТ с обещанным: тихая потеря
искажённого токена, БЕЗ падения и БЕЗ порчи остального текста. Здесь берём случай,
не покрытый этапом 1: токен, разорванный переносом строки при переформулировке LLM.
Это зелёные тесты-подтверждения — они фиксируют, что обещание спеки держится.
"""
from pathlib import Path

from session_store import save_session
from detokenizer import detokenize
from models import Entity


def _entity(token, entity_type, original_text):
    return Entity(
        id=token, segment_id="p0", start=0, end=1, original_text=original_text,
        entity_type=entity_type, detector="ner", confidence=1.0, token=token,
    )


class TestTokenBrokenByNewline:
    """Реальный сценарий: LLM переформатировала ответ и перенос строки попал ВНУТРЬ
    токена ('[ORG_\\n1]'). Спека (блок 6, "Известное ограничение") обещает: такой
    токен не распознаётся, тихо теряется, но остальной текст и корректные токены
    не страдают, функция не падает. Подтверждаем это поведение как есть.
    """

    def _session(self, tmp_path):
        return save_session(
            [
                _entity("[ORG_1]", "ORG", "ООО «Ромашка»"),
                _entity("[PERSON_1]", "PERSON", "Иванов И.И."),
            ],
            storage_dir=str(tmp_path),
        )

    def test_newline_split_token_silently_lost(self, tmp_path):
        sid = self._session(tmp_path)
        text = "Компания [ORG_\n1] и её директор [PERSON_1]."
        restored, unresolved = detokenize(text, sid, storage_dir=str(tmp_path))

        # Разорванный токен НЕ попадает даже в unresolved (regex его не видит) — как в спеке.
        assert unresolved == []
        # Он остаётся в тексте как есть — тихая потеря, не искажение.
        assert "[ORG_\n1]" in restored

    def test_newline_split_does_not_corrupt_neighbours(self, tmp_path):
        sid = self._session(tmp_path)
        text = "Компания [ORG_\n1] и её директор [PERSON_1]."
        restored, _ = detokenize(text, sid, storage_dir=str(tmp_path))

        # Соседний корректный токен восстановлен точно; окружающий текст цел.
        assert "Иванов И.И." in restored
        assert restored.startswith("Компания [ORG_\n1] и её директор ")
        assert restored.endswith("Иванов И.И..")

    def test_detokenize_never_raises_on_garbled_answer(self, tmp_path):
        sid = self._session(tmp_path)
        # Смесь всех искажений сразу — функция обязана вернуть результат, не упасть.
        garbled = "[org_1] ORG_1 [ORG 1] [ORG_\n1] [ORG_1] [ORG_1]extra ]ORG_1[ [[ORG_1]]"
        restored, unresolved = detokenize(garbled, sid, storage_dir=str(tmp_path))
        assert isinstance(restored, str)
        assert isinstance(unresolved, list)
        # Хотя бы один корректный [ORG_1] восстановлен.
        assert "ООО «Ромашка»" in restored
