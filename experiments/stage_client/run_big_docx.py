# -*- coding: utf-8 -*-
"""ЧАСТЬ 3 этапа CLIENT: прогон большого документа — время, память, падает ли.

Идёт ТЕМ ЖЕ путём, что командная строка (`shifrator.py::cmd_encrypt`):
extract -> scan_unread_zones -> pipeline.run_detection -> tokenize. Порядок и
функции те же, чтобы цифра относилась к продукту, а не к отдельному замеру.

Память: пиковый рабочий набор ПРОЦЕССА через GetProcessMemoryInfo (psapi) —
`tracemalloc` тут не годится, основной расход у Natasha в нативных буферах.

Запуск: venv/Scripts/python.exe experiments/stage_client/run_big_docx.py [путь]
Без аргумента берётся probe_out/big_x10.docx.
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(1, os.path.join(_ROOT, "src"))

DEFAULT_DOC = os.path.join(_HERE, "probe_out", "big_x10.docx")
CONFIG = os.path.join(_ROOT, "entity_types.yaml")


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


# ВНИМАНИЕ (стоило одного прогона впустую): restype у GetCurrentProcess ОБЯЗАН
# быть HANDLE. По умолчанию ctypes считает результат 32-битным int, псевдо-хендл
# (-1) на x64 передаётся не расширенным по знаку, вызов молча не срабатывает — и
# структура остаётся нулевой. Ноль в замере обязан быть объяснён механизмом,
# поэтому ниже ещё и проверка кода возврата.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_psapi.GetProcessMemoryInfo.restype = wt.BOOL
_psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]


def mem_mb() -> "tuple[float, float]":
    """(текущий рабочий набор, пиковый) в МБ."""
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    if not _psapi.GetProcessMemoryInfo(
            _kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo не сработал")
    return pmc.WorkingSetSize / 2 ** 20, pmc.PeakWorkingSetSize / 2 ** 20


def stage(name: str, fn):
    t0 = time.perf_counter()
    result = fn()
    dt = time.perf_counter() - t0
    cur, peak = mem_mb()
    print("  %-22s %8.1f c   память %7.1f МБ (пик %7.1f МБ)" % (name, dt, cur, peak),
          flush=True)
    return result, dt


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DOC
    size_kb = os.path.getsize(path) / 1024
    print("документ: %s (%.1f КБ контейнера)" % (path, size_kb), flush=True)
    print("  %-22s %8s     %s" % ("этап", "время", "память"), flush=True)

    t_all = time.perf_counter()

    def _imports():
        import extractor, unread_zones, pipeline, tokenizer, type_policy  # noqa
        return None

    stage("импорт+модели", _imports)

    from extractor import extract
    from unread_zones import scan_unread_zones
    from pipeline import run_detection
    from tokenizer import tokenize
    import type_policy

    doc, _ = stage("extract", lambda: extract(path))
    zones, _ = stage("scan_unread_zones",
                     lambda: scan_unread_zones(path) if path.lower().endswith(".docx") else [])
    entities, t_det = stage("run_detection", lambda: run_detection(doc, CONFIG))
    type_policy.ensure_settings()
    (anon_text, final_entities), _ = stage(
        "tokenize", lambda: tokenize(doc, entities, CONFIG,
                                     enabled_types=type_policy.enabled_types(CONFIG)))

    total = time.perf_counter() - t_all
    cur, peak = mem_mb()
    print("-" * 62, flush=True)
    print("сегментов        : %d" % len(doc.segments))
    print("непрочитанных зон: %d" % len(zones))
    print("сущностей найдено: %d" % len(entities))
    print("масок поставлено : %d" % len(final_entities))
    print("длина anon-текста: %d символов" % len(anon_text))
    print("ИТОГО ВРЕМЯ      : %.1f c   (из них детекция %.1f c = %.0f %%)"
          % (total, t_det, 100 * t_det / total))
    print("ПИК ПАМЯТИ       : %.1f МБ (в конце %.1f МБ)" % (peak, cur))


if __name__ == "__main__":
    main()
