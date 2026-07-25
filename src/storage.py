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
  - list_sessions() -> list[dict] (обогащён source_name/expired — см. ниже)
  - delete_session(session_id) -> bool
  - purge_expired(exclude_session_id=None) -> int
  - default_storage_dir() -> Path
  - save_session_meta(session_id, source_name) -> None — имя исходного документа
    для списка сессий (Задача U2-3, "какой документ"); sidecar {sid}.meta.json,
    НЕ входит в зашифрованный payload session_store — тот формат вне границ этой
    сессии (только storage.py).
  - replace_session_entities(session_id, entities, session_expires_at) -> None —
    этап U3: перезаписывает сессию НОВЫМ списком Entity (после ручной правки
    разметки), сохраняя ИСХОДНЫЙ срок действия (не продлевает TTL при каждой
    правке).
  - save_doc_segments(session_id, doc) / load_doc_segments(session_id) -> этап
    U3: sidecar {sid}.doc.json с сегментами исходного документа (id/text/
    source_type/metadata) — нужен, чтобы пересобрать анонимизированный текст
    после ручной правки без повторного обращения к исходному файлу (тот уже
    удалён после обработки запроса). Содержит ПОЛНЫЙ исходный текст — ПДн,
    хранится в том же профиле и с той же осторожностью, что сессия.
  - save_markup(session_id, entry) -> markup_id — этап U3: сохраняет ОДНУ запись
    ручной разметки (см. модуль docstring app/core.py) в sidecar
    {sid}.markup.json; список копится, не перезаписывается.
  - list_markup(session_id) -> list[dict]
  - update_markup(session_id, markup_id, **patch) -> bool
  - delete_markup(session_id, markup_id) -> bool

Исключения SessionNotFoundError / SessionExpiredError реэкспортированы отсюда же.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

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
    "save_session_meta",
    "replace_session_entities",
    "save_doc_segments",
    "load_doc_segments",
    "save_markup",
    "list_markup",
    "update_markup",
    "delete_markup",
    "SessionNotFoundError",
    "SessionExpiredError",
]


def save_session(entities, session_id=None, ttl_hours=24):
    return _save_session(entities, session_id=session_id, ttl_hours=ttl_hours)


def load_session(session_id):
    return _load_session(session_id)


def _meta_path(store: Path, session_id: str) -> Path:
    return store / f"{session_id}.meta.json"


