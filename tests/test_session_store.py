"""Тесты блока 5 — session_store.py. Все хранилища только через tmp_path (изолировано
от реальной ~/.shifrator/sessions), см. правило блока 5 в задании на тесты."""
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from models import Entity
from session_store import (
    save_session,
    load_session,
    purge_expired,
    delete_session,
    SessionNotFoundError,
    SessionExpiredError,
)


def _entity(segment_id, token, entity_type, original_text):
    return Entity(
        id=str(uuid.uuid4()),
        segment_id=segment_id,
        start=0,
        end=len(original_text),
        original_text=original_text,
        entity_type=entity_type,
        detector="ner",
        confidence=1.0,
        token=token,
    )


class TestSaveLoadRoundtrip:
    """HANDOFF_5, Приёмка: save_session -> load_session, данные идентичны."""

    def test_save_then_load_returns_same_entities(self, tmp_path):
        """Спека, блок 5, Приёмка: save_session -> load_session — данные идентичны исходным."""
        entities = [_entity("p3", "[ORG_1]", "ORG", "ООО «Ромашка»")]
        sid = save_session(entities, storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        assert session["entities"][0]["original_text"] == "ООО «Ромашка»"

    def test_save_returns_valid_uuid4_session_id_when_none_given(self, tmp_path):
        """Спека, блок 5: если session_id не передан, save_session генерирует uuid4."""
        sid = save_session([], storage_dir=str(tmp_path))
        assert uuid.UUID(sid)

    def test_load_session_dict_contains_all_required_keys(self, tmp_path):
        """HANDOFF_5, Инварианты: ключи session_id/created_at/expires_at/entities всегда присутствуют."""
        sid = save_session([], storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        # ЭТАП E: формат v2 добавил маркер "format" (=2). Прежние 4 ключа обязаны
        # оставаться (обратная совместимость читателей v1).
        assert set(session.keys()) == {"format", "session_id", "created_at",
                                       "expires_at", "entities"}
        assert session["format"] == 2

    def test_new_process_can_load_session_saved_by_another(self, tmp_path):
        """Спека, блок 5, Приёмка: load_session в новом процессе (перезапуск интерпретатора) находит данные."""
        entities = [_entity("p3", "[ORG_1]", "ORG", "ООО «Ромашка»")]
        sid = save_session(entities, storage_dir=str(tmp_path))
        script = (
            "import sys; sys.path.insert(0, r'{proj}');"
            "from session_store import load_session;"
            "s = load_session('{sid}', storage_dir=r'{storage}');"
            "print(s['entities'][0]['original_text'])"
        ).format(proj="C:\\Jesus\\ARP", sid=sid, storage=str(tmp_path))
        import os
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", env=env
        )
        assert result.returncode == 0
        assert "ООО «Ромашка»" in result.stdout


class TestSaveSessionDeduplication:
    """HANDOFF_5, Данные для тестов, п.1 — дедупликация по токену."""

    def test_two_entities_same_token_produce_one_record(self, tmp_path):
        """HANDOFF_5, п.1: два Entity с token='[ORG_1]' -> в файле одна запись для [ORG_1]."""
        entities = [
            _entity("p3", "[ORG_1]", "ORG", "ООО «Ромашка»"),
            _entity("p7", "[ORG_1]", "ORG", "ООО «Ромашка»"),
            _entity("p3", "[INN_1]", "INN", "7701234567"),
        ]
        sid = save_session(entities, storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        assert len(session["entities"]) == 2

    def test_deduplicated_record_keeps_first_occurrence_segment_id(self, tmp_path):
        """HANDOFF_5, п.1: у [ORG_1] сохранён segment_id='p3' (первое вхождение)."""
        entities = [
            _entity("p3", "[ORG_1]", "ORG", "ООО «Ромашка»"),
            _entity("p7", "[ORG_1]", "ORG", "ООО «Ромашка»"),
        ]
        sid = save_session(entities, storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        org_record = next(e for e in session["entities"] if e["token"] == "[ORG_1]")
        assert org_record["segment_id"] == "p3"

    def test_each_entity_record_has_exactly_four_keys(self, tmp_path):
        """HANDOFF_5 (обновлено этапом E, затем этапом S1): 4 инвариантных ключа
        v1 ПЛЮС поля v2 (canonical, occurrences) ПЛЮС провенанс S1 (detector,
        group_key). Прежние ключи не исчезают — обратная совместимость
        читателей v1 (file_detokenizer читает original_text)."""
        entities = [_entity("p3", "[ORG_1]", "ORG", "ООО «Ромашка»")]
        sid = save_session(entities, storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        rec = session["entities"][0]
        assert set(rec.keys()) == {"token", "entity_type", "original_text",
                                   "segment_id", "canonical", "occurrences",
                                   "detector", "group_key"}
        assert [o["surface"] for o in rec["occurrences"]] == ["ООО «Ромашка»"]
        assert {"segment_id", "start", "end", "spans", "surface",
                "detector", "group_key"} <= set(rec["occurrences"][0])


class TestSaveSessionEmptyEntities:
    """Спека, блок 5: пустой список entities всё равно создаёт сессию."""

    def test_empty_entities_list_still_creates_session(self, tmp_path):
        """Спека, блок 5: если entities пуст — сессия всё равно создаётся, с 'entities': []."""
        sid = save_session([], storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        assert session["entities"] == []


class TestSaveSessionIdValidation:
    """HANDOFF_5, Данные для тестов, п.5 — валидация формата session_id."""

    def test_invalid_explicit_session_id_raises_value_error(self, tmp_path):
        """HANDOFF_5, п.5: save_session([], session_id='../evil') -> ValueError."""
        with pytest.raises(ValueError):
            save_session([], session_id="../evil", storage_dir=str(tmp_path))

    def test_valid_explicit_session_id_is_used_as_is(self, tmp_path):
        """Спека, блок 5: явно переданный валидный session_id используется как есть."""
        explicit_id = str(uuid.uuid4())
        sid = save_session([], session_id=explicit_id, storage_dir=str(tmp_path))
        assert sid == explicit_id

    def test_saving_with_existing_session_id_overwrites_file(self, tmp_path):
        """Спека, блок 5: если файл сессии с таким id уже существует — он перезаписывается."""
        explicit_id = str(uuid.uuid4())
        save_session([_entity("p1", "[ORG_1]", "ORG", "Первый")], session_id=explicit_id, storage_dir=str(tmp_path))
        save_session([_entity("p1", "[ORG_1]", "ORG", "Второй")], session_id=explicit_id, storage_dir=str(tmp_path))
        session = load_session(explicit_id, storage_dir=str(tmp_path))
        assert session["entities"][0]["original_text"] == "Второй"


class TestLoadSessionIdValidation:
    """Спека, блок 5: невалидный session_id в load_session -> SessionNotFoundError без обращения к ФС."""

    def test_invalid_format_session_id_raises_session_not_found(self, tmp_path):
        """HANDOFF_5, п.5: load_session('../../etc/passwd') -> SessionNotFoundError без обращения к ФС."""
        with pytest.raises(SessionNotFoundError):
            load_session("../../etc/passwd", storage_dir=str(tmp_path))

    def test_wellformed_but_nonexistent_session_id_raises_session_not_found(self, tmp_path):
        """Спека, блок 5: session_id корректного формата, но файла нет -> SessionNotFoundError."""
        with pytest.raises(SessionNotFoundError):
            load_session(str(uuid.uuid4()), storage_dir=str(tmp_path))


class TestLoadSessionExpiry:
    """HANDOFF_5, Данные для тестов, п.3 — истёкший TTL."""

    def test_expired_session_raises_session_expired_error(self, tmp_path):
        """Спека, блок 5, Приёмка: ручное изменение expires_at на прошедшую дату -> SessionExpiredError."""
        sid = save_session([], storage_dir=str(tmp_path))
        enc_path = tmp_path / f"{sid}.enc"
        key = (tmp_path / "key.bin").read_bytes()
        fernet = Fernet(key)
        data = json.loads(fernet.decrypt(enc_path.read_bytes()))
        data["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        enc_path.write_bytes(fernet.encrypt(json.dumps(data).encode("utf-8")))

        with pytest.raises(SessionExpiredError):
            load_session(sid, storage_dir=str(tmp_path))

    def test_ttl_hours_sets_expires_at_offset_from_created_at(self, tmp_path):
        """Спека, блок 5: expires_at - created_at == ttl_hours."""
        sid = save_session([], ttl_hours=2, storage_dir=str(tmp_path))
        session = load_session(sid, storage_dir=str(tmp_path))
        created = datetime.fromisoformat(session["created_at"])
        expires = datetime.fromisoformat(session["expires_at"])
        assert expires - created == timedelta(hours=2)


class TestPurgeExpiredSelectivity:
    """HANDOFF_5, Данные для тестов, п.4 — purge_expired трогает только просроченные .enc."""

    def test_purge_removes_only_expired_enc_file(self, tmp_path):
        """HANDOFF_5, п.4: директория с key.bin и просроченным .enc -> удаляется ровно 1 файл."""
        sid_alive = save_session([], ttl_hours=24, storage_dir=str(tmp_path))
        sid_expired = save_session([], ttl_hours=-1, storage_dir=str(tmp_path))

        removed = purge_expired(storage_dir=str(tmp_path))

        assert removed == 1
        assert not (tmp_path / f"{sid_expired}.enc").exists()
        assert (tmp_path / f"{sid_alive}.enc").exists()

    def test_purge_never_removes_key_bin(self, tmp_path):
        """HANDOFF_5, Приёмка: purge_expired не трогает key.bin."""
        save_session([], ttl_hours=-1, storage_dir=str(tmp_path))
        purge_expired(storage_dir=str(tmp_path))
        assert (tmp_path / "key.bin").exists()

    def test_purge_ignores_unrelated_and_broken_files(self, tmp_path):
        """HANDOFF_5, п.4: garbage.txt и не-Fernet broken.enc остаются нетронутыми, не мешают purge."""
        save_session([], ttl_hours=-1, storage_dir=str(tmp_path))
        (tmp_path / "garbage.txt").write_text("не трогать")
        (tmp_path / "broken.enc").write_bytes(b"not a valid fernet token")

        purge_expired(storage_dir=str(tmp_path))

        assert (tmp_path / "garbage.txt").exists()
        assert (tmp_path / "broken.enc").exists()

    def test_purge_returns_zero_when_directory_does_not_exist(self, tmp_path):
        """Граничный случай: purge_expired на несуществующей директории возвращает 0, не падает."""
        missing_dir = tmp_path / "does_not_exist"
        assert purge_expired(storage_dir=str(missing_dir)) == 0

    def test_purge_returns_zero_when_no_expired_files(self, tmp_path):
        """Граничный случай: purge_expired без просроченных файлов возвращает 0."""
        save_session([], ttl_hours=24, storage_dir=str(tmp_path))
        assert purge_expired(storage_dir=str(tmp_path)) == 0


class TestPurgeExpiredExcludeSession:
    """Изменение A: purge_expired(exclude_session_id=...) чистит все ЧУЖИЕ просроченные
    сессии за один проход, но файл исключённой сессии оставляет — чтобы CLI decrypt
    мог отличить «истекла» от «не найдена» даже при очистке в начале команды."""

    def test_excluded_expired_session_survives_while_others_purged(self, tmp_path):
        """Чужой просроченный .enc удаляется за тот же проход, файл исключённой сессии — нет."""
        sid_req = save_session([], ttl_hours=-1, storage_dir=str(tmp_path))
        sid_other = save_session([], ttl_hours=-1, storage_dir=str(tmp_path))

        removed = purge_expired(storage_dir=str(tmp_path), exclude_session_id=sid_req)

        assert removed == 1
        assert not (tmp_path / f"{sid_other}.enc").exists()
        assert (tmp_path / f"{sid_req}.enc").exists()

    def test_excluded_expired_session_still_reports_expired_on_load(self, tmp_path):
        """Файл исключённой сессии на месте => load_session даёт SessionExpiredError, не NotFound."""
        sid_req = save_session([], ttl_hours=-1, storage_dir=str(tmp_path))
        purge_expired(storage_dir=str(tmp_path), exclude_session_id=sid_req)
        with pytest.raises(SessionExpiredError):
            load_session(sid_req, storage_dir=str(tmp_path))

    def test_exclude_none_is_backward_compatible(self, tmp_path):
        """Дефолт exclude_session_id=None — прежнее поведение: удаляются все просроченные."""
        sid = save_session([], ttl_hours=-1, storage_dir=str(tmp_path))
        removed = purge_expired(storage_dir=str(tmp_path))
        assert removed == 1
        assert not (tmp_path / f"{sid}.enc").exists()


class TestDeleteSession:
    """Изменение B: delete_session — ручное удаление одной сессии по session_id."""

    def test_delete_existing_session_returns_true_and_removes_file(self, tmp_path):
        """Happy path: существующая сессия удалена, delete_session -> True, .enc исчез."""
        sid = save_session([_entity("p1", "[ORG_1]", "ORG", "Первый")], storage_dir=str(tmp_path))
        assert (tmp_path / f"{sid}.enc").exists()
        assert delete_session(sid, storage_dir=str(tmp_path)) is True
        assert not (tmp_path / f"{sid}.enc").exists()

    def test_delete_absent_but_wellformed_session_returns_false(self, tmp_path):
        """Валидный по формату, но отсутствующий session_id -> False, без исключений."""
        save_session([], storage_dir=str(tmp_path))  # директория и key.bin существуют
        absent = str(uuid.uuid4())
        assert delete_session(absent, storage_dir=str(tmp_path)) is False

    def test_delete_invalid_format_returns_false_without_touching_fs(self, tmp_path):
        """Невалидный session_id (path traversal) -> False, к ФС не обращается."""
        sid = save_session([], storage_dir=str(tmp_path))
        assert delete_session("../../etc/passwd", storage_dir=str(tmp_path)) is False
        assert (tmp_path / "key.bin").exists()
        assert (tmp_path / f"{sid}.enc").exists()

    def test_delete_never_removes_key_bin(self, tmp_path):
        """delete_session никогда не трогает key.bin."""
        sid = save_session([], storage_dir=str(tmp_path))
        delete_session(sid, storage_dir=str(tmp_path))
        assert (tmp_path / "key.bin").exists()


class TestSessionExceptionTypes:
    """HANDOFF_5, Публичный интерфейс — типы исключений определены в session_store."""

    def test_session_not_found_error_is_exception_subclass(self):
        """Спека, блок 5: SessionNotFoundError(Exception), определён в session_store.py."""
        assert issubclass(SessionNotFoundError, Exception)

    def test_session_expired_error_is_exception_subclass(self):
        """Спека, блок 5: SessionExpiredError(Exception), определён в session_store.py."""
        assert issubclass(SessionExpiredError, Exception)
