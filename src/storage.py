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
  - save_session_meta(session_id, source_name, policy=None) -> None — имя исходного документа
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

ЭТАП S1 (гигиена данных и жизненный цикл разметки, 2026-07-25). Разметка (U3) —
ДОЛГОЖИВУЩИЙ АКТИВ (эталонный корпус для будущего анализатора/замеров), а не
часть сессии восстановления: живёт в ОТДЕЛЬНОМ хранилище (`default_markup_dir()`,
не `default_storage_dir()`), не удаляется вместе с сессией и не подчищается
`purge_expired` по TTL сессии (24 ч). Своя, более долгая политика срока —
`_MARKUP_TTL_DAYS`, обоснование см. там же. Разметка по-прежнему содержит
фрагмент РЕАЛЬНОГО текста (`value`) — это ПДн, поэтому срок не бесконечен и
пользователю должно быть явно видно, что она хранится (интерфейс — вне
`storage.py`, см. `app/index.html`/`app/server.py`).

  - default_markup_dir() -> Path — отдельная директория разметки, сосед
    `default_storage_dir()` (не внутри неё — чтобы сканы `*.enc`/сайдкаров
    сессии её не задевали).
  - purge_expired_markup(ttl_days=_MARKUP_TTL_DAYS) -> int — удаляет ЗАПИСИ
    разметки старше ttl_days (по created_at КАЖДОЙ записи, не по сессии);
    возвращает число удалённых записей. Не связана с purge_expired сессий.
  - delete_session_markup(session_id) -> bool — удаляет НАКОПЛЕННУЮ разметку
    ОДНОЙ сессии отдельным действием (сессию не трогает).
  - delete_all_markup() -> int — удаляет ВСЮ накопленную разметку (все сессии),
    одно явное действие пользователя («удалить всю накопленную разметку»).
  - markup_summary() -> dict — {sessions, entries, oldest_created_at, ttl_days}
    для интерфейса (сколько накоплено, когда истечёт).
  - migrate_legacy_markup() -> int — переносит {sid}.markup.json, сохранённые
    ДО этапа S1 в старом расположении (директория сессий), в новое; сливает по
    id, если файл уже есть в обоих местах. Идемпотентна. Вызывается один раз
    при старте UI (`app/server.py::main`), НЕ на каждый запрос.

`delete_session(session_id, delete_markup=False)` — по умолчанию разметку
сессии больше НЕ удаляет (только .meta.json/.doc.json — сайдкары сессии, не
разметку); `delete_markup=True` — явный дополнительный выбор пользователя,
удаляет и разметку этой сессии через delete_session_markup().

Исключения SessionNotFoundError / SessionExpiredError реэкспортированы отсюда же.
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import vault
from cryptography.fernet import InvalidToken
from session_store import (
    SessionExpiredError,
    SessionNotFoundError,
    default_storage_dir,
)
from session_store import storage_key as _session_storage_key
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
    "default_markup_dir",
    "save_session_meta",
    "load_session_policy",
    "save_anon_text",
    "load_anon_text",
    "save_unread_zones",
    "load_unread_zones",
    "replace_session_entities",
    "save_doc_segments",
    "load_doc_segments",
    "save_markup",
    "list_markup",
    "update_markup",
    "delete_markup",
    "purge_expired_markup",
    "purge_all",
    "retention_settings",
    "set_retention",
    "delete_session_markup",
    "delete_all_markup",
    "markup_summary",
    "migrate_legacy_markup",
    "SessionNotFoundError",
    "SessionExpiredError",
]

# Разметка (U3) — отдельный от сессий актив, см. докстринг модуля §ЭТАП S1.
# Своя политика срока: 24ч (TTL сессии) хватает на один цикл проверки, но не на
# накопление эталонного корпуса для будущего анализатора — это недели пилота.
# 30 дней — компромисс: достаточно долго, чтобы разметка не терялась между
# рабочими сессиями пользователя, но не «хранить вечно молча» (ПДн-фрагмент в
# `value` — открытый текст, инвариант «срок не бесконечен» соблюдён). Меняется
# централизованно здесь же.
_MARKUP_TTL_DAYS = 30
_MARKUP_DIRNAME = "markup"

