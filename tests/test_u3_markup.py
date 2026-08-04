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
# ЭТАП U5b — контракт четырёх случаев по числу пересечённых масок
# (docs/archive/reports/U5A_REPORT.md, вопрос 4; постановка U5b, задача 1/4). Старый тест
# проверял «любое пересечение — отказ»; по новому контракту:
#   * /api/markup/mark-missed остаётся отказом при ЛЮБОМ пересечении — этот
#     путь предназначен для ЧИСТОГО текста, маршрутизацию «это правка границы»
#     делает клиент ДО отправки (выбирая /api/markup/replace вместо этого);
#     сообщение теперь называет, КАКИЕ маски задеты (тип и токен);
#   * пересечение РОВНО ОДНОЙ маски — законная правка границы через
#     /api/markup/replace: тип наследуется, kind="boundary", ошибки нет;
#   * пересечение ДВУХ И БОЛЕЕ остаётся отказом (в любом обходе) — с тем же
#     именующим сообщением.
# --------------------------------------------------------------------------- #

def test_missed_overlapping_existing_mask_is_rejected(live_server):
    """mark-missed (путь "чистый текст") остаётся отказом при пересечении —
    и с ОДНОЙ маской, и с ДВУМЯ; сообщение называет тип и токен задетых масок,
    а не общую фразу «пересекает маску»."""
    base, home = live_server
    text = "Email: a@example.com и b@example.com здесь.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]
    reps = result["replacements"]
    assert len(reps) == 2 and all(r["entity_type"] == "EMAIL" for r in reps)

    import re
    matches = list(re.finditer(
        r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)" data-token="([^"]+)"',
        result["anon_html"]))
    assert len(matches) == 2, result["anon_html"]
    (seg1, s1, e1, tok1), (seg2, s2, e2, tok2) = [
        (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)) for m in matches
    ]

    # РОВНО ОДНА маска (сдвиг на 1 символ внутрь первой) — по-прежнему отказ
    # через ЭТОТ endpoint, сообщение называет её тип и токен.
    r1 = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": seg1, "start": s1 + 1, "end": e1 + 1,
        "entity_type": "EMAIL",
    })
    assert r1["status"] == "error"
    assert tok1 in r1["message"]
    assert "пересекает" in r1["message"]

    # ДВЕ И БОЛЕЕ (диапазон, задевающий обе маски) — отказ, называет ОБЕ.
    r2 = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": session_id, "segment_id": seg1, "start": s1, "end": e2,
        "entity_type": "EMAIL",
    })
    assert r2["status"] == "error"
    assert tok1 in r2["message"] and tok2 in r2["message"]

    # ни одна ошибочная попытка не сохранилась как запись разметки.
    listed = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert listed["entries"] == []


def test_single_mask_overlap_is_legal_boundary_edit_via_replace(live_server):
    """Новый контракт: то же пересечение РОВНО ОДНОЙ маски, отправленное через
    /api/markup/replace (как это теперь делает клиент), — законная правка
    границы: тип наследуется, kind="boundary", ошибки нет."""
    base, home = live_server
    text = "Email: a@example.com и b@example.com здесь.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]

    import re
    matches = list(re.finditer(
        r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)" data-token="([^"]+)"',
        result["anon_html"]))
    (seg1, s1, e1, tok1), (_seg2, s2, e2, tok2) = [
        (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)) for m in matches
    ]

    # расширяем границу первой маски на 1 символ вправо (в соседний литерал).
    rep = _req(base, "POST", "/api/markup/replace", body={
        "session_id": session_id, "old_token": tok1, "old_segment_id": seg1,
        "old_start": s1, "old_end": e1,
        "segment_id": seg1, "start": s1, "end": e1 + 1, "entity_type": "EMAIL",
    })
    assert rep["status"] == "ok", rep
    assert rep["kind"] == "boundary"

    listed = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert len(listed["entries"]) == 1
    assert listed["entries"][0]["kind"] == "boundary"


