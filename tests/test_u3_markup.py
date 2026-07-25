"""Этап U3 (экран проверки и разметка) — живые HTTP-тесты, не моки.

Как test_u1_packaging.py: реальный `app/server.py` в отдельном процессе,
USERPROFILE/HOME подменены на tmp_path (сессии/разметка не трогают профиль
разработчика). Детекция не тронута — эти тесты проверяют НОВОЕ (ручная
разметка, пересборка, round-trip с ручными масками), не детекцию.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "app")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home), SHIFRATOR_UI_PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_APP, "server.py")], env=env, cwd=_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        ok = False
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/api/ping", timeout=0.5) as r:
                    json.loads(r.read().decode("utf-8"))
                    ok = True
                    break
            except Exception:
                time.sleep(0.1)
        assert ok, "сервер не поднялся вовремя"
        yield base, home
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _req(base, method, path, body=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _encrypt_sync(base, text: str, filename="doc.txt"):
    """POST /api/encrypt/start + опрос статуса до готовности; возвращает result."""
    started = _req(base, "POST", "/api/encrypt/start", body=text.encode("utf-8"),
                    headers={"X-Filename": filename, "X-Allow-Lossy": "0",
                             "Content-Type": "application/octet-stream"})
    assert started["status"] == "ok", started
    job_id = started["job_id"]
    for _ in range(200):
        st = _req(base, "GET", f"/api/encrypt/status?job_id={job_id}")
        assert st["status"] != "error", st
        if st["status"] == "done":
            assert st.get("segment_count") is not None
            assert st["percent"] == 100
            return st["result"]
        time.sleep(0.1)
    raise AssertionError("encrypt job не завершился вовремя")


# --------------------------------------------------------------------------- #
# Задача 1/2 приёмки: полный сценарий — пометить пропущенное, применить,
# скачать, восстановить.
# --------------------------------------------------------------------------- #

def test_mark_missed_apply_and_roundtrip(live_server):
    base, home = live_server
    missed_word = "СЕКРЕТНОЕСЛОВОXYZ"
    text = "Просто фраза без реквизитов.\n" + missed_word + "\nЕщё одна строка.\n"
    result = _encrypt_sync(base, text)
    assert result["status"] == "ok"
    session_id = result["session_id"]
    # слово НЕ распознано автоматической детекцией (обычное слово, не ПДн-паттерн)
    assert missed_word in result["anon_text"]

    seg_id = "l1"  # вторая строка (0-based) — только это слово
    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": seg_id, "start": 0, "end": len(missed_word),
        "entity_type": "PERSON",
    })
    assert r["status"] == "ok", r
    assert r["value"] == missed_word

    listed = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert listed["status"] == "ok"
    assert len(listed["entries"]) == 1
    assert listed["entries"][0]["applied"] is False
    assert listed["entries"][0]["kind"] == "missed"

    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok", applied
    assert applied["results"][0]["applied"] is True
    assert missed_word not in applied["anon_text"], "документ обязан пересобраться с новой маской"
    assert "[PERSON_" in applied["anon_text"]

    # round-trip: восстановленный текст обязан содержать исходное слово обратно
    restored = _req(base, "POST", "/api/decrypt",
                     body={"session_id": session_id, "text": applied["anon_text"]})
    assert restored["status"] == "ok", restored
    assert missed_word in restored["restored"]
    assert restored["unresolved"] == []

    # разметка теперь показывает applied=True
    listed2 = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert listed2["entries"][0]["applied"] is True


# --------------------------------------------------------------------------- #
# Задача 2 приёмка: сохраняется/читается/редактируется/удаляется.
# --------------------------------------------------------------------------- #

def test_markup_crud(live_server):
    base, home = live_server
    text = "Обычный текст.\nВТОРОЕСЛОВО\nТретья строка.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]

    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": "l1", "start": 0, "end": len("ВТОРОЕСЛОВО"),
        "entity_type": "ORG",
    })
    markup_id = r["markup_id"]

    # редактирование типа ДО применения
    upd = _req(base, "POST", "/api/markup/update", body={
        "session_id": session_id, "markup_id": markup_id, "entity_type": "PERSON",
    })
    assert upd["status"] == "ok", upd
    listed = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert listed["entries"][0]["entity_type"] == "PERSON"

    # удаление
    deleted = _req(base, "POST", "/api/markup/delete", body={
        "session_id": session_id, "markup_id": markup_id,
    })
    assert deleted["status"] == "ok"
    assert deleted["deleted"] is True
    listed2 = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert listed2["entries"] == []

    # применение пустого списка правок — не падает, ничего не делает
    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok"
    assert applied["results"] == []


# --------------------------------------------------------------------------- #
# Главный риск задачи (постановка, п.2 приёмки): round-trip с РУЧНЫМИ масками
# — снятие ложной маски, исправление типа, исправление границы, автоматические
# маски не пострадали. Плюс пересечение: ручная поверх автоматической.
# --------------------------------------------------------------------------- #

def test_false_positive_unmask_roundtrip(live_server):
    base, home = live_server
    # email детектируется автоматически; "НЕПДН" рядом — обычное слово (не ПДн)
    text = "Контакт: ivan.petrov@example.com для связи.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]
    reps = result["replacements"]
    assert len(reps) == 1 and reps[0]["entity_type"] == "EMAIL"

    # найдём координаты вхождения через anon_html data-атрибуты
    import re
    m = re.search(r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)" data-token="([^"]+)"',
                  result["anon_html"])
    assert m, result["anon_html"]
    seg, start, end, token = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)

    fp = _req(base, "POST", "/api/markup/false-positive", body={
        "session_id": session_id, "segment_id": seg, "start": start, "end": end, "token": token,
    })
    assert fp["status"] == "ok", fp

    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok", applied
    assert applied["results"][0]["applied"] is True
    assert "ivan.petrov@example.com" in applied["anon_text"], "снятая маска должна вернуть исходный текст"
    assert applied["values_hidden"] == 0

    restored = _req(base, "POST", "/api/decrypt",
                     body={"session_id": session_id, "text": applied["anon_text"]})
    assert restored["status"] == "ok"
    assert restored["unresolved"] == []


def test_boundary_and_type_fix_roundtrip(live_server):
    base, home = live_server
    text = "Позвоните: +7 999 123 45 67 срочно.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]
    reps = result["replacements"]
    assert len(reps) == 1 and reps[0]["entity_type"] == "PHONE"

    import re
    m = re.search(r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)" data-token="([^"]+)"',
                  result["anon_html"])
    seg, old_start, old_end, token = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)

    # "поправить тип" — тот же диапазон, другой тип
    rep = _req(base, "POST", "/api/markup/replace", body={
        "session_id": session_id, "old_token": token, "old_segment_id": seg,
        "old_start": old_start, "old_end": old_end,
        "segment_id": seg, "start": old_start, "end": old_end, "entity_type": "SUM",
    })
    assert rep["status"] == "ok", rep
    assert rep["kind"] == "type"

    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok", applied
    assert applied["replacements"][0]["entity_type"] == "SUM"

    restored = _req(base, "POST", "/api/decrypt",
                     body={"session_id": session_id, "text": applied["anon_text"]})
    assert restored["status"] == "ok"
    assert "+7 999 123 45 67" in restored["restored"]
    assert restored["unresolved"] == []


def test_manual_mask_does_not_disturb_other_automatic_masks(live_server):
    """Автоматические маски НЕ должны пострадать от ручной правки другого вхождения."""
    base, home = live_server
    missed_word = "ПРОПУСКСЛОВО"
    text = "Email: a@example.com Phone: +7 999 111 22 33\n" + missed_word + "\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]
    reps_before = {r["entity_type"] for r in result["replacements"]}
    assert reps_before == {"EMAIL", "PHONE"}

    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": "l1", "start": 0, "end": len(missed_word),
        "entity_type": "ORG",
    })
    assert r["status"] == "ok"
    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok"
    types_after = {r["entity_type"] for r in applied["replacements"]}
    assert types_after == {"EMAIL", "PHONE", "ORG"}

    restored = _req(base, "POST", "/api/decrypt",
                     body={"session_id": session_id, "text": applied["anon_text"]})
    assert restored["status"] == "ok"
    assert "a@example.com" in restored["restored"]
    assert "+7 999 111 22 33" in restored["restored"]
    assert missed_word in restored["restored"]
    assert restored["unresolved"] == []


# --------------------------------------------------------------------------- #
# Пересечение: ручная маска пытается частично накрыть автоматическую —
# поведение должно быть ОПРЕДЕЛЁННЫМ (явная ошибка), не случайным/тихим.
# --------------------------------------------------------------------------- #

def test_missed_overlapping_existing_mask_is_rejected(live_server):
    base, home = live_server
    text = "Email: a@example.com здесь.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]

    import re
    m = re.search(r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)"', result["anon_html"])
    seg, start, end = m.group(1), int(m.group(2)), int(m.group(3))

    # выделение, частично накрывающее существующую маску (сдвиг на 1 символ внутрь)
    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": seg, "start": start + 1, "end": end + 3,
        "entity_type": "EMAIL",
    })
    assert r["status"] == "error"
    assert "пересекает" in r["message"]

    # список правок пуст — ошибочная попытка не должна была сохраниться как запись
    listed = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert listed["entries"] == []


# --------------------------------------------------------------------------- #
# Ошибочные сценарии (задача 5 приёмки): человеческие сообщения, не крэш.
# --------------------------------------------------------------------------- #

def test_error_scenarios_are_human_readable(live_server):
    base, home = live_server
    text = "Просто текст.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]

    # выделение нулевой длины
    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": "l0", "start": 3, "end": 3, "entity_type": "PERSON",
    })
    assert r["status"] == "error"
    assert "трейсбек" not in r["message"].lower() and "traceback" not in r["message"].lower()

    # неизвестный тип
    r2 = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": "l0", "start": 0, "end": 4, "entity_type": "NOT_A_TYPE",
    })
    assert r2["status"] == "error"
    assert "Неизвестный тип" in r2["message"]

    # правка после удаления сессии
    delr = _req(base, "POST", "/api/session-delete", body={"session_id": session_id})
    assert delr["status"] == "ok"
    r3 = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": "l0", "start": 0, "end": 4, "entity_type": "PERSON",
    })
    assert r3["status"] == "error"
    assert "не найдена" in r3["message"].lower()


# --------------------------------------------------------------------------- #
# Задача 4 приёмки: путь хранения — профиль пользователя, не проект.
# --------------------------------------------------------------------------- #

def test_markup_and_doc_sidecars_live_under_home_not_repo(live_server):
    base, home = live_server
    text = "Слово раз.\nВТОРОЕСЛОВО\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]
    storage_dir = result["storage_dir"]

    assert str(home) in storage_dir
    assert _ROOT not in storage_dir

    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": "l1", "start": 0, "end": 11, "entity_type": "ORG",
    })
    assert r["status"] == "ok"

    from pathlib import Path
    store = Path(storage_dir)
    assert (store / f"{session_id}.doc.json").exists()
    assert (store / f"{session_id}.markup.json").exists()

    # gitignore repo-wide: ~/.shifrator/ паттерн покрывает любую such-директорию
    gitignore = (Path(_ROOT) / ".gitignore").read_text(encoding="utf-8")
    assert ".shifrator/" in gitignore
