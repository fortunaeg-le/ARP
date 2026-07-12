"""Этап 2 — целенаправленная охота за критическими ошибками использования блока 5
(session_store.py). НЕ дублирует test_session_store.py этапа 1 (happy path, дедуп,
purge-селективность). Здесь — реальные "грязные" сценарии: гонка при создании ключа,
повреждённый/удалённый key.bin, граница TTL, session_id как источник атаки.

Тесты, помеченные xfail(strict=True), фиксируют ПОДТВЕРЖДЁННЫЕ дефекты: тело теста
проверяет КОРРЕКТНОЕ (ожидаемое по спеке) поведение, которое сейчас НЕ выполняется.
xfail => дефект воспроизводится; если тест вдруг пройдёт — дефект исправлен и strict
превратит это в FAIL, чтобы никто не забыл убрать метку.

Обычные (зелёные) тесты фиксируют либо корректную защиту (path traversal), либо
осознанное пограничное поведение, которое важно держать под наблюдением.
"""
import tempfile
import threading
from pathlib import Path

import pytest

import session_store
from session_store import (
    save_session,
    load_session,
    purge_expired,
    SessionNotFoundError,
    SessionExpiredError,
)
from models import Entity


def _entity(token="[ORG_1]"):
    return Entity(
        id=token,
        segment_id="p0",
        start=0,
        end=3,
        original_text="abc",
        entity_type="ORG",
        detector="ner",
        confidence=1.0,
        token=token,
    )


# --------------------------------------------------------------------------- #
# F1 — КРИТИЧНО: гонка при создании key.bin теряет сессию безвозвратно
# --------------------------------------------------------------------------- #
class TestConcurrentKeyCreationRace:
    """Реальный сценарий: пользователь (или скрипт) запускает два `encrypt` почти
    одновременно на СВЕЖЕЙ директории хранилища (key.bin ещё не создан). Оба процесса
    в `_load_or_create_key` видят отсутствие key.bin, каждый генерирует СВОЙ ключ,
    второй перезаписывает key.bin первого. Сессия, зашифрованная перезаписанным
    ключом, становится нерасшифровываемой навсегда — тихая потеря токен-маппинга.
    """

    def test_two_concurrent_saves_on_fresh_dir_both_recoverable(self, tmp_path):
        store = str(tmp_path / "sessions")

        # Барьер форсирует именно то окно гонки, которое описано в дефекте:
        # оба потока доходят до генерации ключа, пока key.bin ещё отсутствует.
        orig_generate = session_store.Fernet.generate_key
        barrier = threading.Barrier(2)

        def racy_generate():
            barrier.wait()
            return orig_generate()

        session_store.Fernet.generate_key = staticmethod(racy_generate)
        results = {}
        try:
            def worker(name):
                results[name] = save_session([_entity(f"[ORG_{name}]")], storage_dir=store)

            threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            session_store.Fernet.generate_key = staticmethod(orig_generate)

        # Ожидаемо: обе сессии сохранены и обе загружаются. Фактически одна теряется.
        for name, sid in results.items():
            load_session(sid, storage_dir=store)


# --------------------------------------------------------------------------- #
# F2 — КРИТИЧНО: повреждённый key.bin роняет load_session/purge_expired
#      неперехваченным ValueError вместо SessionNotFoundError
# --------------------------------------------------------------------------- #
class TestCorruptedKeyFile:
    """Реальный сценарий: key.bin повреждён (обрезан при сбое записи, затёрт
    антивирусом/бэкапом, синхронизирован наполовину облачным клиентом). Спека блока 5
    требует, чтобы load_session на "повреждён файл или ключ" кидал SessionNotFoundError,
    а не падал. Сейчас Fernet(key) на битом ключе кидает ValueError, который не ловится.
    В CLI `decrypt` первым делом вызывается purge_expired — он падает тем же ValueError
    ещё до какой-либо обработки, роняя всю команду с трейсбеком.
    """

    def _corrupt_setup(self, tmp_path):
        store = str(tmp_path / "sessions")
        sid = save_session([_entity()], storage_dir=store)
        (Path(store) / "key.bin").write_bytes(b"not-a-valid-fernet-key")
        return store, sid

    def test_load_session_corrupted_key_raises_session_not_found(self, tmp_path):
        store, sid = self._corrupt_setup(tmp_path)
        with pytest.raises(SessionNotFoundError):
            load_session(sid, storage_dir=store)

    def test_purge_expired_corrupted_key_does_not_crash(self, tmp_path):
        store, _sid = self._corrupt_setup(tmp_path)
        # Ожидаемо: purge переживает битый ключ (или чисто сообщает), а не роняет процесс.
        purge_expired(storage_dir=store)