def save_session_meta(session_id: str, source_name: str) -> None:
    """Пишет sidecar {session_id}.meta.json с именем исходного документа.

    Некритичный побочный файл: сбой записи (нет прав, диск полон) НЕ должен
    ронять шифрацию — сессия уже сохранена session_store к моменту вызова,
    список сессий просто не покажет имя документа для этой записи.
    """
    store = default_storage_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
        _meta_path(store, session_id).write_text(
            json.dumps({"source_name": source_name}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _load_session_meta(store: Path, session_id: str) -> dict:
    path = _meta_path(store, session_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_sessions():
    """[{session_id, created_at, expires_at, entities_count, source_name, expired}].

    Обогащает session_store.list_sessions() двумя полями для экрана "Мои сессии"
    (Задача U2-3): source_name (из sidecar, None если нет — старые сессии/CLI без
    имени) и expired (expires_at уже в прошлом; session_store сам просроченные
    сессии из списка не убирает, фильтрацию/маркировку делает вызывающий).
    """
    store = default_storage_dir()
    now = datetime.now().astimezone()
    out = []
    for rec in _list_sessions():
        meta = _load_session_meta(store, rec["session_id"])
        try:
            expired = datetime.fromisoformat(rec["expires_at"]) < now
        except ValueError:
            expired = False
        out.append({**rec, "source_name": meta.get("source_name"), "expired": expired})
    return out


def delete_session(session_id):
    """Удаляет сессию (session_store: .enc/.txt) + все sidecar-файлы этапов
    U2/U3 (.meta.json/.doc.json/.markup.json), если есть."""
    deleted = _delete_session(session_id)
    store = default_storage_dir()
    for suffix in (".meta.json", ".doc.json", ".markup.json"):
        try:
            store.joinpath(f"{session_id}{suffix}").unlink(missing_ok=True)
        except OSError:
            pass
    return deleted


def purge_expired(exclude_session_id=None):
    """Удаляет просроченные .enc (session_store) и подчищает ОСИРОТЕВШИЕ
    sidecar-файлы (.meta/.doc/.markup.json), для которых .enc уже удалён —
    session_store ничего не знает про sidecar-и этапов U2/U3, поэтому без этого
    прохода они копились бы в хранилище бессрочно после истечения TTL сессии."""
    removed = _purge_expired(exclude_session_id=exclude_session_id)
    store = default_storage_dir()
    if store.exists():
        live_ids = {p.stem for p in store.glob("*.enc")}
        for suffix in (".meta.json", ".doc.json", ".markup.json"):
            for p in store.glob(f"*{suffix}"):
                sid = p.name[: -len(suffix)]
                if sid not in live_ids:
                    try:
                        p.unlink()
                    except OSError:
                        pass
    return removed


def replace_session_entities(session_id, entities, session_expires_at):
    """Перезаписывает сессию новым списком Entity, сохраняя ИСХОДНЫЙ expires_at.

    Этап U3: после ручной правки разметки (пометка пропущенного/снятие ложной
    маски/исправление границы или типа) сессия должна содержать ОБНОВЛЁННЫЙ
    список сущностей — иначе восстановление не знает про ручные маски. Без
    сохранения исходного expires_at каждая правка продлевала бы TTL сессии
    заново (косвенное бессрочное хранение ПДн — запрещённое инвариантом
    24-часового срока).
    """
    remaining_hours = (session_expires_at - datetime.now().astimezone()).total_seconds() / 3600
    remaining_hours = max(remaining_hours, 1 / 3600)  # минимум 1 секунда — не даём отрицательный TTL
    _save_session(entities, session_id=session_id, ttl_hours=remaining_hours)


def _doc_path(store: Path, session_id: str) -> Path:
    return store / f"{session_id}.doc.json"


def save_doc_segments(session_id: str, doc) -> None:
    """Sidecar {session_id}.doc.json — сегменты исходного документа (текст +
    структура), нужны для пересборки анонимизированного текста после ручной
    правки разметки (U3). СОДЕРЖИТ ПОЛНЫЙ ИСХОДНЫЙ ТЕКСТ (ПДн) — тот же профиль
    пользователя, та же осторожность, что у файла сессии; никуда не отправляется,
    не входит в git (```~/.shifrator``` вне репозитория)."""
    store = default_storage_dir()
    store.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_format": doc.source_format,
        "segments": [
            {"id": s.id, "text": s.text, "source_type": s.source_type, "metadata": s.metadata}
            for s in doc.segments
        ],
    }
    _doc_path(store, session_id).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load_doc_segments(session_id: str):
    """Читает {session_id}.doc.json и возвращает models.SourceDocument.

    Кидает FileNotFoundError, если сессия не создавалась через этот этап
    (старая сессия/CLI) — вызывающий код (app/core.py) переводит это в
    человеческий текст: правка разметки для такой сессии недоступна."""
    from models import SourceDocument, TextSegment

    path = _doc_path(default_storage_dir(), session_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Для сессии {session_id} не сохранена структура документа "
            "(создана до этапа разметки или напрямую через CLI) — правка недоступна."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = [
        TextSegment(id=s["id"], text=s["text"], source_type=s["source_type"], metadata=s["metadata"])
        for s in data["segments"]
    ]
    return SourceDocument(segments=segments, source_format=data["source_format"], source_path="")


def _markup_path(store: Path, session_id: str) -> Path:
    return store / f"{session_id}.markup.json"


def _load_markup_list(store: Path, session_id: str) -> list[dict]:
    path = _markup_path(store, session_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_markup_list(store: Path, session_id: str, entries: list[dict]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    _markup_path(store, session_id).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_markup(session_id: str, entry: dict) -> str:
    """Сохраняет ОДНУ запись ручной разметки (см. app/core.py — форма записи:
    kind/entity_type/segment_id/start/end/value/created_at/build_mark/applied).
    Список копится в {session_id}.markup.json — тот же профиль, что сессия
    (содержит фрагмент РЕАЛЬНОГО текста — ПДн). Возвращает id новой записи.
    """
    store = default_storage_dir()
    entries = _load_markup_list(store, session_id)
    markup_id = str(uuid.uuid4())
    entries.append({**entry, "id": markup_id})
    _write_markup_list(store, session_id, entries)
    return markup_id


def list_markup(session_id: str) -> list[dict]:
    return _load_markup_list(default_storage_dir(), session_id)


def update_markup(session_id: str, markup_id: str, **patch) -> bool:
    """Точечно обновляет поля записи разметки (напр. entity_type при
    пере-выборе типа ДО применения). Возвращает False, если запись не найдена."""
    store = default_storage_dir()
    entries = _load_markup_list(store, session_id)
    for e in entries:
        if e["id"] == markup_id:
            e.update(patch)
            _write_markup_list(store, session_id, entries)
            return True
    return False


def delete_markup(session_id: str, markup_id: str) -> bool:
    store = default_storage_dir()
    entries = _load_markup_list(store, session_id)
    new_entries = [e for e in entries if e["id"] != markup_id]
    if len(new_entries) == len(entries):
        return False
    _write_markup_list(store, session_id, new_entries)
    return True
