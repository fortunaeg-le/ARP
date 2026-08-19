"""Окно приложения вместо вкладки браузера (этап DESKTOP-FIT).

Как было: лаунчер звал `webbrowser.open` и пользователь получал ОБЫЧНУЮ вкладку
в своём браузере — с адресной строкой, вкладками, закладками и расширениями.
Это не десктоп-приложение: программа выглядит сайтом, теряется среди чужих
вкладок и делит окно с посторонним содержимым.

Как стало: у любого браузера на движке Chromium есть режим `--app=URL` — окно
без адресной строки, без вкладок и без закладок, со своей иконкой в панели
задач. Новой зависимости это не требует (долг ENV-LOCK-INCOMPLETE открыт,
`packaging/` зафиксирован): Edge стоит на любой Windows 10/11 из коробки,
поэтому ищем сначала его, потом Chrome, потом Chromium.

Профиль окна — СВОЙ (`--user-data-dir` рядом с `launcher.lock`). Иначе окно
подключилось бы к уже запущенному браузеру пользователя: унаследовало бы его
расширения и сессии, а закрытие браузера утащило бы за собой окно программы.
Программа офлайн — фоновая сеть браузера гасится теми же флагами, что в
`packaging/ui_circle.py`.

Ничего из этого не нашлось — падаем обратно на `webbrowser.open` и НЕ считаем
это ошибкой: программа обязана запускаться всегда, каким бы браузером ни
пользовался человек.
"""

import ctypes
import os
import subprocess
import webbrowser

import procutil

_CREATE_NO_WINDOW = 0x08000000  # без него --windowed exe моргает консолью

# Порядок — от «точно есть» к «может быть». Edge идёт первым: он есть на любой
# Windows 10/11 без установки, поэтому режим окна работает у любого клиента.
_RELATIVE_CANDIDATES = [
    ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
    ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"),
    ("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
    ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
    ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
    ("ProgramFiles", r"Chromium\Application\chrome.exe"),
    ("ProgramFiles(x86)", r"Chromium\Application\chrome.exe"),
    ("LOCALAPPDATA", r"Chromium\Application\chrome.exe"),
]

# Окна, поднятые этим процессом (для отладки и тестов; за реальное закрытие
# отвечает объект задания ниже).
_WINDOWS = []

# Объект задания Windows (job object). ЗАЧЕМ ОН НУЖЕН — живая проба
# (experiments/desktop_fit_probe.py): запущенный `msedge.exe` СРАЗУ ЗАВЕРШАЕТСЯ
# с кодом 0, передав окно другому процессу браузера. Popen на него смотрит и
# видит «процесс закончился», а окно на экране живёт. Убивать по сохранённому
# PID поэтому бесполезно: taskkill отвечает «нет такого процесса», а окно
# остаётся висеть с мёртвым сервером за спиной — ровно тот зомби, которого
# лаунчер и не должен оставлять.
#
# Задание с флагом KILL_ON_JOB_CLOSE решает это на уровне ядра: браузер и ВСЕ
# его потомки состоят в задании, а закрытие последней ссылки на задание (выход
# программы, в том числе аварийный) завершает их. Явный TerminateJobObject в
# close_all() — тот же результат по кнопке «Выход», не дожидаясь выхода процесса.
_JOB = None

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _job_handle():
    """Задание, в которое складываются окна. None — если ядро не дало создать
    (тогда работаем без него: окно всё равно откроется, см. _adopt)."""
    global _JOB
    if _JOB is not None:
        return _JOB
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JobExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle, _JOB_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            return None
        _JOB = handle
        return _JOB
    except Exception:  # noqa: BLE001 — нет kernel32/не Windows: живём без задания
        return None


def _adopt(pid: int) -> bool:
    """Включает процесс окна в задание. Неудача не фатальна: окно уже открыто, и
    отказаться от него из-за проблемы с уборкой было бы хуже, чем убрать хуже."""
    job = _job_handle()
    if job is None:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(job, handle))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def browser_candidates():
    """Пути, по которым ищем браузер. Собираются из переменных окружения, а не
    зашиты строкой: на локализованных и переставленных системах каталог
    Program Files не обязан лежать на диске C."""
    paths = []
    for var, tail in _RELATIVE_CANDIDATES:
        base = os.environ.get(var)
        if base:
            paths.append(os.path.join(base, tail))
    return paths


