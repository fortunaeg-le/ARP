"""Локальный десктоп-интерфейс SHIFRATOR (стандартная библиотека, без сборки).

Запуск:  venv\\Scripts\\python.exe app\\server.py   (или двойной клик по app\\start.bat)

Поднимает HTTP-сервер ТОЛЬКО на 127.0.0.1 (в сеть ничего не уходит — это и
требование приватности ПДн, и аргумент пилота) и открывает браузер. Вся обработка
идёт в этом же интерпретаторе через app/core.py, который дёргает реальный конвейер
из src/. Никаких моков.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
from paths import app_root  # noqa: E402

print(f"[BUILD_MARK={core.BUILD_MARK}]", file=sys.stderr)

HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("SHIFRATOR_UI_PORT", "8765"))
_INDEX = os.path.join(app_root(), "app", "index.html")

_ALLOWED_EXT = (".docx", ".txt")
_MAX_UPLOAD = 50 * 1024 * 1024   # 50 МБ — договор столько не весит; защита от случайностей


def find_free_port(preferred: int, host: str = HOST, attempts: int = 50) -> int:
    """Возвращает preferred, если он свободен, иначе следующий свободный порт.

    Не хардкодит порт (был инцидент с портовым конфликтом на этапе UI-фикса):
    пробует preferred, preferred+1, … до attempts раз, затем отдаёт порт,
    выбранный ОС (bind на 0). Каждая попытка — реальный bind+close, а не
    эвристика "похоже, свободен" (TOCTOU-гонка возможна, но окно исчезающе
    мало для локального однопользовательского инструмента).
    """
    for port in range(preferred, preferred + attempts):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _friendly_encrypt_error(exc: Exception) -> str:
    """Типизированные ошибки конвейера -> человеческий текст (без трейсбека)."""
    from ooxml_core import OoxmlError

    if isinstance(exc, FileNotFoundError):
        return "Файл не найден — попробуйте загрузить документ ещё раз."
    if isinstance(exc, (ValueError, OoxmlError)):
        # extractor: битый/не-.docx, неопознанная кодировка .txt; сканер зон: битый ZIP.
        return str(exc)
    return f"Не удалось обработать документ: {type(exc).__name__}: {exc}"


def _friendly_decrypt_error(exc: Exception) -> str:
    from storage import SessionNotFoundError, SessionExpiredError

    if isinstance(exc, SessionExpiredError):
        return ("Сессия истекла (срок хранения — 24 часа с момента шифрации). "
                "Восстановить исходный текст по ней уже нельзя.")
    if isinstance(exc, SessionNotFoundError):
        return ("Сессия не найдена или повреждена. Проверьте ID сессии — это строка "
                "вида 8-4-4-4-12 из шестнадцатеричных цифр, выданная при шифрации.")
    if isinstance(exc, ValueError):
        return str(exc)
    return f"Не удалось восстановить текст: {type(exc).__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    # Тихий лог: без строки на каждый запрос в консоль пилота.
    def log_message(self, *args):
        pass

    # --- helpers ---
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > _MAX_UPLOAD:
            return None
        return self.rfile.read(length)

    # --- routes ---
    def do_GET(self):
        if self.path == "/api/ping":
            # Хендшейк для лаунчера: отличить "уже запущенный наш процесс" от
            # "порт занят чужим приложением" и от зомби старого билда.
            self._send_json({"build_mark": core.BUILD_MARK, "pid": os.getpid()})
            return
        if self.path in ("/", "/index.html"):
            try:
                with open(_INDEX, "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(500, "index.html not found")
                return
            body = body.replace(b"{{BUILD_MARK}}", core.BUILD_MARK.encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/encrypt":
            self._handle_encrypt()
        elif self.path == "/api/decrypt":
            self._handle_decrypt()
        else:
            self.send_error(404)

    def _handle_encrypt(self):
        from urllib.parse import unquote
        filename = unquote(self.headers.get("X-Filename", "document.docx"))
        allow_lossy = self.headers.get("X-Allow-Lossy", "0") == "1"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ALLOWED_EXT:
            self._send_json({"status": "error",
                             "message": "Поддерживаются только файлы .docx и .txt."})
            return

        data = self._read_body()
        if data is None:
            self._send_json({"status": "error",
                             "message": "Файл слишком большой (ограничение 50 МБ)."})
            return
        if not data:
            self._send_json({"status": "error", "message": "Пустой файл."})
            return

        # Пишем во временный файл с ПРАВИЛЬНЫМ расширением — extractor выбирает
        # обработчик по нему. Файл локальный, после обработки удаляется.
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="shifrator_ui_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                result = core.run_encrypt(tmp, allow_lossy=allow_lossy)
            except core.EncryptRefused as e:
                self._send_json({"status": "refused", "zones": e.zones,
                                 "source_name": filename})
                return
            except Exception as e:  # noqa: BLE001 — переводим в человеческий текст
                # Внутренний временный путь наружу не показываем — подменяем именем файла.
                msg = _friendly_encrypt_error(e).replace(tmp, filename)
                self._send_json({"status": "error", "message": msg})
                return
            result["status"] = "ok"
            result["source_name"] = filename
            self._send_json(result)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _handle_decrypt(self):
        data = self._read_body()
        if data is None:
            self._send_json({"status": "error", "message": "Слишком большой ввод."})
            return
        try:
            payload = json.loads(data.decode("utf-8"))
            session_id = (payload.get("session_id") or "").strip()
            text = payload.get("text", "")
        except (ValueError, AttributeError):
            self._send_json({"status": "error", "message": "Некорректный запрос."})
            return

        if not session_id:
            self._send_json({"status": "error", "message": "Укажите ID сессии."})
            return
        if not text.strip():
            self._send_json({"status": "error",
                             "message": "Вставьте обезличенный текст (или загрузите {ID}.txt)."})
            return

        try:
            result = core.run_decrypt(session_id, text)
        except Exception as e:  # noqa: BLE001
            self._send_json({"status": "error", "message": _friendly_decrypt_error(e)})
            return
        result["status"] = "ok"
        self._send_json(result)


class Server(ThreadingHTTPServer):
    # На Windows SO_REUSEADDR (значение по умолчанию в http.server) позволяет НОВОМУ
    # процессу забиндиться на порт, даже если СТАРЫЙ процесс всё ещё слушает его —
    # без ошибки. Итог: два сервера одновременно на 127.0.0.1:8765, ОС отдаёт
    # запросы то одному, то другому — пользователь видит то старый код, то новый.
    # Отключаем reuse, чтобы конфликт порта падал явно, а не терялся молча.
    allow_reuse_address = False


def main():
    port = find_free_port(DEFAULT_PORT)
    if port != DEFAULT_PORT:
        print(f"  Порт {DEFAULT_PORT} занят — использую {port}.", file=sys.stderr)
    try:
        server = Server((HOST, port), Handler)
    except OSError as e:
        print("=" * 60)
        print(f"  [ОШИБКА] Не удалось запустить сервер на порту {port}.")
        print(f"  Системная ошибка: {e}")
        print("=" * 60)
        sys.exit(1)
    url = f"http://{HOST}:{port}/"
    print("=" * 60)
    print("  SHIFRATOR — десктоп-интерфейс")
    print(f"  Открыт локально: {url}")
    print("  Обработка идёт на этом компьютере, в сеть ничего не уходит.")
    print("  Остановить: Ctrl+C в этом окне.")
    print("=" * 60)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
        server.shutdown()


if __name__ == "__main__":
    main()