# --------------------------------------------------------------------------- #
# F5 — ВАЖНО: удаление key.bin между save и load тихо пересоздаёт ключ,
#      делая ВСЕ ранее сохранённые сессии нерасшифровываемыми
# --------------------------------------------------------------------------- #
class TestKeyDeletedBetweenSaveAndLoad:
    """Реальный сценарий: key.bin удалён (чистка временных файлов, пользователь стёр
    "непонятный" файл, неполная синхронизация). При следующем load_session
    `_load_or_create_key` МОЛЧА создаёт НОВЫЙ key.bin. Это маскирует проблему и,
    что хуже, гарантирует, что все прочие ещё живые сессии в этой директории теперь
    тоже неrecoverable — их зашифровали старым, уже несуществующим ключом.
    Тест фиксирует именно этот опасный побочный эффект (создание нового ключа на чтении).
    """

    def test_load_after_key_deletion_silently_recreates_key(self, tmp_path):
        store = Path(tmp_path / "sessions")
        sid = save_session([_entity()], storage_dir=str(store))
        (store / "key.bin").unlink()

        with pytest.raises(SessionNotFoundError):
            load_session(sid, storage_dir=str(store))

        # Опасный побочный эффект: чтение пересоздало ключ. Новый ключ != старого,
        # поэтому исходная сессия (и любые другие в этой директории) потеряны навсегда.
        assert (store / "key.bin").exists(), (
            "load_session пересоздал key.bin на чтении — это тихо хоронит все прежние "
            "сессии, зашифрованные старым ключом (ВАЖНО: потеря данных без ошибки)."
        )


# --------------------------------------------------------------------------- #
# F7 — НЕЗНАЧИТЕЛЬНО / пробел в спеке: граница TTL включительна
# --------------------------------------------------------------------------- #
class TestTtlBoundaryInclusive:
    """Спека фиксирует сравнение `datetime.now(utc) >= expires_at`, но НЕ описывает
    явно, считается ли сессия живой ровно в момент expires_at. При ttl_hours=0
    (expires_at == created_at) любая последующая загрузка мгновенно считается истёкшей.
    Это пограничный, но детерминированный факт — фиксируем, чтобы фаза 2 приняла
    осознанное решение о включительности границы.
    """

    def test_ttl_zero_is_immediately_expired(self, tmp_path):
        store = str(tmp_path / "sessions")
        sid = save_session([], ttl_hours=0, storage_dir=store)
        with pytest.raises(SessionExpiredError):
            load_session(sid, storage_dir=store)


# --------------------------------------------------------------------------- #
# C3 — зелёный: session_id как источник атаки. Защита от path traversal держит.
# --------------------------------------------------------------------------- #
class TestSessionIdAttackVectorsBlocked:
    """Реальный сценарий: session_id приходит извне (аргумент CLI, чужой ввод).
    Проверяем, что валидация `^[0-9a-f-]{36}$` отбивает path traversal, пустой id,
    юникод и слишком длинные значения ЧИСТЫМ SessionNotFoundError, не трогая ФС и
    не падая иным исключением. Это подтверждение корректной защиты (не дефект)."""

    @pytest.mark.parametrize(
        "attack",
        [
            "../../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config",
            "/etc/shadow",
            "0000000000000000000000000000000000000000",  # 40 символов — длиннее uuid
            "",
            "café-0000-0000-0000-00000000",  # юникод
            "00000000-0000-0000-0000-00000000000g",  # 'g' не hex
            "../" * 12,
        ],
    )
    def test_malicious_session_id_raises_clean_not_found(self, tmp_path, attack):
        store = str(tmp_path / "sessions")
        save_session([], storage_dir=store)  # директория и key.bin существуют
        with pytest.raises(SessionNotFoundError):
            load_session(attack, storage_dir=store)
