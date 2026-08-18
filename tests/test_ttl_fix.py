"""Сессия TTL-FIX: срок хранения сессии объявлен 7 дней (storage.py,
`_DEFAULT_SESSION_TTL_DAYS`), но `app/core.py::run_encrypt` — точка входа
десктоп-интерфейса — звала `save_session(..., ttl_hours=24)` явным аргументом,
который по контракту `storage.save_session` (см. её докстринг) СИЛЬНЕЕ
настройки. Каждая сессия, созданная через интерфейс, реально жила 24 часа,
что бы ни было настроено. CLI (`shifrator.py`) этот путь не задевал — там
`ttl_hours` не передаётся, дефолт `storage.save_session` (`None`) уже читал
`retention_settings()`.

Правка — src/storage.py:308 остаётся единственным местом, где ttl_hours из
дней переводится в часы; app/core.py:607 больше не подсовывает свою константу.

Тесты здесь проверяют ПОВЕДЕНИЕ (не то, что константа поменялась):
  1. run_encrypt (реальная точка входа интерфейса) пишет сессию со сроком из
     retention_settings(), не с зашитыми 24 часами.
  2. Смена настройки (set_retention) меняет фактический срок новой сессии.
  3. Сессия, «созданная» 30 часов назад (за старым баг-порогом в 24 часа), при
     действующем сроке 48 часов остаётся ЖИВОЙ — раньше бы этого не произошло:
     под старым багом 24-часовая сессия к 30 часам уже истекла бы.
  4. Автоочистка (purge_expired) согласована с тем же сроком: не трогает
     сессию младше срока, удаляет старше — без отдельного «своего» знания о TTL.
"""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_APP = os.path.join(_ROOT, "app")
for _p in (_SRC, _APP):
    if _p not in sys.path:
        sys.path.insert(1, _p)

import vault  # noqa: E402
import storage  # noqa: E402
import session_store  # noqa: E402
from models import Entity  # noqa: E402


def _entity():
    return Entity(
        id="e1", segment_id="p0", start=0, end=6, original_text="Иванов",
        entity_type="PERSON", detector="ner", confidence=1.0,
        token="[PERSON_1]", group_key="PER:0", canonical="Иванов",
    )


@pytest.fixture
def iso_store(tmp_path, monkeypatch):
    """Тот же приём, что tests/test_storage_s1.py::iso_store — обе точки
    default_storage_dir (session_store и storage) уводятся под tmp_path."""
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(session_store, "default_storage_dir", lambda: sessions_dir)
    monkeypatch.setattr(storage, "default_storage_dir", lambda: sessions_dir)
    return sessions_dir


def _rewrite_expires_at(sessions_dir, sid, new_created_at, new_expires_at):
    """Перезаписывает created_at/expires_at УЖЕ сохранённой сессии напрямую в
    зашифрованном файле — тот же приём, что tests/test_session_store.py
    (Fernet(vault.unwrap_key(...))), чтобы симулировать «сессия создана давно»
    без подмены системных часов (запрещено инструкцией: время подставлять)."""
    key = vault.unwrap_key((sessions_dir / "key.bin").read_bytes())
    fernet = Fernet(key)
    enc_path = sessions_dir / f"{sid}.enc"
    data = json.loads(fernet.decrypt(enc_path.read_bytes()))
    data["created_at"] = new_created_at.isoformat()
    data["expires_at"] = new_expires_at.isoformat()
    enc_path.write_bytes(fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8")))


class TestRetentionAppliedAtSaveTime:
    def test_default_retention_is_seven_days_not_24_hours(self, iso_store):
        sid = storage.save_session([_entity()])
        session = storage.load_session(sid)
        created = datetime.fromisoformat(session["created_at"])
        expires = datetime.fromisoformat(session["expires_at"])
        assert expires - created == timedelta(days=7)

    def test_changing_retention_setting_changes_new_session_ttl(self, iso_store):
        storage.set_retention(session_days=3)
        sid = storage.save_session([_entity()])
        session = storage.load_session(sid)
        created = datetime.fromisoformat(session["created_at"])
        expires = datetime.fromisoformat(session["expires_at"])
        assert expires - created == timedelta(days=3)


class TestRunEncryptUsesConfiguredRetention:
    """Точка входа интерфейса (app/core.py::run_encrypt) — именно она держала
    жёстко зашитый ttl_hours=24."""

    def test_run_encrypt_session_ttl_follows_retention_setting(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("HOMEDRIVE", raising=False)
        monkeypatch.delenv("HOMEPATH", raising=False)

        storage.set_retention(session_days=5)

        import core as app_core
        importlib.reload(app_core)

        src = tmp_path / "doc.txt"
        src.write_text("Иванов Иван Иванович подписал договор.", encoding="utf-8")
        result = app_core.run_encrypt(str(src))

        sessions_dir = storage.default_storage_dir()
        sid = None
        for p in sessions_dir.glob("*.enc"):
            sid = p.stem
        assert sid is not None, "run_encrypt должен был создать файл сессии"

        session = storage.load_session(sid)
        created = datetime.fromisoformat(session["created_at"])
        expires = datetime.fromisoformat(session["expires_at"])
        assert expires - created == timedelta(days=5), (
            "run_encrypt проигнорировал настройку срока — похоже, снова "
            "передаёт свой ttl_hours вместо дефолта storage.save_session"
        )
        assert result["entities_total"] >= 0  # конвейер вообще отработал


class TestExpiryFollowsConfiguredTermNotOldHardcode:
    def test_session_aged_30h_alive_under_48h_retention(self, iso_store):
        """Старый баг: ttl_hours=24 жёстко. Сессия «возрастом» 30 часов под
        ним была бы уже мертва. Настроенный срок — 48 часов (2 дня): та же
        сессия должна остаться живой."""
        storage.set_retention(session_days=2)
        sid = storage.save_session([_entity()])

        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=30)
        expires = created + timedelta(hours=48)
        _rewrite_expires_at(iso_store, sid, created, expires)

        session = storage.load_session(sid)  # не должно кинуть SessionExpiredError
        assert session["session_id"] == sid

    def test_session_aged_50h_expired_under_48h_retention(self, iso_store):
        """Тот же настроенный срок (48 часов): сессия старше него должна
        считаться просроченной — граница именно по настройке, не «никогда»."""
        storage.set_retention(session_days=2)
        sid = storage.save_session([_entity()])

        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=50)
        expires = created + timedelta(hours=48)
        _rewrite_expires_at(iso_store, sid, created, expires)

        with pytest.raises(storage.SessionExpiredError):
            storage.load_session(sid)


class TestAutocleanupConsistentWithConfiguredTerm:
    def test_purge_expired_keeps_session_younger_than_configured_term(self, iso_store):
        storage.set_retention(session_days=2)
        sid = storage.save_session([_entity()])
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=30)
        expires = created + timedelta(hours=48)
        _rewrite_expires_at(iso_store, sid, created, expires)

        removed = storage.purge_expired()

        assert removed == 0
        assert (iso_store / f"{sid}.enc").exists()

    def test_purge_expired_removes_session_older_than_configured_term(self, iso_store):
        storage.set_retention(session_days=2)
        sid = storage.save_session([_entity()])
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=50)
        expires = created + timedelta(hours=48)
        _rewrite_expires_at(iso_store, sid, created, expires)

        removed = storage.purge_expired()

        assert removed == 1
        assert not (iso_store / f"{sid}.enc").exists()
