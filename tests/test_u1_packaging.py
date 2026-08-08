"""Этап U1 (упаковка) — тесты лаунчера/self-check, не связанные с детекцией.

Изоляция: как везде в проекте, USERPROFILE/HOME подменяются на tmp_path, чтобы
launcher.lock и хранилище сессий не трогали реальный профиль пользователя.
"""
import ast
import json
import os
import re
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


# --------------------------------------------------------------------------- #
# Спека PyInstaller обязана знать про КАЖДЫЙ модуль app/.
#
# Найдено этапом U4: новый `app/report.py` импортируется ЛЕНИВО (внутри функций
# core/server), в hiddenimports его не было — в собранном exe экран «Аналитика»
# отвалился бы, а `core._markup_census` (он ловит Exception) молча перестал бы
# писать слепок знаменателя метрик. То есть отказ был бы ТИХИМ. Обычный прогон
# тестов идёт из исходников и такого не видит вовсе; поэтому проверяем спеку
# как данные, а не собираем exe.
#
# ЭТАП AUDIT (находка `INSTR-PKGSPEC-SUBSTRING`). Оба теста ниже проверяли
# `f'"{m}"' not in spec` — то есть присутствие имени в кавычках ГДЕ УГОДНО в
# тексте спеки: в комментарии, в `excludes`, в `datas`, в пути. Заявлено было
# «модуль в hiddenimports». Имя, попавшее в спеку любой другой строкой, красило
# тест зелёным, а сборка теряла модуль — ровно тот класс тихой деградации,
# ради которого тест и заведён. Теперь `hiddenimports` разбирается КАК СПИСОК.
# --------------------------------------------------------------------------- #

def _spec_hiddenimports():
    """Список `hiddenimports` из `packaging/shifrator.spec`, разобранный как
    данные, а не как подстрока.

    Спека — исполняемый python, но присваивание `hiddenimports = [...]` в ней
    литеральное, поэтому срез до закрывающей скобки читается `ast.literal_eval`
    (комментарии внутри списка ему не мешают). Если литерал перестанет быть
    литералом, тест обязан упасть, а не молча перейти на слабую проверку.
    """
    spec_path = os.path.join(_ROOT, "packaging", "shifrator.spec")
    spec = open(spec_path, encoding="utf-8").read()
    m = re.search(r"^hiddenimports\s*=\s*\[", spec, re.MULTILINE)
    assert m, "в packaging/shifrator.spec нет присваивания hiddenimports = [...]"
    start = m.end() - 1
    depth, end = 0, None
    for i in range(start, len(spec)):
        if spec[i] == "[":
            depth += 1
        elif spec[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end, "не найдена закрывающая скобка списка hiddenimports"
    try:
        names = ast.literal_eval(spec[start:end])
    except (ValueError, SyntaxError) as exc:                  # pragma: no cover
        raise AssertionError(
            "hiddenimports перестал быть литеральным списком (%s) — проверка "
            "спеки как данных больше не работает, чинить надо её, а не тест" % exc
        )
    assert isinstance(names, list) and all(isinstance(n, str) for n in names)
    return set(names)


def test_pyinstaller_spec_lists_every_app_module():
    listed = _spec_hiddenimports()
    app_modules = {
        os.path.splitext(name)[0]
        for name in os.listdir(_APP)
        if name.endswith(".py") and name != "launcher.py"   # launcher — точка входа
    }
    missing = sorted(app_modules - listed)
    assert not missing, (
        f"модули app/ отсутствуют в hiddenimports спеки PyInstaller: {missing}. "
        "В собранном exe они не окажутся, а ленивый импорт внутри функции даст "
        "тихую деградацию вместо явной ошибки."
    )


def test_spec_module_check_is_not_a_substring_search():
    """Само-проверка прибора (находка `INSTR-PKGSPEC-SUBSTRING`).

    Имя, присутствующее в спеке ВНЕ `hiddenimports`, не должно считаться
    перечисленным. Берём заведомо существующую в тексте спеки строку, которой
    в списке нет, — если она «нашлась», проверка снова стала подстроковой.
    """
    listed = _spec_hiddenimports()
    spec = open(os.path.join(_ROOT, "packaging", "shifrator.spec"),
                encoding="utf-8").read()
    outsider = "index.html"                    # лежит в datas, модулем не является
    assert f'"{outsider}"' in spec, "фикстура устарела: строки нет в спеке вовсе"
    assert outsider not in listed, (
        "проверка спеки снова ищет подстроку по всему файлу вместо разбора "
        "hiddenimports — это находка INSTR-PKGSPEC-SUBSTRING, она закрыта"
    )


# --------------------------------------------------------------------------- #
# ЭТАП U1b. Тот же класс отказа для src/: модуль детекции, попавший в проект
# после прошлой сборки, обязан быть в спеке. Зеркало проверки, которую спека с
# этого этапа делает и сама (`_assert_every_local_module_listed`) — здесь она
# ловится обычным прогоном тестов, до того как кто-то запустит pyinstaller.
# --------------------------------------------------------------------------- #

def test_pyinstaller_spec_lists_every_src_module():
    listed = _spec_hiddenimports()
    src_modules = {
        os.path.splitext(name)[0]
        for name in os.listdir(os.path.join(_ROOT, "src"))
        if name.endswith(".py")
    }
    missing = sorted(src_modules - listed)
    assert not missing, (
        f"модули src/ отсутствуют в hiddenimports спеки PyInstaller: {missing}"
    )


# --------------------------------------------------------------------------- #
# ЭТАП U1b: версии зависимостей поставки зафиксированы ТОЧНО, не диапазонами.
# Численное окружение и версии моделей влияют на результат детекции — у клиента
# должно стоять ровно то, на чём мы мерили.
# --------------------------------------------------------------------------- #

_LOCK_CRITICAL = (
    "natasha", "navec", "slovnet", "razdel", "yargy", "pymorphy2",
    "pymorphy2-dicts-ru", "numpy", "lxml", "cryptography", "python-docx",
    "pyyaml",
)


def _read_lock():
    path = os.path.join(_ROOT, "packaging", "requirements-build.lock.txt")
    pins = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"нефиксированная версия в lock-файле: {line!r}"
        name, ver = line.split("==", 1)
        pins[name.strip().lower().replace("_", "-")] = ver.strip()
    return pins


def test_build_lock_pins_every_critical_package_exactly():
    pins = _read_lock()
    missing = sorted(p for p in _LOCK_CRITICAL if p not in pins)
    assert not missing, f"нет точного пина для критичных пакетов сборки: {missing}"


def test_build_lock_matches_installed_environment():
    """Lock, разошедшийся с окружением, — документ, а не пин."""
    from importlib.metadata import PackageNotFoundError, version

    pins = _read_lock()
    mismatched = []
    for name in _LOCK_CRITICAL:
        try:
            have = version(name)
        except PackageNotFoundError:
            mismatched.append(f"{name}: не установлен")
            continue
        if have != pins[name]:
            mismatched.append(f"{name}: установлено {have}, в lock {pins[name]}")
    assert not mismatched, (
        "окружение разошлось с packaging/requirements-build.lock.txt: "
        + "; ".join(mismatched)
    )
