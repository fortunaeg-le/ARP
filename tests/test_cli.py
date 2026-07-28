"""Тесты блока 7 — shifrator.py CLI (encrypt/decrypt), через subprocess (реальный процесс).

Изоляция хранилища сессий: USERPROFILE подменяется на tmp_path/home, чтобы CLI
(жёстко использующий ~/.shifrator/sessions по умолчанию) не трогал реальную
директорию пользователя — см. правило "session_store — только через tmp_path".
"""
import os
import subprocess
import sys

import pytest

import conftest

SHIFRATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shifrator.py"
)


def _run(args, tmp_path, stdin_text=None):
    # ЭТАП T1: тесты CLI проверяют КОНВЕЙЕР (какие маски встали, round-trip), а не
    # набор типов по умолчанию — набор пришпилен к «Максимуму» настоящим файлом
    # настроек в подменённом HOME (см. conftest.pin_all_types).
    conftest.pin_all_types(tmp_path / "home")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", USERPROFILE=str(tmp_path / "home"), HOME=str(tmp_path / "home"))
    return subprocess.run(
        [sys.executable, SHIFRATOR] + args,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=os.path.dirname(SHIFRATOR),
    )


class TestCliEncryptTxtRoundtrip:
    """HANDOFF_7, Пример 2 — .txt-вход, encrypt затем decrypt."""

    def test_encrypt_txt_writes_tokenized_text_to_session_file(self, tmp_path):
        """Новый контракт: 'ИНН 7707083893, email test@example.com...' -> файл {storage_dir}/{session_id}.txt с [INN_1]/[EMAIL_1]."""
        txt_path = tmp_path / "input.txt"
        with open(txt_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("ИНН 7707083893, email test@example.com\r\nВторая строка\r\n")

        result = _run(["encrypt", str(txt_path)], tmp_path)
        sid = result.stdout.strip()
        anon_text = (tmp_path / "home" / ".shifrator" / "sessions" / f"{sid}.txt").read_text(encoding="utf-8")
        assert anon_text == "ИНН [INN_1], email [EMAIL_1]\nВторая строка"

    def test_encrypt_prints_valid_session_id_to_stdout(self, tmp_path):
        """Новый контракт: encrypt печатает session_id (валидный uuid4) в stdout, и только его."""
        txt_path = tmp_path / "input.txt"
        with open(txt_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("Просто текст.\r\n")

        result = _run(["encrypt", str(txt_path)], tmp_path)
        import uuid
        sid = result.stdout.strip()
        assert uuid.UUID(sid)

    def test_decrypt_restores_original_text_exactly(self, tmp_path):
        """HANDOFF_7, Пример 2: decrypt(encrypt(text)) восстанавливает исходный текст точно."""
        txt_path = tmp_path / "input.txt"
        with open(txt_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("ИНН 7707083893, email test@example.com\r\nВторая строка\r\n")

        enc = _run(["encrypt", str(txt_path)], tmp_path)
        sid = enc.stdout.strip()
        anon_text = (tmp_path / "home" / ".shifrator" / "sessions" / f"{sid}.txt").read_text(encoding="utf-8")

        dec = _run(["decrypt", sid], tmp_path, stdin_text=anon_text)
        assert dec.stdout.rstrip("\n") == "ИНН 7707083893, email test@example.com\nВторая строка"


class TestCliErrors:
    """HANDOFF_7, Пример 3 — граничные случаи ошибок."""

    def test_encrypt_missing_file_prints_error_and_exits_1(self, tmp_path):
        """HANDOFF_7, Пример 3: encrypt nope.docx -> stderr 'Ошибка: файл не найден: nope.docx', код 1."""
        result = _run(["encrypt", "nope.docx"], tmp_path)
        assert result.returncode == 1
        assert "файл не найден" in result.stderr

    def test_encrypt_unsupported_format_prints_error_and_exits_1(self, tmp_path):
        """HANDOFF_7, Пример 3: encrypt dummy.pdf -> stderr с сообщением о неподдерживаемом формате, код 1."""
        pdf_path = tmp_path / "dummy.pdf"
        pdf_path.write_bytes(b"dummy")
        result = _run(["encrypt", str(pdf_path)], tmp_path)
        assert result.returncode == 1
        assert "Неподдерживаемый формат" in result.stderr

    def test_decrypt_unknown_session_prints_error_and_exits_1(self, tmp_path):
        """HANDOFF_7, Пример 3: decrypt на несуществующий session_id -> stderr 'сессия не найдена', код 1."""
        result = _run(["decrypt", "00000000-0000-0000-0000-000000000000"], tmp_path, stdin_text="текст")
        assert result.returncode == 1
        assert "сессия не найдена" in result.stderr


class TestCliDecryptUnresolvedWarning:
    """Спека, блок 7: если есть unresolved токены, decrypt печатает предупреждение в stderr."""

    def test_decrypt_with_unknown_token_prints_warning(self, tmp_path):
        """Спека, блок 7: ответ LLM с несуществующим токеном -> предупреждение в stderr про нераспознанные токены."""
        txt_path = tmp_path / "input.txt"
        with open(txt_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("Просто текст без сущностей.\r\n")

        enc = _run(["encrypt", str(txt_path)], tmp_path)
        sid = enc.stdout.strip()

        dec = _run(["decrypt", sid], tmp_path, stdin_text="Неизвестный токен [ORG_99] в ответе.")
        assert "[ORG_99]" in dec.stderr


def _sessions_dir(tmp_path):
    return tmp_path / "home" / ".shifrator" / "sessions"


def _make_session(tmp_path, ttl_hours):
    """Создаёт сессию в CLI-хранилище (tmp_path/home/.shifrator/sessions) и возвращает sid."""
    from session_store import save_session
    from models import Entity

    store = _sessions_dir(tmp_path)
    store.mkdir(parents=True, exist_ok=True)
    ent = Entity(
        id="1", segment_id="p0", start=0, end=3, original_text="abc",
        entity_type="ORG", detector="ner", confidence=1.0, token="[ORG_1]",
    )
    return save_session([ent], ttl_hours=ttl_hours, storage_dir=str(store))


class TestCliDecryptPurgesOthersAtStart:
    """Изменение A: decrypt чистит чужие просроченные сессии В НАЧАЛЕ команды (всегда),
    но запрошенную истёкшую сессию по-прежнему сообщает как «истекла», а не «не найдена»."""

    def test_expired_request_reports_expired_and_purges_other_stale(self, tmp_path):
        """Истёкшая запрошенная сессия -> 'истекла' (код 1); чужой просроченный .enc удалён за тот же decrypt."""
        sid_req = _make_session(tmp_path, ttl_hours=-1)     # запрошенная, истёкшая
        sid_other = _make_session(tmp_path, ttl_hours=-1)   # чужая, истёкшая
        sid_alive = _make_session(tmp_path, ttl_hours=24)   # чужая, живая

        result = _run(["decrypt", sid_req], tmp_path, stdin_text="какой-то текст")

        assert result.returncode == 1
        assert "истекла" in result.stderr
        # Чужой просроченный файл реально удалён за этот вызов decrypt...
        assert not (_sessions_dir(tmp_path) / f"{sid_other}.enc").exists()
        # ...живой не тронут, а запрошенный оставлен (исключён из очистки), чтобы дать «истекла».
        assert (_sessions_dir(tmp_path) / f"{sid_alive}.enc").exists()
        assert (_sessions_dir(tmp_path) / f"{sid_req}.enc").exists()


class TestCliDelete:
    """Изменение B: подкоманда delete <session_id> — ручное удаление, код возврата 0 всегда."""

    def test_delete_existing_session_prints_deleted_rc0(self, tmp_path):
        """delete существующей сессии -> stdout 'Сессия <id> удалена', код 0, файл исчез."""
        sid = _make_session(tmp_path, ttl_hours=24)
        result = _run(["delete", sid], tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == f"Сессия {sid} удалена"
        assert not (_sessions_dir(tmp_path) / f"{sid}.enc").exists()

    def test_delete_absent_session_prints_not_found_rc0(self, tmp_path):
        """delete отсутствующей (но валидной по формату) сессии -> 'Сессия <id> не найдена', код 0."""
        _make_session(tmp_path, ttl_hours=24)  # чтобы хранилище существовало
        import uuid
        absent = str(uuid.uuid4())
        result = _run(["delete", absent], tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == f"Сессия {absent} не найдена"

    def test_delete_invalid_format_prints_not_found_rc0(self, tmp_path):
        """delete с невалидным session_id -> 'не найдена', код 0, к ФС не обращается."""
        result = _run(["delete", "not-a-valid-id"], tmp_path)
        assert result.returncode == 0
        assert "не найдена" in result.stdout


class TestCliArgparseExitCode:
    """Спека, блок 7: код возврата 2 — ошибка разбора аргументов (стандартное поведение argparse)."""

    def test_missing_subcommand_exits_with_code_2(self, tmp_path):
        """Спека, блок 7: запуск без подкоманды -> argparse завершает с кодом 2."""
        result = _run([], tmp_path)
        assert result.returncode == 2
