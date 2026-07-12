"""Этап 2 — охота за критическими ошибками использования блока 7 (shifrator.py CLI)
на реальных сценариях. НЕ дублирует test_cli.py этапа 1 (happy path roundtrip,
отсутствующий файл, неподдерживаемый формат, argparse-код 2).

Фокус: как CLI ведёт себя на битом входе и на честно истёкшей сессии — то, что
пользователь увидит в stderr в реальной эксплуатации.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from session_store import save_session
from models import Entity

SHIFRATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shifrator.py"
)


def _run(args, tmp_path, stdin_text=None):
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        USERPROFILE=str(tmp_path / "home"),
        HOME=str(tmp_path / "home"),
    )
    return subprocess.run(
        [sys.executable, SHIFRATOR] + args,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=os.path.dirname(SHIFRATOR),
    )


# --------------------------------------------------------------------------- #
# F3b — ВАЖНО: encrypt на битом .docx выводит сырой python-трейсбек вместо
#       локализованного "Ошибка: ..." (следствие F3 в блоке 1)
# --------------------------------------------------------------------------- #
class TestEncryptCorruptDocx:
    """Реальный сценарий: пользователь переименовал .txt в .docx или файл скачался
    битым. Все прочие ошибки encrypt (файл не найден, неподдерживаемый формат) дают
    аккуратное `Ошибка: ...` и код 1. Здесь же наружу вываливается трейсбек
    docx.opc.exceptions.PackageNotFoundError — не по контракту блока 7.
    Код возврата случайно оказывается 1 (Python так завершает при необработанном
    исключении), поэтому проверять надо именно ОТСУТСТВИЕ трейсбека и наличие
    локализованного сообщения.
    """

    def test_corrupt_docx_prints_localized_error_not_traceback(self, tmp_path):
        bad = tmp_path / "report.docx"
        bad.write_text("на самом деле просто текст", encoding="utf-8")

        result = _run(["encrypt", str(bad)], tmp_path)

        assert "Traceback" not in result.stderr
        assert "PackageNotFoundError" not in result.stderr
        assert "Ошибка" in result.stderr


# --------------------------------------------------------------------------- #
# F6 — ВАЖНО: честно истёкшая сессия через CLI сообщает "не найдена"
#      вместо "истекла"; ветка except SessionExpiredError — мёртвый код
# --------------------------------------------------------------------------- #
class TestDecryptExpiredSessionMessage:
    """Реальный сценарий: пользователь зашифровал документ, вернулся через сутки+ и
    вызвал decrypt с ПРАВИЛЬНЫМ session_id. cmd_decrypt первым делом зовёт
    purge_expired(), который УДАЛЯЕТ истёкший .enc, после чего load_session кидает
    SessionNotFoundError. Пользователь видит 'сессия не найдена' (как будто ошибся в id),
    хотя правда — 'сессия истекла'. Обработчик `except SessionExpiredError` в cmd_decrypt
    для нормального TTL-истечения недостижим. Спека блока 5 в приёмке прямо ожидает
    SessionExpiredError на истёкшей сессии.
    """

    def _make_expired_session(self, tmp_path):
        store = tmp_path / "home" / ".shifrator" / "sessions"
        store.mkdir(parents=True, exist_ok=True)
        ent = Entity(
            id="1", segment_id="p0", start=0, end=3, original_text="abc",
            entity_type="ORG", detector="ner", confidence=1.0, token="[ORG_1]",
        )
        return save_session([ent], ttl_hours=-1, storage_dir=str(store))

    def test_expired_session_reports_expired_not_not_found(self, tmp_path):
        sid = self._make_expired_session(tmp_path)
        result = _run(["decrypt", sid], tmp_path, stdin_text="какой-то текст")
        assert result.returncode == 1
        assert "истекла" in result.stderr, (
            f"Ожидалось сообщение об истечении сессии, получено: {result.stderr!r}"
        )
