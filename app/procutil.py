"""Windows-специфичные помощники процесса: жив ли PID, принудительное завершение.

Только stdlib (ctypes) — не тянем psutil ради двух функций в упаковке, где каждый
лишний пакет — это лишние мегабайты и риск не собраться PyInstaller'ом.
"""

import ctypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_STILL_ACTIVE = 259


def is_pid_alive(pid: int) -> bool:
    """True, если процесс с данным PID существует и ещё выполняется."""
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def kill_pid(pid: int) -> bool:
    """Принудительно завершает процесс с данным PID. Возвращает успех."""
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)
