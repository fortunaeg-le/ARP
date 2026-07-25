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
import time
import uuid
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


def _friendly_markup_error(exc: Exception) -> str:
    """U3: ошибки правки/пересборки разметки -> человеческий текст (без трейсбека).

    Те же типизированные ошибки хранилища, что и decrypt, плюс FileNotFoundError
    (сессия создана до этапа разметки — нет {sid}.doc.json) и ValueError
    (некорректное выделение/пересечение и т.п. — уже человеческий текст, см.
    app/core.py::_validate_missed)."""
    from storage import SessionNotFoundError, SessionExpiredError

    if isinstance(exc, SessionExpiredError):
        return ("Сессия истекла (срок хранения — 24 часа с момента шифрации). "
                "Разметку по ней сохранить уже нельзя.")
    if isinstance(exc, SessionNotFoundError):
        return "Сессия не найдена или повреждена — обновите страницу."
    if isinstance(exc, FileNotFoundError):
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return f"Не удалось сохранить правку: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# U3-4: индикатор прогресса шифрации крупного документа.
#
# Детекцию инструментировать НЕЛЬЗЯ (граница этой сессии — src/, кроме
# storage.py, не трогать; поэтапный колбэк потребовал бы правки
# src/pipeline.py и трёх детекторов). Минимальное изменение в её границах:
# /api/encrypt стал асинхронным (фоновый поток + опрос статуса), что даёт ДВЕ
# честные вещи без единой правки в src/, кроме storage.py:
#   1) точное число сегментов документа — известно сразу после extract()
#      (core.run_encrypt(on_extracted=...), извлечение быстрое, детекция — нет);
#   2) процент как оценка по прошедшему времени/числу сегментов (не точный
#      прогресс стадий, а честная линейная экстраполяция, доезжающая до 95% и
#      прыгающая на 100% только по факту готовности — не создаёт иллюзию точности).
# Если позже понадобится НАСТОЯЩИЙ прогресс по стадиям — минимальная точка
# входа: 4 callback-точки в src/pipeline.py::run_detection (после каждого шага),
# см. HANDOFF_U3 §4.
# --------------------------------------------------------------------------- #

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_SECONDS = 30 * 60
_SECONDS_PER_SEGMENT = 0.03   # грубая калибровка (documents из STATE.md: ~2500 сегм. => десятки с)
_MIN_ESTIMATE_SECONDS = 2.0


def _purge_old_jobs_locked():
    now = time.time()
    for jid in [jid for jid, j in _JOBS.items() if now - j["started_at"] > _JOB_TTL_SECONDS]:
        del _JOBS[jid]


