"""Блок 5 — Хранилище сессии (токен-маппинг).

Сохраняет список Entity с токенами (выход блока 4) на диск в зашифрованном виде
(cryptography.fernet), загружает обратно с проверкой TTL и удаляет просроченные
сессии. Файл сессии — {storage_dir}/{session_id}.enc, ключ шифрования —
{storage_dir}/key.bin (генерируется при первом сохранении).

Публичные функции:
  - save_session(entities, session_id=None, ttl_hours=24, storage_dir=None) -> session_id
  - load_session(session_id, storage_dir=None) -> dict  (формат см. спецификацию)
  - purge_expired(storage_dir=None, exclude_session_id=None) -> число удалённых просроченных .enc
  - delete_session(session_id, storage_dir=None) -> bool  (ручное удаление одной сессии)

Исключения SessionNotFoundError / SessionExpiredError определены здесь же.

dataclass'ы импортируются из models.py, не переопределены.
"""

import json
import os
import re
import sys
import tempfile
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


def default_storage_dir() -> Path:
    """Возвращает дефолтную директорию хранилища сессий (~/.shifrator/sessions)."""
    return _DEFAULT_STORAGE_DIR


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
    """Читает ключ Fernet из {storage_dir}/key.bin, создавая его при первом запуске.

    Создание атомарно (защита от TOCTOU-гонки): ключ пишется целиком во временный
    файл и публикуется под финальным именем через os.link, который эксклюзивен —
    кидает FileExistsError, если key.bin уже существует. Поэтому при параллельном
    первом сохранении ровно ОДИН создатель побеждает, а проигравшие читают его уже
    полностью записанный ключ (а не свой). Так закрыты сразу два окна: перезапись
    ключа (link может пройти лишь однажды) и чтение недописанного файла (key.bin
    появляется под финальным именем уже целиком, в отличие от create+write).

    Примечание по атомарности: на POSIX os.link атомарен и падает на существующей
    цели по спецификации. На Windows/NTFS он ложится на CreateHardLinkW с той же
    семантикой отказа при существующей цели. На ФС без жёстких ссылок или при
    временном файле на другом томе гарантия слабее — тогда возможен возврат к
    неатомарному пути (см. except OSError ниже).
    """
    key_path = storage_dir / _KEY_FILENAME
    if key_path.exists():
        return key_path.read_bytes()

    storage_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()

    fd, tmp_name = tempfile.mkstemp(
        dir=str(storage_dir), prefix=_KEY_FILENAME + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(key)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        try:
            os.link(tmp_path, key_path)
        except FileExistsError:
            # Гонку выиграл другой поток/процесс — берём его целиком записанный ключ.
            return key_path.read_bytes()
        except OSError:
            # ФС не поддерживает жёсткие ссылки: неатомарный запасной путь. Всё ещё
            # эксклюзивен через O_CREAT|O_EXCL, но с окном чтения недописанного файла.
            try:
                excl_fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return key_path.read_bytes()
            with os.fdopen(excl_fd, "wb") as excl_file:
                excl_file.write(key)
                excl_file.flush()
                os.fsync(excl_file.fileno())
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

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
    fd, tmp_path = tempfile.mkstemp(dir=store, suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            f.write(encrypted)
        _chmod_600(Path(tmp_path))
        os.replace(tmp_path, session_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

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
    try:
        fernet = Fernet(key)
    except ValueError as exc:
        # Повреждённый key.bin (обрезан, затёрт, синхронизирован наполовину):
        # Fernet() кидает ValueError ещё до decrypt. Спека блока 5 требует явного
        # SessionNotFoundError на «повреждён файл или ключ», а не падения.
        raise SessionNotFoundError(
            f"Сессия не расшифровывается (повреждён ключ): {session_id}"
        ) from exc

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


def purge_expired(storage_dir: str | None = None, exclude_session_id: str | None = None) -> int:
    """Удаляет просроченные .enc-файлы в директории хранилища.

    Перебирает только *.enc; key.bin и прочие файлы никогда не трогаются.
    Файлы, которые не расшифровываются / содержат невалидный JSON / не имеют
    expires_at, пропускаются (с предупреждением в stderr), но не удаляются.
    Возвращает число фактически удалённых просроченных файлов.

    exclude_session_id — если задан, файл {exclude_session_id}.enc в этом проходе
    НЕ удаляется (и даже не проверяется), сколько бы он ни был просрочен. Нужно
    для CLI decrypt: очистку зовём в начале, но файл запрошенной сессии оставляем,
    чтобы последующий load_session мог отличить «истекла» (SessionExpiredError) от
    «не найдена» (SessionNotFoundError). Все ОСТАЛЬНЫЕ просроченные сессии при этом
    вычищаются за тот же проход.
    """
    store = _resolve_storage_dir(storage_dir)
    if not store.exists():
        return 0

    exclude_name = f"{exclude_session_id}.enc" if exclude_session_id is not None else None

    key_path = store / _KEY_FILENAME
    if not key_path.exists():
        # Без ключа расшифровать ничего нельзя — удалять по TTL невозможно.
        return 0
    try:
        fernet = Fernet(key_path.read_bytes())
    except ValueError as exc:
        # Повреждённый key.bin: Fernet() кидает ValueError. Без валидного ключа
        # ни одну сессию не расшифровать, поэтому чистить нечего — предупреждаем и
        # выходим без падения (CLI decrypt зовёт purge_expired первым).
        print(
            f"[session_store] повреждён {_KEY_FILENAME}: {type(exc).__name__}: {exc}; "
            "просроченные сессии не вычищены",
            file=sys.stderr,
        )
        return 0

    now = datetime.now(timezone.utc)
    removed = 0

    for entry in store.iterdir():
        if not entry.is_file() or entry.suffix != ".enc":
            continue
        if exclude_name is not None and entry.name == exclude_name:
            # Файл запрошенной у decrypt сессии не трогаем в этом проходе, чтобы
            # load_session мог отличить «истекла» от «не найдена».
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
            except OSError as exc:
                print(
                    f"[session_store] не удалось удалить {entry.name}: {exc}",
                    file=sys.stderr,
                )
                continue
            removed += 1
            # B6-fix: рядом с {sid}.enc encrypt пишет {sid}.txt (анонимизированный
            # текст). Удаляем сайдкар вместе с просроченным .enc, иначе .txt копятся
            # в хранилище без границы даже после TTL-очистки. Отсутствие сайдкара —
            # штатная ситуация (не всякая сессия его имеет), не ошибка: просто пропускаем.
            txt_sidecar = entry.with_suffix(".txt")
            try:
                txt_sidecar.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(
                    f"[session_store] не удалось удалить сайдкар {txt_sidecar.name}: {exc}",
                    file=sys.stderr,
                )

    return removed


def delete_session(session_id: str, storage_dir: str | None = None) -> bool:
    """Удаляет файлы сессии {storage_dir}/{session_id}.enc и {session_id}.txt.

    Валидирует session_id тем же regex, что load_session (^[0-9a-f-]{36}$); при
    несовпадении к файловой системе НЕ обращается и возвращает False (защита от
    path traversal). key.bin никогда не трогает; штатных исключений не кидает.

    B6-fix: encrypt пишет рядом с {sid}.enc ещё и {sid}.txt (анонимизированный
    текст). Раньше delete удалял только .enc и рапортовал успех — .txt оставался
    на диске (ложный успех + утечка остаточных данных). Теперь удаляем ОБА файла.

    Возвращает True, если удалён .enc (основной файл с чувствительным маппингом;
    .txt вторичен и подчищается вместе с ним). Если .enc не найден, но остался
    осиротевший .txt — удаляем и его, пишем предупреждение в stderr, но НЕ считаем
    это успешным удалением сессии (возвращаем False): основного файла не было.
    """
    if not _SESSION_ID_RE.match(session_id):
        return False

    store = _resolve_storage_dir(storage_dir)
    enc_path = store / f"{session_id}.enc"
    txt_path = store / f"{session_id}.txt"

    enc_deleted = False
    try:
        enc_path.unlink()
        enc_deleted = True
    except FileNotFoundError:
        pass

    txt_deleted = False
    try:
        txt_path.unlink()
        txt_deleted = True
    except FileNotFoundError:
        pass

    if enc_deleted:
        return True

    if txt_deleted:
        # Нештатная ситуация: .enc нет, а .txt остался. Подчистили сайдкар, но
        # это не удаление основной сессии — сигналим и возвращаем False.
        print(
            f"[session_store] удалён осиротевший сайдкар {session_id}.txt, "
            f"но {session_id}.enc не найден — сессия не считается удалённой",
            file=sys.stderr,
        )
    return False
