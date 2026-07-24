"""Лаунчер для конечного пользователя (этап U1) — точка входа собранного .exe.

Отличия от прямого `python app/server.py` (которым по-прежнему можно пользоваться
из командной строки для разработки):
  1. Self-check ДО поднятия сервера — при провале показывает человеческое окно
     с ошибкой, а не трейсбек (app/selfcheck.py).
  2. Файл-замок (`{сессии}/../launcher.lock`) — обнаруживает СВОЙ предыдущий
     процесс: если он жив и отвечает на /api/ping — просто открывает браузер на
     его порт и завершается (двойной клик не плодит второй сервер); если жив,
     но НЕ отвечает (завис/зомби) — принудительно завершает его перед стартом;
     если мёртв — молча убирает протухший замок.
  3. Порт подбирается динамически (server.find_free_port) — конфликт порта не
     мешает запуску.
  4. Окно статуса на tkinter (у пользователя нет терминала: --windowed exe не
     покажет консоль) — единственный видимый признак работающей программы и
     единственный способ её штатно закрыть. Закрытие окна = корректная
     остановка сервера, без зомби-процессов.
"""

import json
import os
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import procutil  # noqa: E402
import selfcheck  # noqa: E402
import server as server_mod  # noqa: E402
import core  # noqa: E402


def _lock_path():
    from storage import default_storage_dir
    return default_storage_dir().parent / "launcher.lock"


def _ping(port: int, timeout: float = 1.0):
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — любой сбой связи = "не отвечает"
        return None


def _handle_stale_instance():
    """Разбирает файл-замок предыдущего запуска. Возвращает True, если уже
    есть ЖИВОЙ и отвечающий процесс (в этом случае лаунчер просто открывает
    браузер на его адрес и завершается, не поднимая второй сервер)."""
    path = _lock_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data.get("pid", -1))
        port = int(data.get("port", -1))
    except (OSError, ValueError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return False

    if not procutil.is_pid_alive(pid):
        path.unlink(missing_ok=True)
        return False

    info = _ping(port)
    if info is not None:
        # Живой и отвечающий процесс — не второй сервер, просто открываем окно.
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return True

    # Процесс жив, но не отвечает — зомби (была история со старым сервером,
    # молча отвечавшим на запросы вместо нового). Завершаем его и продолжаем.
    procutil.kill_pid(pid)
    for _ in range(20):
        if not procutil.is_pid_alive(pid):
            break
        time.sleep(0.1)
    path.unlink(missing_ok=True)
    return False


def _write_lock(port: int):
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": os.getpid(), "port": port, "build_mark": core.BUILD_MARK}),
        encoding="utf-8",
    )


def _remove_lock():
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def _show_error_window(title: str, messages: list, log_path: str):
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title(title)
    root.geometry("560x360")
    tk.Label(root, text="Программу не удалось запустить", font=("Segoe UI", 13, "bold"),
              fg="#b32020").pack(pady=(16, 6))
    body = scrolledtext.ScrolledText(root, wrap="word", height=12, font=("Segoe UI", 10))
    body.insert("1.0", "\n\n".join(messages) + f"\n\nТехнический журнал: {log_path}")
    body.configure(state="disabled")
    body.pack(fill="both", expand=True, padx=16, pady=6)
    tk.Button(root, text="Закрыть", command=root.destroy, width=14).pack(pady=12)
    root.mainloop()


def _run_status_window(url: str, on_exit):
    import tkinter as tk

    root = tk.Tk()
    root.title("SHIFRATOR")
    root.geometry("480x220")
    tk.Label(root, text="SHIFRATOR запущен", font=("Segoe UI", 14, "bold")).pack(pady=(18, 4))
    tk.Label(root, text="Обработка идёт на этом компьютере, в сеть ничего не уходит.",
              font=("Segoe UI", 10)).pack()
    tk.Label(root, text=url, font=("Consolas", 11), fg="#2f6df6").pack(pady=8)
    tk.Label(root, text=f"сборка: {core.BUILD_MARK}", font=("Segoe UI", 9), fg="#5c6675").pack()
    tk.Label(
        root,
        text="Не закрывайте это окно, пока работаете.\nОкно браузера можно закрывать и открывать заново.",
        font=("Segoe UI", 9), fg="#5c6675", justify="center",
    ).pack(pady=10)

    def _open_again():
        webbrowser.open(url)

    def _exit():
        root.destroy()
        on_exit()

    btns = tk.Frame(root)
    btns.pack(pady=6)
    tk.Button(btns, text="Открыть окно снова", command=_open_again, width=18).pack(side="left", padx=6)
    tk.Button(btns, text="Выход", command=_exit, width=12).pack(side="left", padx=6)

    root.protocol("WM_DELETE_WINDOW", _exit)
    root.mainloop()


def main():
    if _handle_stale_instance():
        return

    report = selfcheck.run()
    if not report.ok:
        messages = [r.human_message for r in report.failures()]
        log_path = str(selfcheck._log_path())
        _show_error_window("SHIFRATOR — ошибка запуска", messages, log_path)
        sys.exit(1)

    port = server_mod.find_free_port(server_mod.DEFAULT_PORT)
    try:
        httpd = server_mod.Server((server_mod.HOST, port), server_mod.Handler)
    except OSError as exc:
        _show_error_window(
            "SHIFRATOR — ошибка запуска",
            [f"Не удалось запустить локальный сервер (порт {port}): {exc}"],
            str(selfcheck._log_path()),
        )
        sys.exit(1)

    _write_lock(port)
    url = f"http://{server_mod.HOST}:{port}/"

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    webbrowser.open(url)

    def _shutdown():
        httpd.shutdown()
        httpd.server_close()
        _remove_lock()
        sys.exit(0)

    _run_status_window(url, _shutdown)
    # Окно закрыто без штатного "Выход" (WM_DELETE_WINDOW тоже зовёт _shutdown,
    # так что сюда доходим и в обычном, и в ручном случае) — подчищаем ещё раз
    # на всякий случай перед выходом из процесса.
    _remove_lock()


if __name__ == "__main__":
    main()