# --------------------------------------------------------------------------- #
# ЭТАП STORE, часть 3 — сроки хранения и автоочистка
# --------------------------------------------------------------------------- #
# Почему очистка до сих пор не вызывалась — установлено по документу этапа S1,
# а не по догадке: `docs/archive/reports/HANDOFF_S1.md` §7 «STOP / не
# запланировано (доложено, не сделано)» прямо говорит, что автоматический
# периодический вызов `purge_expired_markup()` НЕ добавлен, потому что таймер в
# ThreadingHTTPServer — отдельное архитектурное решение (граница доверия
# HTTP-слоя), вне границ S1. То есть НЕ ЗАБЫЛИ — сознательно отложили и
# записали это. Правку истории git подтвердить нельзя: весь этап S1 лежит в
# одном чужом коммите 8422395 «Этапы смерджены и немного оптимизированы».
#
# Отсюда и здешнее решение: фонового потока по-прежнему НЕТ. Очистка зовётся на
# естественных событиях — старт интерфейса и завершение операции с хранилищем
# (`purge_all`). Это не откладывает удаление надолго: продукт запускают, чтобы
# им пользоваться, а не работающая программа файлов не создаёт.
#
# Сроки — решение владельца: 7 дней сессии / 30 дней разметка, И ОБА
# НАСТРАИВАЕМЫЕ. Политику хранения за юриста не угадываем: умолчание — не
# приговор, оно меняется в интерфейсе.
_DEFAULT_SESSION_TTL_DAYS = 7
_DEFAULT_MARKUP_TTL_DAYS = _MARKUP_TTL_DAYS
_RETENTION_FILENAME = "retention.json"

# Границы вменяемости для введённого пользователем срока. Ноль/отрицательное
# означало бы «удалять всё немедленно, в том числе то, с чем сейчас работают»;
# верхняя граница — чтобы «хранить вечно» нельзя было получить опечаткой.
_TTL_MIN_DAYS = 1
_TTL_MAX_DAYS = 365


def _retention_path() -> Path:
    return default_storage_dir().parent / _RETENTION_FILENAME


def retention_settings() -> dict:
    """{"session_days": int, "markup_days": int} — сроки хранения.

    Читается ПРИ КАЖДОМ вызове (не кешируется): пользователь меняет срок в
    интерфейсе, и следующая же очистка обязана считать по новому значению, без
    перезапуска. Файл ПДн не содержит и потому не шифруется — это настройка,
    а не данные; шифровать её значило бы сделать сессии нечитаемыми ради числа,
    которое ничего не выдаёт.
    """
    out = {"session_days": _DEFAULT_SESSION_TTL_DAYS,
           "markup_days": _DEFAULT_MARKUP_TTL_DAYS}
    path = _retention_path()
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out          # битый файл — умолчание, а не падение экрана
    for k in out:
        v = data.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and _TTL_MIN_DAYS <= v <= _TTL_MAX_DAYS:
            out[k] = v
    return out


def set_retention(session_days: int | None = None, markup_days: int | None = None) -> dict:
    """Меняет срок хранения. Возвращает действующие значения ПОСЛЕ правки.

    Значение вне [1, 365] — ValueError с человеческим текстом: молча подставить
    границу нельзя, пользователь должен увидеть, что его число не принято.
    """
    current = retention_settings()
    for name, value in (("session_days", session_days), ("markup_days", markup_days)):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"Срок хранения задаётся целым числом дней, получено: {value!r}")
        if not (_TTL_MIN_DAYS <= value <= _TTL_MAX_DAYS):
            raise ValueError(
                f"Срок хранения должен быть от {_TTL_MIN_DAYS} до {_TTL_MAX_DAYS} дней, "
                f"получено: {value}"
            )
        current[name] = value
    path = _retention_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    return current


