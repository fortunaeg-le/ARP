"""Этап U1 (упаковка) — тесты лаунчера/self-check, не связанные с детекцией.

Изоляция: как везде в проекте, USERPROFILE/HOME подменяются на tmp_path, чтобы
launcher.lock и хранилище сессий не трогали реальный профиль пользователя.
"""
import json
import os
import socket
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "app")

if _APP not in sys.path:
    sys.path.insert(0, _APP)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


# --------------------------------------------------------------------------- #
# find_free_port — не хардкодит порт, реально проверяет занятость.
# --------------------------------------------------------------------------- #

def test_find_free_port_returns_preferred_when_free():
    import server

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()

    assert server.find_free_port(free_port) == free_port


def test_find_free_port_skips_busy_port():
    import server

    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    busy_port = busy.getsockname()[1]
    try:
        got = server.find_free_port(busy_port)
        assert got != busy_port
        # порт реально свободен — можно на него забиндиться
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", got))
        probe.close()
    finally:
        busy.close()


# --------------------------------------------------------------------------- #
# procutil — жив ли PID / принудительное завершение.
# --------------------------------------------------------------------------- #

def test_procutil_is_pid_alive_for_self_and_for_bogus_pid():
    import procutil

    assert procutil.is_pid_alive(os.getpid()) is True
    # PID вряд ли существующий (максимум на Windows — около 2^22)
    assert procutil.is_pid_alive(10**7) is False


def test_procutil_kill_pid_terminates_process():
    import procutil

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert procutil.is_pid_alive(proc.pid) is True
        assert procutil.kill_pid(proc.pid) is True
        proc.wait(timeout=5)
        assert procutil.is_pid_alive(proc.pid) is False
    finally:
        if proc.poll() is None:
            proc.kill()


# --------------------------------------------------------------------------- #
# launcher — файл-замок: мёртвый PID подчищается, живой-но-не-отвечающий убивается.
# --------------------------------------------------------------------------- #

def test_stale_lock_with_dead_pid_is_cleaned_silently(isolated_home):
    import importlib
    import launcher
    importlib.reload(launcher)

    lock = launcher._lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 10**7, "port": 8765, "build_mark": "x"}), encoding="utf-8")

    assert launcher._handle_stale_instance() is False
    assert not lock.exists()


def test_stale_lock_with_alive_but_unresponsive_pid_is_killed(isolated_home):
    """Была история с зомби-процессом сервера (старый код молча слушал вместо
    нового). Живой PID, чей порт никто не слушает, — тот же класс дефекта:
    лаунчер обязан завершить его, а не считать сервер работающим."""
    import importlib
    import launcher
    importlib.reload(launcher)

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()  # порт свободен и точно никто на нём не слушает

        lock = launcher._lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": proc.pid, "port": dead_port, "build_mark": "x"}),
            encoding="utf-8",
        )

        assert launcher._handle_stale_instance() is False
        proc.wait(timeout=5)
        assert proc.poll() is not None, "зомби-процесс должен быть принудительно завершён"
        assert not lock.exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stale_lock_with_live_responding_process_is_left_alone(isolated_home, monkeypatch):
    """Живой процесс, отвечающий на /api/ping, — легитимный второй запуск
    (двойной клик по ярлыку, пока приложение уже открыто): лаунчер не должен
    поднимать второй сервер и не должен убивать первый."""
    import importlib
    import launcher
    import procutil
    importlib.reload(launcher)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)

    server_script = os.path.join(_APP, "server.py")
    env = dict(os.environ, USERPROFILE=str(isolated_home), HOME=str(isolated_home),
               SHIFRATOR_UI_PORT="0")
    # Порт 0 без реального free-port подбора недостаточен для server.py напрямую
    # (main() уже делает find_free_port сам) — найдём свободный порт и укажем его.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env["SHIFRATOR_UI_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, server_script], env=env, cwd=_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        import time
        import urllib.request

        ok = False
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.5) as r:
                    json.loads(r.read().decode("utf-8"))
                    ok = True
                    break
            except Exception:
                time.sleep(0.1)
        assert ok, "сервер не поднялся вовремя"

        lock = launcher._lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": proc.pid, "port": port, "build_mark": "x"}),
            encoding="utf-8",
        )

        assert launcher._handle_stale_instance() is True
        assert procutil.is_pid_alive(proc.pid) is True, "легитимный процесс не должен быть убит"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