def test_boundary_edit_end_to_end_provenance_and_report(live_server):
    """U5A-5: сквозной путь — от правки границы (эмулирует клиентский расчёт
    диапазона, ровно те же координаты, что вычислил бы computeSelectionInfo)
    до записи kind="boundary" на диске и до строки отчёта. Заодно закрывает
    U5A-1: ВТОРАЯ правка границы ТОЙ ЖЕ (уже раз исправленной) маски обязана
    видеть провенанс РЕАЛЬНОГО детектора, а не "manual" — иначе повторная
    правка увела бы ошибку движка в строку «ручная маска» (вклад программы
    стало бы не отделить от вклада человека). И задача 3: направление правки
    (недобор/перебор) видно в totals отчёта."""
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

    # 1-я правка: сужаем на 1 символ справа — старая маска была ДЛИННЕЕ новой
    # (перебор, читаемость).
    rep1 = _req(base, "POST", "/api/markup/replace", body={
        "session_id": session_id, "old_token": token, "old_segment_id": seg,
        "old_start": old_start, "old_end": old_end,
        "segment_id": seg, "start": old_start, "end": old_end - 1, "entity_type": "PHONE",
    })
    assert rep1["status"] == "ok" and rep1["kind"] == "boundary", rep1

    listed1 = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    original_detector = listed1["entries"][0]["old_detector"]
    assert original_detector == "regex", listed1   # PHONE — regex-детектор (entity_types.yaml)

    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok", applied

    # 2-я правка ТОЙ ЖЕ, уже раз исправленной маски — расширяем обратно
    # (старая маска на этот раз была КОРОЧЕ новой — недобор, риск утечки).
    m2 = re.search(r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)" data-token="([^"]+)"',
                   applied["anon_html"])
    seg2, s2, e2, tok2 = m2.group(1), int(m2.group(2)), int(m2.group(3)), m2.group(4)
    rep2 = _req(base, "POST", "/api/markup/replace", body={
        "session_id": session_id, "old_token": tok2, "old_segment_id": seg2,
        "old_start": s2, "old_end": e2,
        "segment_id": seg2, "start": s2, "end": e2 + 1, "entity_type": "PHONE",
    })
    assert rep2["status"] == "ok" and rep2["kind"] == "boundary", rep2

    listed2 = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    second_entry = next(e for e in listed2["entries"] if e["id"] == rep2["markup_id"])
    # U5A-1, ГЛАВНАЯ ПРОВЕРКА: провенанс НЕ стёрт в "manual" повторной правкой.
    assert second_entry["old_detector"] == "regex", listed2

    applied2 = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied2["status"] == "ok", applied2
    restored = _req(base, "POST", "/api/decrypt",
                     body={"session_id": session_id, "text": applied2["anon_text"]})
    assert restored["status"] == "ok" and restored["unresolved"] == []
    assert "+7 999 123 45 67" in restored["restored"]

    # Строка отчёта: две правки границы, направление верно посчитано, обе
    # атрибутированы РЕАЛЬНОМУ детектору (regex), не "manual".
    d = _req(base, "GET", f"/api/report?scope=session&session_id={session_id}")
    assert d["status"] == "ok", d
    t = d["report"]["totals"]
    assert t["boundary"] == 2
    assert t["boundary_was_shorter"] == 1
    assert t["boundary_was_longer"] == 1
    assert d["report"]["by_detector"].get("regex", {}).get("boundary") == 2
    assert d["report"]["by_detector"].get("manual", {}).get("boundary", 0) == 0


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
    # ЭТАП S1: разметка — отдельный от сессий актив, живёт в соседней директории
    # ("markup"), не внутри storage_dir сессии (см. storage.py §ЭТАП S1).
    markup_store = store.parent / "markup"
    assert str(home) in str(markup_store)
    assert (markup_store / f"{session_id}.markup.json").exists()

    # gitignore repo-wide: ~/.shifrator/ паттерн покрывает любую such-директорию
    gitignore = (Path(_ROOT) / ".gitignore").read_text(encoding="utf-8")
    assert ".shifrator/" in gitignore


# --------------------------------------------------------------------------- #
# ЭТАП U5b — «Просмотрено, правок нет» (решение владельца, находка U5A-6).
# --------------------------------------------------------------------------- #

def test_mark_reviewed_creates_applied_entry_that_can_be_undone(live_server):
    base, home = live_server
    text = "Совсем обычный текст без ПДн.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]

    r = _req(base, "POST", "/api/markup/reviewed", body={"session_id": session_id})
    assert r["status"] == "ok", r
    markup_id = r["markup_id"]

    listed = _req(base, "GET", f"/api/markup/list?session_id={session_id}")
    assert len(listed["entries"]) == 1
    entry = listed["entries"][0]
    assert entry["kind"] == "reviewed"
    # applied=True сразу — apply_pending_markup нечего применять к документу.
    assert entry["applied"] is True

    # "Применить правки" не ломается на записи без координат/значения.
    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": session_id})
    assert applied["status"] == "ok", applied
    assert applied["results"] == []   # applied=True с самого начала — не "pending"

    # отменить случайный клик можно, как любую правку.
    deleted = _req(base, "POST", "/api/markup/delete",
                   body={"session_id": session_id, "markup_id": markup_id})
    assert deleted["status"] == "ok" and deleted["deleted"] is True


def test_reviewed_document_enters_report_with_zero_edits_and_full_denominator(live_server):
    """Находка U5A-6: без «Просмотрено» документ без единой правки в отчёт не
    попадает вовсе — с ним попадает, с ПОЛНЫМ знаменателем движка и НУЛЁМ
    правок (то есть идеальными precision/recall для этого документа)."""
    base, home = live_server
    text = "Контакт: ivan.petrov@example.com для связи.\n"
    result = _encrypt_sync(base, text)
    session_id = result["session_id"]
    masks_in_document = result["entities_total"]
    assert masks_in_document > 0

    # без отметки документ отсутствует в отчёте (докстринг report.py §Откуда
    # берётся знаменатель / docs/archive/reports/U5A_REPORT.md вопрос 6).
    before = _req(base, "GET", f"/api/report?scope=session&session_id={session_id}")
    assert before["report"]["sample"]["documents"] == 0

    r = _req(base, "POST", "/api/markup/reviewed", body={"session_id": session_id})
    assert r["status"] == "ok", r

    d = _req(base, "GET", f"/api/report?scope=session&session_id={session_id}")
    assert d["status"] == "ok", d
    rep = d["report"]
    assert rep["sample"]["documents"] == 1
    assert rep["sample"]["reviewed_no_edits"] == 1
    assert rep["sample"]["edits"] == 0
    assert rep["totals"]["masks_total"] == masks_in_document
    assert rep["metrics"]["precision_reviewed_pct"] == 100.0
    assert rep["metrics"]["recall_reviewed_pct"] == 100.0
    assert rep["findings"] == []   # не правка — не находка
    assert "Отмечено «просмотрено, правок нет»: 1" in d["md_text"]