def find_browser():
    """Путь к первому найденному Chromium-браузеру или None."""
    override = os.environ.get("SHIFRATOR_APP_BROWSER")
    if override:
        return override if os.path.isfile(override) else None
    for path in browser_candidates():
        if os.path.isfile(path):
            return path
    return None


def profile_dir():
    """Каталог профиля окна — рядом с `launcher.lock`, в `~/.shifrator`."""
    from storage import default_storage_dir
    return default_storage_dir().parent / "window-profile"


def _work_area():
    """Рабочая область экрана (без панели задач) в логических пикселях.
    Не определилась — None, и тогда размер окна не задаём вовсе."""
    class _Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    try:
        rect = _Rect()
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        if not ok:
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        return w, h
    except Exception:  # noqa: BLE001 — нет user32/не Windows: работаем без размера
        return None


def window_size():
    """Размер окна при первом открытии: желаемые 1360x900, но НЕ БОЛЬШЕ рабочей
    области экрана. Без ограничения окно на ноутбуке 1366x768 вылезает за низ
    экрана — ровно та жалоба, из-за которой интерфейсом нельзя было
    пользоваться. Профиль запоминает размер сам, поэтому это только первый раз."""
    area = _work_area()
    if area is None:
        return None
    return min(1360, area[0]), min(900, area[1])


def open_app_window(url: str, manage: bool = True) -> bool:
    """Открывает URL отдельным окном приложения. Возвращает True при успехе.

    `manage=False` — окно НЕ включается в задание, то есть не будет закрыто на
    выходе этого процесса. Нужно ровно одному вызову: второй запуск программы
    (двойной клик по ярлыку, пока она уже работает) открывает окно и тут же
    завершается сам — с заданием он убил бы окно через долю секунды после того,
    как человек его попросил. Окно в этом случае почти всегда достаётся
    браузеру, поднятому ПЕРВЫМ лаунчером (профиль общий), и уходит вместе с ним.
    """
    exe = find_browser()
    if exe is None:
        return False

    profile = profile_dir()
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    args = [exe, f"--app={url}", f"--user-data-dir={profile}"]
    size = window_size()
    if size is not None:
        args.append("--window-size={},{}".format(*size))
    args += [
        "--no-first-run", "--no-default-browser-check",
        # Программа работает офлайн — гасим всё фоновое, что браузер иначе
        # тянет в сеть. Клиент не должен видеть сетевую активность у
        # инструмента, который обещает «ничего не уходит наружу».
        "--disable-background-networking", "--disable-component-update",
        "--disable-sync", "--disable-default-apps", "--disable-extensions",
        "--no-service-autorun", "--metrics-recording-only",
    ]

    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    if manage:
        # Включать в задание надо СРАЗУ: процесс-стартер живёт доли секунды, а
        # его потомки попадают в задание только по наследству от него.
        _adopt(proc.pid)
        _WINDOWS.append(proc)
    return True


def open(url: str, manage: bool = True) -> str:  # noqa: A001 — зеркалит webbrowser.open
    """Открывает интерфейс. Возвращает "app" (своё окно) или "browser" (запасной
    путь). Запуск программы не зависит от того, какой браузер стоит у клиента."""
    if open_app_window(url, manage=manage):
        return "app"
    webbrowser.open(url)
    return "browser"


def close_all():
    """Закрывает окна, поднятые этим процессом. Зовётся на выходе из программы:
    окно приложения не должно пережить сервер, за которым оно ходит."""
    global _JOB
    if _JOB is not None:
        try:
            ctypes.windll.kernel32.TerminateJobObject(_JOB, 1)
            ctypes.windll.kernel32.CloseHandle(_JOB)
        except Exception:  # noqa: BLE001
            pass
        _JOB = None

    # Запасной путь на случай, когда задание не создалось: гасим то, что ещё
    # видно по PID. Дерево целиком — Chromium это не один процесс, и убитый
    # главный оставил бы живые рендереры.
    for proc in _WINDOWS:
        if proc.poll() is None:
            _kill_tree(proc.pid)
    for proc in _WINDOWS:
        try:
            proc.wait(timeout=5)
        except subprocess.SubprocessError:
            pass
    _WINDOWS.clear()


def _kill_tree(pid: int):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        procutil.kill_pid(pid)
