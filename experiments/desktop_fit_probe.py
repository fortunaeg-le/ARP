"""ЭТАП DESKTOP-FIT — живая проба окна приложения (разведочный скрипт).

Проверяет НА РЕАЛЬНОМ Edge, что `appwindow.open` даёт окно приложения, а не
вкладку: поднимает настоящий сервер, открывает окно, меряет по CDP разницу
outerHeight - innerHeight (высота обвязки браузера) и наличие вкладок, затем
гасит всё через appwindow.close_all() и проверяет, что зомби не осталось.

Запуск: venv/Scripts/python.exe experiments/desktop_fit_probe.py
Окно живёт несколько секунд и закрывается само.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import appwindow  # noqa: E402
import procutil  # noqa: E402
import server as server_mod  # noqa: E402
import ui_circle  # noqa: E402


def _eval(ws, expr):
    """Runtime.evaluate поверх сырого CDP-сокета (ui_circle._WebSocket умеет
    только send/recv — класс Browser там завязан на свой headless-запуск)."""
    import json as _json

    ws.send(_json.dumps({"id": 1, "method": "Runtime.evaluate",
                         "params": {"expression": expr, "returnByValue": True}}))
    while True:
        msg = _json.loads(ws.recv())
        if msg.get("id") == 1:
            return msg


def measure(dbg_port):
    deadline = time.time() + 30
    targets = []
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{dbg_port}/json/list", timeout=1) as r:
                targets = [t for t in json.loads(r.read().decode("utf-8"))
                           if t.get("type") == "page"]
            if targets:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    if not targets:
        raise SystemExit("ПРОБА УПАЛА: браузер не отдал ни одной страницы по CDP")

    print(f"страниц открыто: {len(targets)}  (вкладок быть не должно, ровно 1)")
    print(f"адрес страницы:  {targets[0]['url']}")

    ws = ui_circle._WebSocket(targets[0]["webSocketDebuggerUrl"])
    try:
        res = _eval(ws, "JSON.stringify({inner: window.innerHeight, outer: window.outerHeight,"
                        " w: window.innerWidth, ow: window.outerWidth})")
        data = json.loads(res["result"]["result"]["value"])
    finally:
        ws.close()
    return len(targets), data


def _profile_procs() -> int:
    """Сколько процессов браузера живёт на НАШЕМ профиле (чужой Edge не считаем
    и не трогаем). Только одинарные кавычки: двойные внутри -Command мешаются."""
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedge.exe' "
           "-and $_.CommandLine -like '*window-profile*' } | Measure-Object | % Count")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True).stdout.strip()
    try:
        return int(out or 0)
    except ValueError:
        return -1


def _cdp_alive(dbg_port) -> bool:
    """Отвечает ли браузер по протоколу отладки — прямой признак, что он жив."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{dbg_port}/json/list", timeout=2) as r:
            r.read()
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    port = server_mod.find_free_port(server_mod.DEFAULT_PORT)
    httpd = server_mod.Server((server_mod.HOST, port), server_mod.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{server_mod.HOST}:{port}/"
    print(f"сервер поднят: {url}")

    dbg_port = ui_circle.free_port()
    real_popen = subprocess.Popen

    def popen_with_debug(args, **kwargs):
        # ТОЛЬКО браузеру. `appwindow.subprocess` — тот же объект модуля, что и
        # здесь, поэтому неразборчивая подмена ломала и `subprocess.run` внутри
        # самой пробы: в powershell и taskkill улетал лишний аргумент, и прибор
        # показывал «процессов 0» при живом окне.
        args = list(args)
        if str(args[0]).lower().endswith(("msedge.exe", "chrome.exe")):
            args.append(f"--remote-debugging-port={dbg_port}")
        return real_popen(args, **kwargs)

    appwindow.subprocess.Popen = popen_with_debug
    try:
        mode = appwindow.open(url)
        print(f"режим открытия: {mode}")
        if mode != "app":
            raise SystemExit("ПРОБА УПАЛА: окно приложения не поднялось")

        pages, geom = measure(dbg_port)
        chrome_h = geom["outer"] - geom["inner"]
        print(f"геометрия: {geom}")
        print(f"обвязка браузера по высоте: {chrome_h} px")
        print("ВЫВОД:", "окно приложения (без адресной строки и вкладок)"
              if chrome_h <= 80 and pages == 1 else
              "ПОХОЖЕ НА ВКЛАДКУ — обвязка слишком высокая")

        before = _profile_procs()
        print(f"процессов браузера на нашем профиле ДО закрытия: {before}"
              f"   отвечает по CDP: {_cdp_alive(dbg_port)}")
        assert before > 0, "прибор сломан: окно открыто, а процессов не видно"

        appwindow.close_all()
        time.sleep(2.0)
        left = _profile_procs()
        print(f"после close_all осталось: {left} (должно быть 0)"
              f"   отвечает по CDP: {_cdp_alive(dbg_port)}")
        print("ЗОМБИ:", "нет" if left == 0 else "ЕСТЬ — окно пережило программу")
    finally:
        appwindow.subprocess.Popen = real_popen
        appwindow.close_all()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