# --------------------------------------------------------------------------- #
# ЭТАП STORE: сайдкары шифруются тем же ключом, что и {sid}.enc
# --------------------------------------------------------------------------- #
# До этого этапа шифровался ровно ОДИН файл сессии, а рядом с ним открытым
# текстом лежали пять: полный исходный текст документа (.doc.json), сырой текст
# непрочитанных зон (.unread.json), не до конца обезличенный результат (.txt),
# имя исходного файла (.meta.json) и фрагменты реального текста в разметке
# (.markup.json). Решение владельца — шифровать все шесть; обоснование по
# каждому см. в докстринге `src/vault.py`.
#
# Ключ берётся из ТОЙ ЖЕ директории сессий, куда пишет этот модуль (а не из
# session_store-умолчания): иначе подмена хранилища в одном месте и не в другом
# давала бы шифрование не тем ключом, что молча хоронит сайдкары.

def _key() -> bytes:
    return _session_storage_key(str(default_storage_dir()))


def _write_encrypted_json(path: Path, payload) -> None:
    vault.write_encrypted(path, _key(), json.dumps(payload, ensure_ascii=False))


def _read_encrypted_json(path: Path):
    """Читает JSON-сайдкар, расшифровывая при необходимости.

    Файл БЕЗ оболочки — записанный до этого этапа открытым текстом: читается как
    есть (инвариант «сессия старой версии открывается»). Перешифровкой на месте
    здесь НЕ занимаемся: чтение бывает на путях, где запись недопустима
    (список сессий, свод по разметке), а тихая правка файла на чтении — источник
    сюрпризов. Открытые остатки уносит очистка по сроку (часть 3).
    """
    return json.loads(vault.read_maybe_encrypted(path, _key()).decode("utf-8"))


def save_anon_text(session_id: str, anon_text: str) -> None:
    """Сайдкар {sid}.txt — обезличенный текст, который пользователь копирует в
    LLM. Шифруется: обезличен он НЕ полностью (детекция часть значений
    пропускает — измеренный гейтом факт), значит это файл с остаточными ПДн.
    Наружу текст отдаёт интерфейс из памяти, а не этот файл."""
    store = default_storage_dir()
    store.mkdir(parents=True, exist_ok=True)
    vault.write_encrypted(store / f"{session_id}.txt", _key(), anon_text)


def load_anon_text(session_id: str) -> str:
    return vault.read_maybe_encrypted(
        default_storage_dir() / f"{session_id}.txt", _key()
    ).decode("utf-8")


def save_unread_zones(session_id: str, payload: dict) -> None:
    """Сайдкар {sid}.unread.json — непрочитанные зоны документа С СЫРЫМ текстом.
    Сырой он намеренно (пользователь должен видеть, что выброшено), значит это
    чистые ПДн — шифруется."""
    store = default_storage_dir()
    store.mkdir(parents=True, exist_ok=True)
    _write_encrypted_json(store / f"{session_id}.unread.json", payload)


def load_unread_zones(session_id: str) -> dict | None:
    path = default_storage_dir() / f"{session_id}.unread.json"
    if not path.exists():
        return None
    try:
        return _read_encrypted_json(path)
    except (OSError, ValueError):
        return None


def default_markup_dir() -> Path:
    """Отдельная директория разметки — СОСЕД default_storage_dir(), не внутри
    неё (см. докстринг модуля §ЭТАП S1: разные жизненные циклы)."""
    return default_storage_dir().parent / _MARKUP_DIRNAME


def save_session(entities, session_id=None, ttl_hours=None):
    """ЭТАП STORE: срок жизни сессии берётся из настройки пользователя
    (`retention_settings()["session_days"]`, умолчание 7 дней), а не из
    зашитых 24 часов. Явный ttl_hours (тесты, служебные вызовы) по-прежнему
    сильнее настройки."""
    if ttl_hours is None:
        ttl_hours = retention_settings()["session_days"] * 24
    return _save_session(entities, session_id=session_id, ttl_hours=ttl_hours)


