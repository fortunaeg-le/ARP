# -*- coding: utf-8 -*-
"""
O2 — сколько стоит сборщик мусора.

Гипотеза: yargy создаёт миллионы короткоживущих объектов (6 млн State на
одну крупную фикстуру), из-за чего поколенческий GC CPython запускается
постоянно. Отключение/огрубление GC поведения НЕ меняет (в пайплайне нет
логики, зависящей от __del__ или от момента сборки), но ест память —
поэтому меряем И время, И пик рабочего множества.

ЗАПУСК: venv/Scripts/python.exe experiments/stage_o2/probe_gc.py [--load large|corpus|styles|txt]
"""
import argparse
import ctypes
import gc
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
CFG = os.path.join(ROOT, "entity_types.yaml")
FIX = os.path.join(ROOT, "tests", "fixtures")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")


class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = ctypes.c_void_p
try:
    _gpmi = _k32.K32GetProcessMemoryInfo
except AttributeError:                      # старые сборки: psapi.dll
    _gpmi = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
_gpmi.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_uint32]
_gpmi.restype = ctypes.c_int


def mem():
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    ok = _gpmi(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    if not ok:
        raise OSError("GetProcessMemoryInfo failed: %d" % ctypes.get_last_error())
    return c.PeakWorkingSetSize / 1048576.0, c.WorkingSetSize / 1048576.0


def paths_for(load):
    if load == "large":
        return [os.path.join(FIX, "synthetic_corporate_large.docx")]
    if load == "styles":
        return [os.path.join(FIX, "synthetic_many_styles.docx")]
    if load == "txt":
        return [os.path.join(FIX, "synthetic_corporate_large.txt")]
    names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
    if ":" in load:
        names = names[:int(load.split(":")[1])]
    return [os.path.join(DOCS, f) for f in names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", default="large")
    args = ap.parse_args()

    import extractor
    from pipeline import run_detection
    from tokenizer import tokenize
    import hashlib

    paths = paths_for(args.load)

    def run():
        h = hashlib.sha256()
        for p in paths:
            d = extractor.extract(p)
            e = run_detection(d, CFG)
            anon, kept = tokenize(d, e, CFG)
            h.update(anon.encode("utf-8"))
        return h.hexdigest()

    print("нагрузка: %s (%d файлов), пороги GC: %s" % (args.load, len(paths), gc.get_threshold()))
    results = []

    # прогрев (первый прогон всегда дороже — модели/страницы)
    run()

    for label, setup, teardown in (
        ("GC как есть (700,10,10)", lambda: None, lambda: None),
        ("GC ВЫКЛЮЧЕН",             lambda: gc.disable(), lambda: gc.enable()),
        ("GC freeze",               lambda: (gc.collect(), gc.freeze()), lambda: gc.unfreeze()),
        ("GC gen0=5000",            lambda: gc.set_threshold(5000, 10, 10),
                                    lambda: gc.set_threshold(700, 10, 10)),
        ("GC gen0=50000",           lambda: gc.set_threshold(50000, 10, 10),
                                    lambda: gc.set_threshold(700, 10, 10)),
        ("GC freeze+gen0=50000",    lambda: (gc.collect(), gc.freeze(), gc.set_threshold(50000, 10, 10)),
                                    lambda: (gc.unfreeze(), gc.set_threshold(700, 10, 10))),
        ("GC freeze+gen0=50k,g1=50", lambda: (gc.collect(), gc.freeze(), gc.set_threshold(50000, 50, 50)),
                                    lambda: (gc.unfreeze(), gc.set_threshold(700, 10, 10))),
    ):
        setup()
        gc.collect() if label.startswith("GC как есть") else None
        p0, _ = mem()
        t = time.perf_counter()
        h = run()
        dt = time.perf_counter() - t
        p1, cur = mem()
        teardown()
        gc.collect()
        results.append((label, dt, p1, cur, h))
        print("  %-26s %8.2f s   пик %6.0f MB (был %6.0f)  текущ %6.0f MB  sha %s"
              % (label, dt, p1, p0, cur, h[:16]))

    base = results[0][1]
    print("\nотносительно базового:")
    for label, dt, p1, cur, h in results:
        print("  %-26s %+6.1f%%   выход %s"
              % (label, (dt - base) / base * 100, "ИДЕНТИЧЕН" if h == results[0][4] else "!!! ОТЛИЧАЕТСЯ"))


if __name__ == "__main__":
    main()