def _run_encrypt_job(job_id: str, tmp_path: str, allow_lossy: bool, filename: str):
    def on_extracted(doc):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["segment_count"] = len(doc.segments)

    try:
        result = core.run_encrypt(tmp_path, allow_lossy=allow_lossy, source_name=filename,
                                   on_extracted=on_extracted)
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["status"] = "done"
                result["status"] = "ok"
                job["result"] = result
    except core.EncryptRefused as e:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {"status": "refused", "zones": e.zones, "source_name": filename}
    except Exception as e:  # noqa: BLE001 — переводим в человеческий текст
        msg = _friendly_encrypt_error(e).replace(tmp_path, filename)
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["result"] = {"status": "error", "message": msg}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


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
        if self.path == "/api/sessions":
            self._handle_list_sessions()
            return
        if self.path == "/api/markup/types":
            self._send_json({"status": "ok", "types": core.markup_type_options()})
            return
        if self.path.startswith("/api/encrypt/status"):
            self._handle_encrypt_status()
            return
        if self.path.startswith("/api/markup/list"):
            self._handle_markup_list()
            return
        if self.path == "/api/markup/summary":
            self._handle_markup_summary()
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
        elif self.path == "/api/encrypt/start":
            self._handle_encrypt_start()
        elif self.path == "/api/decrypt":
            self._handle_decrypt()
        elif self.path == "/api/session-delete":
            self._handle_session_delete()
        elif self.path == "/api/markup/mark-missed":
            self._handle_markup_op(self._op_mark_missed)
        elif self.path == "/api/markup/false-positive":
            self._handle_markup_op(self._op_mark_false_positive)
        elif self.path == "/api/markup/replace":
            self._handle_markup_op(self._op_mark_replace)
        elif self.path == "/api/markup/update":
            self._handle_markup_op(self._op_markup_update)
        elif self.path == "/api/markup/delete":
            self._handle_markup_op(self._op_markup_delete)
        elif self.path == "/api/markup/apply":
            self._handle_markup_op(self._op_markup_apply)
        elif self.path == "/api/markup/delete-all":
            self._handle_markup_delete_all()
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
                result = core.run_encrypt(tmp, allow_lossy=allow_lossy, source_name=filename)
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
            self._send_json(result)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _handle_encrypt_start(self):
        """U3-4: асинхронный запуск шифрации — возвращает job_id немедленно,
        фактическая обработка идёт в фоновом потоке (см. _run_encrypt_job)."""
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

        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="shifrator_ui_")
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        job_id = str(uuid.uuid4())
        with _JOBS_LOCK:
            _purge_old_jobs_locked()
            _JOBS[job_id] = {"status": "running", "started_at": time.time(),
                              "segment_count": None, "result": None}
        threading.Thread(target=_run_encrypt_job, args=(job_id, tmp, allow_lossy, filename),
                          daemon=True).start()
        self._send_json({"status": "ok", "job_id": job_id})

    def _handle_encrypt_status(self):
        from urllib.parse import urlsplit, parse_qs
        qs = parse_qs(urlsplit(self.path).query)
        job_id = (qs.get("job_id") or [""])[0]
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                self._send_json({"status": "error", "message": "Задача не найдена (истекла или сервер перезапущен)."})
                return
            elapsed = time.time() - job["started_at"]
            segment_count = job["segment_count"]
            if job["status"] == "done":
                percent = 100
            else:
                estimate = max(_MIN_ESTIMATE_SECONDS,
                                (segment_count or 0) * _SECONDS_PER_SEGMENT)
                percent = min(95, round(100 * elapsed / estimate))
            payload = {"status": job["status"], "segment_count": segment_count,
                       "percent": percent, "elapsed": round(elapsed, 1)}
            if job["status"] == "done":
                payload["result"] = job["result"]
        self._send_json(payload)

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

    def _handle_list_sessions(self):
        # Список сессий пользователя (Задача U2-3) — только storage.py, файлы напрямую не трогаем.
        from storage import list_sessions
        self._send_json({"status": "ok", "sessions": list_sessions()})

    def _handle_session_delete(self):
        from storage import delete_session

        data = self._read_body()
        if data is None:
            self._send_json({"status": "error", "message": "Слишком большой запрос."})
            return
        try:
            payload = json.loads(data.decode("utf-8"))
            session_id = (payload.get("session_id") or "").strip()
            delete_markup = bool(payload.get("delete_markup", False))
        except (ValueError, AttributeError):
            self._send_json({"status": "error", "message": "Некорректный запрос."})
            return
        if not session_id:
            self._send_json({"status": "error", "message": "Укажите ID сессии."})
            return
        # S1: разметка сессии по умолчанию ПЕРЕЖИВАЕТ удаление сессии (отдельный
        # актив, см. src/storage.py §ЭТАП S1) — delete_markup=True удаляет и её,
        # это явный дополнительный выбор пользователя в диалоге подтверждения.
        deleted = delete_session(session_id, delete_markup=delete_markup)
        self._send_json({"status": "ok", "deleted": deleted})

    def _handle_markup_summary(self):
        from storage import markup_summary
        self._send_json({"status": "ok", **markup_summary()})

    def _handle_markup_delete_all(self):
        from storage import delete_all_markup
        removed = delete_all_markup()
        self._send_json({"status": "ok", "removed_sessions": removed})

    # ------------------------------------------------------------------ #
    # U3 — разметка экрана проверки.
    # ------------------------------------------------------------------ #

    def _handle_markup_list(self):
        from urllib.parse import urlsplit, parse_qs
        qs = parse_qs(urlsplit(self.path).query)
        session_id = (qs.get("session_id") or [""])[0]
        if not session_id:
            self._send_json({"status": "error", "message": "Укажите ID сессии."})
            return
        self._send_json({"status": "ok", "entries": core.list_markup_entries(session_id)})

    def _handle_markup_op(self, op):
        """Общая обвязка для всех POST /api/markup/*: разобрать JSON, вызвать
        op(payload) -> dict, перевести типизированные ошибки в человеческий текст."""
        data = self._read_body()
        if data is None:
            self._send_json({"status": "error", "message": "Слишком большой запрос."})
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, AttributeError):
            self._send_json({"status": "error", "message": "Некорректный запрос."})
            return
        try:
            result = op(payload)
        except Exception as e:  # noqa: BLE001
            self._send_json({"status": "error", "message": _friendly_markup_error(e)})
            return
        result["status"] = "ok"
        self._send_json(result)

    @staticmethod
    def _op_mark_missed(p):
        return core.mark_missed(p["session_id"], p["segment_id"], int(p["start"]), int(p["end"]),
                                 p["entity_type"])

    @staticmethod
    def _op_mark_false_positive(p):
        return core.mark_false_positive(p["session_id"], p["segment_id"], int(p["start"]),
                                         int(p["end"]), p["token"])

    @staticmethod
    def _op_mark_replace(p):
        return core.mark_replace(
            p["session_id"], p["old_token"], p["old_segment_id"], int(p["old_start"]), int(p["old_end"]),
            p["segment_id"], int(p["start"]), int(p["end"]), p["entity_type"],
        )

    @staticmethod
    def _op_markup_update(p):
        core.update_markup_entry(p["session_id"], p["markup_id"], p["entity_type"])
        return {}

    @staticmethod
    def _op_markup_delete(p):
        return core.delete_markup_entry(p["session_id"], p["markup_id"])

    @staticmethod
    def _op_markup_apply(p):
        return core.apply_pending_markup(p["session_id"])


class Server(ThreadingHTTPServer):
    # На Windows SO_REUSEADDR (значение по умолчанию в http.server) позволяет НОВОМУ
    # процессу забиндиться на порт, даже если СТАРЫЙ процесс всё ещё слушает его —
    # без ошибки. Итог: два сервера одновременно на 127.0.0.1:8765, ОС отдаёт
    # запросы то одному, то другому — пользователь видит то старый код, то новый.
    # Отключаем reuse, чтобы конфликт порта падал явно, а не терялся молча.
    allow_reuse_address = False


def _migrate_legacy_markup_once():
    """S1: разметка (U3), сохранённая ДО этой сессии, лежит в старом
    расположении (директория сессий) — переносим в отдельное хранилище разметки
    ОДИН раз при старте, иначе она бы осиротела там и не участвовала ни в
    новой политике срока, ни в «удалить всю разметку». Некритично для запуска:
    сбой переноса (диск/права) не должен блокировать интерфейс."""
    try:
        from storage import migrate_legacy_markup
        n = migrate_legacy_markup()
        if n:
            print(f"  Перенесена разметка {n} сессий в новое хранилище (этап S1).", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  [предупреждение] перенос старой разметки не удался: {e}", file=sys.stderr)


def main():
    _migrate_legacy_markup_once()
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
