"""Тонкий интерфейс хранения сессий (этап U1, задел под серверную версию).

Сейчас SHIFRATOR однопользовательский, локальный: сессии живут файлами в
профиле пользователя. Позже появится серверная многопользовательская версия —
тогда меняется РЕАЛИЗАЦИЯ этого модуля (например, поход в БД вместо файлов),
а не вызывающий код. CLI (`shifrator.py`) и десктоп-интерфейс (`app/core.py`,
`app/server.py`) обязаны ходить в хранилище ТОЛЬКО через функции этого модуля
и не импортировать `session_store` напрямую — тогда замена реализации не
требует правки ни одного из них.

Библиотечные функции восстановления (`detokenizer.py`, `file_detokenizer.py`)
сюда не переведены: они уже принимают `storage_dir` инъекцией параметра и не
трогают файлы напрямую (вызывают `session_store.load_session`) — это тот же
принцип "не знает про файлы", применённый на уровень ниже; у них десятки
тестов, завязанных на сигнатуру `storage_dir=`, трогать вне области этой сессии
(см. HANDOFF_U1_PACKAGING.md).

Публичные функции:
  - save_session(entities, session_id=None, ttl_hours=24) -> session_id
  - load_session(session_id) -> dict
  - list_sessions() -> list[dict]
  - delete_session(session_id) -> bool
  - purge_expired(exclude_session_id=None) -> int
  - default_storage_dir() -> Path
  - save_markup(session_id, markup) -> задел следующего этапа (разметка/аннотации
    UI), вызывающего кода ещё нет — намеренно поднимает NotImplementedError, а
    не молчит, чтобы будущий вызов не притворился успешным.

Исключения SessionNotFoundError / SessionExpiredError реэкспортированы отсюда же.
"""

from session_store import (
    SessionExpiredError,
    SessionNotFoundError,
    default_storage_dir,
)
from session_store import delete_session as _delete_session
from session_store import list_sessions as _list_sessions
from session_store import load_session as _load_session
from session_store import purge_expired as _purge_expired
from session_store import save_session as _save_session

__all__ = [
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "purge_expired",
    "default_storage_dir",
    "save_markup",
    "SessionNotFoundError",
    "SessionExpiredError",
]


def save_session(entities, session_id=None, ttl_hours=24):
    return _save_session(entities, session_id=session_id, ttl_hours=ttl_hours)


def load_session(session_id):
    return _load_session(session_id)


def list_sessions():
    return _list_sessions()


def delete_session(session_id):
    return _delete_session(session_id)


def purge_expired(exclude_session_id=None):
    return _purge_expired(exclude_session_id=exclude_session_id)


def save_markup(session_id, markup):
    """Сохранение разметки/аннотаций к сессии — задел, не реализовано в U1.

    Явно поднимает NotImplementedError вместо тихого no-op: вызывающий код,
    который появится позже, не должен получить ложное "сохранено"."""
    raise NotImplementedError(
        "storage.save_markup — задел следующего этапа (разметка), "
        "вызывающего кода в U1 ещё нет"
    )
