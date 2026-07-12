"""Этап 3 (BREAKING) — направления 2 и 3: "успех", но данные остаются/теряются;
неочевидные последовательности CLI-действий.

Находки:
  B6 (ВАЖНО): `encrypt` пишет РЯДОМ с {session_id}.enc ещё и {session_id}.txt
     (анонимизированный текст, см. cmd_encrypt в shifrator.py). Но ни `delete`,
     ни авто-очистка `purge_expired` этот .txt НЕ трогают (обе работают только с
     *.enc). Итог 1: `delete` печатает "Сессия ... удалена" (успех, код 0), а файл
     .txt с текстом документа остаётся на диске — ложный успех. Итог 2: .txt-файлы
     не удаляются НИКОГДА и накапливаются в ~/.shifrator/sessions без границы даже
     после TTL-очистки .enc (деградация при долгой работе, направление 3).

Подтверждения устойчивости (PASS):
  - encrypt -> delete -> decrypt со СТАРЫМ id => чистый "сессия не найдена", код 1
    (последовательность из направления 2, ведёт себя корректно).
  - "грязный" ответ LLM с JSON-массивом ["ORG_1","ORG_2"] НЕ принимается за токены;
    настоящий [ORG_1] восстанавливается, неизвестный [ORG_9] уходит в unresolved.

Код не чинится.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import session_store
from session_store import save_session, purge_expired, delete_session
from detokenizer import detokenize
from models import Entity

SHIFRATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shifrator.py"
)


def _run(args, home, stdin_text=None):
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        USERPROFILE=str(home),
        HOME=str(home),
    )
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    return subprocess.run(
        [sys.executable, SHIFRATOR] + args,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=os.path.dirname(SHIFRATOR),
    )


def _entity(token="[ORG_1]", original="ООО Ромашка"):
    return Entity(
        id=token, segment_id="l0", start=0, end=1,
        original_text=original, entity_type="ORG",
        detector="ner", confidence=1.0, token=token,
    )


# --------------------------------------------------------------------------- #
# B6 — ВАЖНО: delete рапортует успех, но .txt-сайдкар остаётся
# --------------------------------------------------------------------------- #
class TestDeleteLeavesTxtSidecar:
    def test_delete_removes_both_enc_and_txt_sidecar(self, tmp_path):
        """B6-фикс: delete удаляет и .enc, и .txt-сайдкар, и рапортует успех.
        Раньше .txt оставался на диске при рапорте «удалена» (ложный успех)."""
        home = tmp_path / "home"
        doc = tmp_path / "contract.txt"
        doc.write_text("ООО Ромашка, ИНН 7707083893", encoding="utf-8")

        enc = _run(["encrypt", str(doc)], home)
        assert enc.returncode == 0, enc.stderr
        sid = enc.stdout.strip()

        sessions = home / ".shifrator" / "sessions"
        assert (sessions / f"{sid}.enc").exists()
        assert (sessions / f"{sid}.txt").exists(), "encrypt должен был создать .txt-сайдкар"

        dele = _run(["delete", sid], home)
        assert dele.returncode == 0
        assert "удалена" in dele.stdout  # CLI рапортует успех

        # теперь успех не ложный: оба файла удалены.
        assert not (sessions / f"{sid}.enc").exists()
        assert not (sessions / f"{sid}.txt").exists(), "B6-фикс: .txt должен удаляться вместе с .enc"

    # Строгая репродукция находки B6 (была xfail): ПОСЛЕ 'удалена' файлов сессии быть не должно.
    def test_after_delete_no_session_files_remain(self, tmp_path):
        home = tmp_path / "home"
        doc = tmp_path / "c.txt"
        doc.write_text("ООО Ромашка", encoding="utf-8")
        sid = _run(["encrypt", str(doc)], home).stdout.strip()
        _run(["delete", sid], home)
        sessions = home / ".shifrator" / "sessions"
        leftovers = [p.name for p in sessions.iterdir() if p.name.startswith(sid)]
        assert leftovers == [], f"после delete остались файлы сессии: {leftovers}"

    def test_purge_expired_removes_txt_sidecar(self, tmp_path):
        """B6-фикс: просроченный .enc вычищается purge_expired ВМЕСТЕ со своим
        .txt-сайдкаром (иначе анонимизированные тексты копятся без границы). Сайдкар
        живой (непросроченной) сессии purge не трогает."""
        store = tmp_path / "sessions"
        store.mkdir()
        sid = save_session([_entity()], ttl_hours=24, storage_dir=str(store))
        # ttl=0 -> сессия мгновенно истекает по правилу now >= expires_at.
        sid_exp = save_session([_entity()], ttl_hours=0, storage_dir=str(store))
        # положить рядом .txt-сайдкары, как это делает cmd_encrypt
        (store / f"{sid}.txt").write_text("аноним 1", encoding="utf-8")
        (store / f"{sid_exp}.txt").write_text("аноним 2", encoding="utf-8")

        removed = purge_expired(storage_dir=str(store))
        assert removed >= 1  # просроченный .enc удалён
        assert not (store / f"{sid_exp}.enc").exists()
        # .txt просроченной сессии удалён вместе с её .enc
        assert not (store / f"{sid_exp}.txt").exists(), "B6-фикс: сайдкар просроченной сессии должен удаляться"
        # .txt живой сессии не тронут (её .enc не истёк)
        assert (store / f"{sid}.txt").exists(), "сайдкар непросроченной сессии не должен удаляться"


# --------------------------------------------------------------------------- #
# Последовательность (PASS): encrypt -> delete -> decrypt старым id
# --------------------------------------------------------------------------- #
class TestEncryptDeleteDecryptSequence:
    def test_decrypt_after_delete_reports_not_found(self, tmp_path):
        home = tmp_path / "home"
        doc = tmp_path / "c.txt"
        doc.write_text("ООО Ромашка", encoding="utf-8")
        sid = _run(["encrypt", str(doc)], home).stdout.strip()
        _run(["delete", sid], home)

        # пользователь скопировал session_id заранее и пытается расшифровать после delete
        result = _run(["decrypt", sid], home, stdin_text="ответ без токенов [ORG_1]")
        assert result.returncode == 1
        assert "не найдена" in result.stderr


# --------------------------------------------------------------------------- #
# Грязный ответ LLM (PASS): токеноподобный текст в JSON/коде
# --------------------------------------------------------------------------- #
class TestDirtyLlmResponseRobust:
    def test_json_array_not_mistaken_for_tokens(self, tmp_path):
        store = tmp_path / "sessions"
        store.mkdir()
        sid = save_session([_entity("[ORG_1]", "ООО Ромашка")], storage_dir=str(store))
        llm = 'code: orgs = ["ORG_1", "ORG_2"]\nТокен [ORG_1] и неизвестный [ORG_9].'
        restored, unresolved = detokenize(llm, sid, storage_dir=str(store))
        # массив в кавычках не тронут
        assert '["ORG_1", "ORG_2"]' in restored
        # настоящий токен восстановлен
        assert "ООО Ромашка" in restored
        # неизвестный — в unresolved
        assert unresolved == ["[ORG_9]"]