def load_session(session_id):
    return _load_session(session_id)


def _meta_path(store: Path, session_id: str) -> Path:
    return store / f"{session_id}.meta.json"


def save_session_meta(session_id: str, source_name: str, policy: dict | None = None) -> None:
    """Пишет sidecar {session_id}.meta.json с именем исходного документа.

    `policy` (ЭТАП T1) — слепок состава включённых типов, которым документ
    маскировался: {"profile": ..., "enabled_types": [...], "disabled_types": [...]}.
    Хранится ЗДЕСЬ, а не в сессии, потому что формат {sid}.enc — контракт блока 5
    (та же причина, по которой lossy-пометка живёт отдельным sidecar-файлом), и
    расширять его ради пометки нельзя. Настройка может измениться между
    шифрацией и отчётом, поэтому отчёт обязан читать слепок момента шифрации, а
    не текущие настройки.

    Некритичный побочный файл: сбой записи (нет прав, диск полон) НЕ должен
    ронять шифрацию — сессия уже сохранена session_store к моменту вызова,
    список сессий просто не покажет имя документа для этой записи.
    """
    store = default_storage_dir()
    payload = {"source_name": source_name}
    if policy is not None:
        payload["policy"] = policy
    try:
        store.mkdir(parents=True, exist_ok=True)
        _write_encrypted_json(_meta_path(store, session_id), payload)
    except OSError:
        pass


def load_session_policy(session_id: str) -> dict | None:
    """Слепок состава включённых типов из sidecar сессии (ЭТАП T1) или None —
    сессия удалена/старая/без слепка. Отчёт обязан отличать «состав неизвестен»
    от «состав полный», поэтому None здесь содержателен."""
    policy = _load_session_meta(default_storage_dir(), session_id).get("policy")
    return policy if isinstance(policy, dict) else None


def _load_session_meta(store: Path, session_id: str) -> dict:
    path = _meta_path(store, session_id)
    if not path.exists():
        return {}
    try:
        return _read_encrypted_json(path)
    except (OSError, ValueError, InvalidToken, vault.KeyUnavailableError):
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


def delete_session(session_id, delete_markup: bool = False):
    """Удаляет сессию (session_store: .enc/.txt) + sidecar-файлы этапа U2/U3
    краткоживущего цикла (.meta.json/.doc.json), если есть.

    Разметка (.markup.json) — ДОЛГОЖИВУЩИЙ актив в ОТДЕЛЬНОМ хранилище
    (см. §ЭТАП S1 докстринга модуля) и по умолчанию НЕ трогается: она нужна
    ПОСЛЕ удаления сессии для накопления эталонного корпуса. `delete_markup=True`
    — явный дополнительный выбор пользователя («удалить и разметку тоже»),
    удаляет разметку именно ЭТОЙ сессии через delete_session_markup().
    """
    deleted = _delete_session(session_id)
    store = default_storage_dir()
    # ЭТАП STORE: в список добавлен .unread.json — до этого этапа он не
    # удалялся НИ ОДНИМ путём (ни удалением сессии, ни очисткой по сроку) и
    # копился в профиле бессрочно, храня сырой текст непрочитанных зон.
    for suffix in (".meta.json", ".doc.json", ".unread.json"):
        try:
            store.joinpath(f"{session_id}{suffix}").unlink(missing_ok=True)
        except OSError:
            pass
    if delete_markup:
        delete_session_markup(session_id)
    return deleted


