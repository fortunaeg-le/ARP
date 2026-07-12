"""Блок 5 — Хранилище сессии (токен-маппинг).

Сохраняет список Entity с токенами (выход блока 4) на диск в зашифрованном виде
(cryptography.fernet), загружает обратно с проверкой TTL и удаляет просроченные
сессии. Файл сессии — {storage_dir}/{session_id}.enc, ключ шифрования —
{storage_dir}/key.bin (генерируется при первом сохранении).

Публичные функции:
  - save_session(entities, session_id=None, ttl_hours=24, storage_dir=None) -> session_id
  - load_session(session_id, storage_dir=None) -> dict  (формат см. спецификацию)
  - purge_expired(storage_dir=None) -> число удалённых просроченных .enc-файлов

Исключения SessionNotFoundError / SessionExpiredError определены здесь же.

dataclass'ы импортируются из models.py, не переопределены.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from models import Entity

# Регулярка формата uuid4 (как строка session_id): 32 hex-цифры + 4 дефиса = 36 символов.
_SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")

# Дефолтная директория хранилища (общая для всех трёх функций и блока 7).
_DEFAULT_STORAGE_DIR = Path.home() / ".shifrator" / "sessions"

_KEY_FILENAME = "key.bin"


class SessionNotFoundError(Exception):
    """Файл сессии не найден или session_id имеет неверный формат."""


class SessionExpiredError(Exception):
    """Срок жизни сессии (expires_at) истёк."""


def _resolve_storage_dir(storage_dir: str | None) -> Path:
    """Возвращает Path к директории хранилища (дефолт — ~/.shifrator/sessions)."""
    if storage_dir is None:
        return _DEFAULT_STORAGE_DIR
    return Path(storage_dir)


def _chmod_600(path: Path) -> None:
    """Выставляет права 0o600 на POSIX; на Windows тихо ничего не делает."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _load_or_create_key(storage_dir: Path) -> bytes:
    """Читает ключ Fernet из {storage_dir}/key.bin, создавая его при первом запуске."""
    key_path = storage_dir / _KEY_FILENAME
    if key_path.exists():
        return key_path.read_bytes()
    storage_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    _chmod_600(key_path)
    return key


def save_session(
    entities: list[Entity],
    session_id: str | None = None,
    ttl_hours: int = 24,
    storage_dir: str | None = None,
) -> str:
    """Сохраняет список Entity с токенами в зашифрованный файл сессии.

    Возвращает session_id (сгенерированный uuid4, если на вход пришёл None).
    Дедупликация записей по token; при повторе токена сохраняется segment_id
    первого по порядку вхождения. Пустой список entities — валиден.
    """
    if session_id is None:
        import uuid

        session_id = str(uuid.uuid4())
    elif not _SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Неверный формат session_id: {session_id!r}; ожидается uuid4 (^[0-9a-f-]{{36}}$)"
        )

    store = _resolve_storage_dir(storage_dir)
    store.mkdir(parents=True, exist_ok=True)

    key = _load_or_create_key(store)
    fernet = Fernet(key)

    # Дедупликация по token, сохраняя порядок первого появления.
    seen_tokens: set[str] = set()
    entity_records = []
    for e in entities:
        if e.token in seen_tokens:
            continue
        seen_tokens.add(e.token)
        entity_records.append(
            {
                "token": e.token,
                "entity_type": e.entity_type,
                "original_text": e.original_text,
                "segment_id": e.segment_id,
            }
        )

    created_at = datetime.now().astimezone()
    expires_at = created_at + timedelta(hours=ttl_hours)

    payload = {
        "session_id": session_id,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "entities": entity_records,
    }

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    encrypted = fernet.encrypt(raw)

    session_path = store / f"{session_id}.enc"
    session_path.write_bytes(encrypted)
    _chmod_600(session_path)

    return session_id


def load_session(session_id: str, storage_dir: str | None = None) -> dict:
    """Загружает и расшифровывает сессию по session_id.

    Кидает SessionNotFoundError при неверном формате id / отсутствии файла,
    SessionExpiredError — если expires_at уже прошёл.
    """
    if not _SESSION_ID_RE.match(session_id):
        # Защита от path traversal: к файловой системе не обращаемся.
        raise SessionNotFoundError(f"Сессия не найдена: {session_id!r}")

    store = _resolve_storage_dir(storage_dir)
    session_path = store / f"{session_id}.enc"
    if not session_path.exists():
        raise SessionNotFoundError(f"Сессия не найдена: {session_id}")

    key = _load_or_create_key(store)
    fernet = Fernet(key)

    encrypted = session_path.read_bytes()
    try:
        raw = fernet.decrypt(encrypted)
    except InvalidToken as exc:
        raise SessionNotFoundError(
            f"Сессия не расшифровывается (повреждён файл или ключ): {session_id}"
        ) from exc

    data = json.loads(raw.decode("utf-8"))

    expires_at = datetime.fromisoformat(data["expires_at"])
    if datetime.now(timezone.utc) >= expires_at:
        raise SessionExpiredError(f"Срок сессии истёк: {session_id} (expires_at={data['expires_at']})")

    return data


def purge_expired(storage_dir: str | None = None) -> int:
    """Удаляет просроченные .enc-файлы в директории хранилища.

    Перебирает только *.enc; key.bin и прочие файлы никогда не трогаются.
    Файлы, которые не расшифровываются / содержат невалидный JSON / не имеют
    expires_at, пропускаются (с предупреждением в stderr), но не удаляются.
    Возвращает число фактически удалённых просроченных файлов.
    """
    store = _resolve_storage_dir(storage_dir)
    if not store.exists():
        return 0

    key_path = store / _KEY_FILENAME
    if not key_path.exists():
        # Без ключа расшифровать ничего нельзя — удалять по TTL невозможно.
        return 0
    fernet = Fernet(key_path.read_bytes())

    now = datetime.now(timezone.utc)
    removed = 0

    for entry in store.iterdir():
        if not entry.is_file() or entry.suffix != ".enc":
            continue
        try:
            raw = fernet.decrypt(entry.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (InvalidToken, json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            print(
                f"[session_store] пропущен файл {entry.name}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue

        if now >= expires_at:
            try:
                entry.unlink()
                removed += 1
            except OSError as exc:
                print(
                    f"[session_store] не удалось удалить {entry.name}: {exc}",
                    file=sys.stderr,
                )

    return removed
