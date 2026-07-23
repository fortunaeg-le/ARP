"""Блок 6 — Детокенизатор.

Восстанавливает исходный текст из ответа LLM, заменяя токены вида [TYPE_N]
на значение из сессии (блок 5, session_store.py).

ЭТАП E — ДВА РЕЖИМА ВОССТАНОВЛЕНИЯ:
  * mode="canonical" (умолчание, сценарий владельца): токен заменяется
    КАНОНИЧЕСКОЙ формой сущности (ORG — юридическая форма «ООО «Меркурий»»,
    PER — ФИО в именительном). Обезличенный текст возвращается из нейросети
    ПЕРЕПИСАННЫМ — токен мог переехать в другой падежный контекст, исходная
    поверхностная форма вхождения там неприменима; канон — единственная форма,
    валидная в любом месте. Для сущностей без канона (regex-типы, ADDRESS,
    старые сессии v1) подставляется original_text — для них канон и исходная
    форма совпадают.
  * mode="exact": позиционное точное восстановление — n-е вхождение токена в
    тексте получает поверхностную форму n-го замаскированного вхождения из
    occurrences (сессии v2). Валидно ТОЛЬКО для неизменённого anonymized-текста
    (структурная приёмка round-trip, побайтная сверка); на переписанном LLM
    тексте порядок вхождений не гарантирован. Сессия v1 (без occurrences) в
    exact-режиме отдаёт original_text (все вхождения токена в v1 имели
    идентичную поверхностную форму — токен выдавался по (тип, текст)).

Публичная функция:
  - detokenize(text, session_id, storage_dir=None, mode="canonical")
      -> (восстановленный_текст, unresolved)
"""

import re

from session_store import load_session

# Формат токена, зафиксированный спецификацией: [TYPE_N], TYPE — только заглавные
# латинские буквы и подчёркивания (см. ограничение на token_prefix), N — целое число.
_TOKEN_RE = re.compile(r"\[[A-Z_]+_\d+\]")


def detokenize(
    text: str,
    session_id: str,
    storage_dir: str | None = None,
    mode: str = "canonical",
) -> tuple[str, list[str]]:
    """Заменяет токены в text на значения из сессии session_id (режимы — см.
    docstring модуля).

    SessionNotFoundError/SessionExpiredError (session_store) пробрасываются наверх,
    не перехватываются — вызывающий код должен отличать "сессия недоступна" от
    "в тексте просто нет токенов".

    Возвращает (восстановленный_текст, unresolved) — unresolved это список токенов,
    которые встретились в text, но отсутствуют в сессии; уникальные, в порядке
    первого появления в тексте.
    """
    if mode not in ("canonical", "exact"):
        raise ValueError(f"Неизвестный режим детокенизации: {mode!r}")

    session = load_session(session_id, storage_dir=storage_dir)

    # canonical: канон сущности, fallback original_text (v1-сессии/типы без канона).
    mapping = {
        e["token"]: (e.get("canonical") or e["original_text"])
        for e in session["entities"]
    }
    occurrences = None
    if mode == "exact":
        occurrences = {
            e["token"]: [o["surface"] for o in (e.get("occurrences") or [])]
            for e in session["entities"]
        }
        fallback = {e["token"]: e["original_text"] for e in session["entities"]}

    unresolved: list[str] = []
    seen_unresolved: set[str] = set()
    occ_counter: dict[str, int] = {}

    def repl(match: re.Match) -> str:
        token = match.group(0)
        if token not in mapping:
            if token not in seen_unresolved:
                seen_unresolved.add(token)
                unresolved.append(token)
            return token
        if occurrences is None:
            return mapping[token]
        # exact: n-е вхождение токена -> surface n-го замаскированного вхождения
        n = occ_counter.get(token, 0)
        occ_counter[token] = n + 1
        occ = occurrences.get(token) or []
        if n < len(occ):
            return occ[n]
        return fallback[token]

    restored = _TOKEN_RE.sub(repl, text)
    return restored, unresolved