def purge_expired(exclude_session_id=None):
    """Удаляет просроченные .enc (session_store) и подчищает ОСИРОТЕВШИЕ
    sidecar-файлы краткоживущего цикла (.meta/.doc.json), для которых .enc уже
    удалён — session_store ничего не знает про sidecar-и этапа U2/U3, поэтому
    без этого прохода они копились бы в хранилище бессрочно после истечения
    TTL сессии.

    Разметка (.markup.json) сюда НЕ входит — с этапа S1 она живёт в отдельном
    хранилище (`default_markup_dir()`) с собственной, более долгой политикой
    срока (`purge_expired_markup`), не привязанной к TTL сессии."""
    removed = _purge_expired(exclude_session_id=exclude_session_id)
    store = default_storage_dir()
    if store.exists():
        live_ids = {p.stem for p in store.glob("*.enc")}
        for suffix in (".meta.json", ".doc.json", ".unread.json"):  # STORE: +unread
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


# ЭТАП S1 (задача 2): ключи segment.metadata, которые detect-время кладёт как
# СЛУЖЕБНЫЕ КЕШИ производных видов текста для детекторов (`_norm_cache` —
# normalizer.detection_view, `_anchor_search_cache`/`_per_search_cache` —
# anchor_registry.anchor_search_view/per_search_view, `detection_text` —
# регистро-нормализованная копия extractor'а) — каждый ЕЩЁ ОДНА полная копия
# текста сегмента ОТКРЫТЫМ ТЕКСТОМ (+карта индексов у _norm_cache). Ни один из
# них НЕ читается после run_detection/tokenize: экран проверки и пересборка
# (app/core.py::_render_state/_validate_missed/_add_missed, tokenizer._assemble/
# _render_segment) работают ТОЛЬКО с s.text/s.source_type/s.metadata структурными
# полями (paragraph_index/style/table_index/row_index/col_index) — проверено
# grep'ом по app/ и tokenizer.py (см. HANDOFF_S1). Значит это чистый сайдэффект
# детекции, раздувающий {sid}.doc.json на диске пользователя без пользы —
# не сериализуем. Восстановление НЕ пострадает: сама детекция здесь не
# перезапускается (сегменты читаются для рендера/добавления РУЧНЫХ масок, не для
# повторного прогона детекторов), а если это когда-нибудь изменится — тест
# `tests/test_storage_s1.py::test_doc_segments_roundtrip_survives_cache_strip`
# упадёт первым (round-trip реального экрана проверки, не unit на strip-функции).
#
# ЭТАП STORE: в список добавлен `_addr_view_cache` (кладёт `ner_detector._addr_view`,
# этап E′). Он появился ПОЗЖЕ этого списка и в него не попал — ровно та же
# служебная копия текста, что четыре соседних, но единственная, доживавшая до
# диска: замер на agency_0002.docx нашёл её в 32 сегментах из 43 (см.
# docs/archive/legal/LEGAL_CHECK.md §3). Инвариант S1 «в файле одна копия текста,
# та, без которой не восстановить» был нарушен молча.
_DOC_METADATA_DROP_KEYS = frozenset({
    "_norm_cache", "_anchor_search_cache", "_per_search_cache", "detection_text",
    "_addr_view_cache",
})


def _strip_detection_caches(metadata: dict) -> dict:
    return {k: v for k, v in metadata.items() if k not in _DOC_METADATA_DROP_KEYS}


def save_doc_segments(session_id: str, doc) -> None:
    """Sidecar {session_id}.doc.json — сегменты исходного документа (текст +
    структура), нужны для пересборки анонимизированного текста после ручной
    правки разметки (U3). СОДЕРЖИТ ПОЛНЫЙ ИСХОДНЫЙ ТЕКСТ (ПДн) — тот же профиль
    пользователя, та же осторожность, что у файла сессии; никуда не отправляется,
    не входит в git (```~/.shifrator``` вне репозитория).

    ЭТАП S1: `metadata` каждого сегмента очищается от служебных кешей
    детекции (`_strip_detection_caches`) ПЕРЕД записью — это единственные лишние
    копии ПДн в этом файле (см. `_DOC_METADATA_DROP_KEYS`); `s.text` — ОДНА
    оставшаяся копия, необходимая для восстановления/рендера."""
    store = default_storage_dir()
    store.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_format": doc.source_format,
        "segments": [
            {"id": s.id, "text": s.text, "source_type": s.source_type,
             "metadata": _strip_detection_caches(s.metadata)}
            for s in doc.segments
        ],
    }
    _write_encrypted_json(_doc_path(store, session_id), payload)


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
    data = _read_encrypted_json(path)
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
        return _read_encrypted_json(path)
    except (OSError, ValueError, InvalidToken, vault.KeyUnavailableError):
        return []


