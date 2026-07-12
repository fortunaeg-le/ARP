"""Блок 6 — Детокенизатор.

Восстанавливает исходный текст из ответа LLM, заменяя токены вида [TYPE_N]
на original_text из сессии (блок 5, session_store.py).

Публичная функция:
  - detokenize(text, session_id, storage_dir=None) -> (восстановленный_текст, unresolved)

dataclass'ы импортируются из models.py, не переопределены (сам блок 6 их не использует
напрямую — session_store уже работает с готовым dict, но правило распространяется
на весь проект).
"""

import re

from session_store import load_session

# Формат токена, зафиксированный спецификацией: [TYPE_N], TYPE — только заглавные
# латинские буквы и подчёркивания (см. ограничение на token_prefix), N — целое число.
_TOKEN_RE = re.compile(r"\[[A-Z_]+_\d+\]")


def detokenize(text: str, session_id: str, storage_dir: str | None = None) -> tuple[str, list[str]]:
    """Заменяет токены в text на исходные значения из сессии session_id.

    SessionNotFoundError/SessionExpiredError (session_store) пробрасываются наверх,
    не перехватываются — вызывающий код должен отличать "сессия недоступна" от
    "в тексте просто нет токенов".

    Возвращает (восстановленный_текст, unresolved) — unresolved это список токенов,
    которые встретились в text, но отсутствуют в сессии; уникальные, в порядке
    первого появления в тексте.
    """
    session = load_session(session_id, storage_dir=storage_dir)

    mapping = {e["token"]: e["original_text"] for e in session["entities"]}

    unresolved: list[str] = []
    seen_unresolved: set[str] = set()

    def repl(match: re.Match) -> str:
        token = match.group(0)
        if token in mapping:
            return mapping[token]
        if token not in seen_unresolved:
            seen_unresolved.add(token)
            unresolved.append(token)
        return token

    restored = _TOKEN_RE.sub(repl, text)
    return restored, unresolved