def _write_markup_list(store: Path, session_id: str, entries: list[dict]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    _write_encrypted_json(_markup_path(store, session_id), entries)


def save_markup(session_id: str, entry: dict) -> str:
    """Сохраняет ОДНУ запись ручной разметки (см. app/core.py — форма записи:
    kind/entity_type/segment_id/start/end/value/created_at/build_mark/applied).
    Список копится в {session_id}.markup.json в `default_markup_dir()` —
    ОТДЕЛЬНОМ от сессии хранилище (см. §ЭТАП S1): содержит фрагмент РЕАЛЬНОГО
    текста (`value`) — ПДн, поэтому своя, более долгая политика срока
    (`_MARKUP_TTL_DAYS`), а не TTL сессии. Возвращает id новой записи.
    """
    store = default_markup_dir()
    entries = _load_markup_list(store, session_id)
    markup_id = str(uuid.uuid4())
    entries.append({**entry, "id": markup_id})
    _write_markup_list(store, session_id, entries)
    return markup_id


def list_markup(session_id: str) -> list[dict]:
    return _load_markup_list(default_markup_dir(), session_id)


def update_markup(session_id: str, markup_id: str, **patch) -> bool:
    """Точечно обновляет поля записи разметки (напр. entity_type при
    пере-выборе типа ДО применения). Возвращает False, если запись не найдена."""
    store = default_markup_dir()
    entries = _load_markup_list(store, session_id)
    for e in entries:
        if e["id"] == markup_id:
            e.update(patch)
            _write_markup_list(store, session_id, entries)
            return True
    return False


def delete_markup(session_id: str, markup_id: str) -> bool:
    store = default_markup_dir()
    entries = _load_markup_list(store, session_id)
    new_entries = [e for e in entries if e["id"] != markup_id]
    if len(new_entries) == len(entries):
        return False
    _write_markup_list(store, session_id, new_entries)
    return True


def delete_session_markup(session_id: str) -> bool:
    """Удаляет НАКОПЛЕННУЮ разметку одной сессии отдельным действием (сессию —
    её .enc/.txt/.meta/.doc — не трогает). Возвращает True, если файл был и
    удалён; False, если разметки по этой сессии не было."""
    path = _markup_path(default_markup_dir(), session_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def delete_all_markup() -> int:
    """Удаляет ВСЮ накопленную разметку (все сессии) — одно явное действие
    пользователя («удалить всю накопленную разметку»), приёмка задачи S1-2.
    Возвращает число удалённых файлов (= число сессий, у которых была разметка)."""
    store = default_markup_dir()
    if not store.exists():
        return 0
    removed = 0
    for p in store.glob("*.markup.json"):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def purge_all() -> dict:
    """Одна точка автоочистки: просроченные сессии + просроченная разметка.

    Возвращает {"sessions": n, "markup_entries": m} — вызывающий ОБЯЗАН показать
    это пользователю, если что-то удалено. Тихая автоочистка неотличима от
    потери данных: человек, не увидевший сообщения, решит, что программа
    сломалась, а не что сработал объявленный ему срок.

    Фонового потока нет намеренно (см. §ЭТАП STORE выше): зовётся на старте
    интерфейса и после операций с хранилищем.

    Ни один файл ЖИВОЙ сессии здесь не трогается: `purge_expired` удаляет
    только те `.enc`, у которых `expires_at` уже в прошлом, а сайдкары — только
    осиротевшие. То, с чем работают прямо сейчас, по определению не просрочено.
    """
    sessions = purge_expired()
    entries = purge_expired_markup()
    return {"sessions": sessions, "markup_entries": entries}


def purge_expired_markup(ttl_days: int | None = None) -> int:
    """Удаляет ЗАПИСИ разметки старше ttl_days (по created_at КАЖДОЙ записи),
    независимо от жизни сессии, к которой они относятся, — сессия могла быть
    удалена/просрочена уже давно, разметка живёт своим сроком (§ЭТАП S1).
    Файл сессии, у которой все записи истекли, удаляется целиком; частично
    истекший — переписывается с оставшимися. Записи без валидного created_at
    НЕ трогает (безопасный дефолт — не терять данные без даты по ошибке).
    Возвращает число удалённых ЗАПИСЕЙ (не файлов)."""
    store = default_markup_dir()
    if not store.exists():
        return 0
    if ttl_days is None:
        ttl_days = retention_settings()["markup_days"]
    cutoff = datetime.now().astimezone() - timedelta(days=ttl_days)
    removed = 0
    for path in store.glob("*.markup.json"):
        try:
            entries = _read_encrypted_json(path)
        except (OSError, ValueError, InvalidToken, vault.KeyUnavailableError):
            continue
        kept = []
        for e in entries:
            try:
                created = datetime.fromisoformat(e["created_at"])
            except (KeyError, TypeError, ValueError):
                kept.append(e)
                continue
            if created < cutoff:
                removed += 1
            else:
                kept.append(e)
        if not kept:
            try:
                path.unlink()
            except OSError:
                pass
        elif len(kept) != len(entries):
            _write_encrypted_json(path, kept)
    return removed


def markup_summary() -> dict:
    """Свод по накопленной разметке для интерфейса (задача S1-1: пользователь
    должен ВИДЕТЬ, что разметка хранится дольше сессий и что она есть):
    {sessions, entries, oldest_created_at, ttl_days}."""
    store = default_markup_dir()
    sessions = 0
    entries_total = 0
    oldest = None
    if store.exists():
        for path in store.glob("*.markup.json"):
            try:
                data = _read_encrypted_json(path)
            except (OSError, ValueError, InvalidToken, vault.KeyUnavailableError):
                continue
            if not data:
                continue
            sessions += 1
            entries_total += len(data)
            for e in data:
                ca = e.get("created_at")
                if ca and (oldest is None or ca < oldest):
                    oldest = ca
    return {"sessions": sessions, "entries": entries_total, "oldest_created_at": oldest,
            "ttl_days": retention_settings()["markup_days"]}


def migrate_legacy_markup() -> int:
    """Переносит {sid}.markup.json, сохранённые ДО этапа S1 (в директории
    сессий), в новое отдельное хранилище (`default_markup_dir()`) — иначе
    существующая разметка потерялась бы вместе со следующим `purge_expired`
    (старое поведение удаляло sidecar при просрочке .enc). Идемпотентна: если
    в старом месте ничего нет — no-op. Сливает по id, если в НОВОМ месте уже
    есть файл того же session_id (не должно случаться в норме, но безопасно).
    Возвращает число мигрированных файлов (= сессий). Вызывать один раз при
    старте UI, не на каждый запрос."""
    old_dir = default_storage_dir()
    if not old_dir.exists():
        return 0
    new_dir = default_markup_dir()
    migrated = 0
    for old_path in old_dir.glob("*.markup.json"):
        sid = old_path.name[: -len(".markup.json")]
        try:
            old_entries = _read_encrypted_json(old_path)
        except (OSError, ValueError, InvalidToken, vault.KeyUnavailableError):
            continue
        existing = _load_markup_list(new_dir, sid)
        existing_ids = {e.get("id") for e in existing}
        merged = existing + [e for e in old_entries if e.get("id") not in existing_ids]
        _write_markup_list(new_dir, sid, merged)
        try:
            old_path.unlink()
        except OSError:
            pass
        migrated += 1
    return migrated
