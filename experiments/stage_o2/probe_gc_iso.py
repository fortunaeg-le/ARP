# -*- coding: utf-8 -*-
"""
O2 — GC-варианты, КАЖДЫЙ В СВОЁМ ПРОЦЕССЕ.

Пик рабочего множества процесса не сбрасывается, поэтому меряя варианты
подряд в одном процессе, получаешь пик самого прожорливого — а не своего.
Здесь каждый вариант — отдельный python.exe.

ЗАПУСК:
    venv/Scripts/python.exe experiments/stage_o2/probe_gc_iso.py --load large
    venv/Scripts/python.exe experiments/stage_o2/probe_gc_iso.py --load corpus
Внутренний режим (вызывается сам): --worker <вариант>
"""
import argparse
import ctypes
import gc
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CFG = os.path.join(ROOT, "entity_types.yaml")
FIX = os.path.join(ROOT, "tests", "fixtures")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

VARIANTS = [
    "base",            # как сейчас: (700, 10, 10)
    "off",             # gc.disable()
    "freeze",          # gc.freeze() после загрузки моделей
    "g0_5000",
    "g0_20000",
    "g0_50000",
    "freeze_g0_50000",
    "freeze_g0_50000_g1_50",
]


class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = ctypes.c_void_p
_gpmi = getattr(_k32, "K32GetProcessMemoryInfo", None) or \
    ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
_gpmi.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_uint32]
_gpmi.restype = ctypes.c_int


def mem():
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    if not _gpmi(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise OSError("GetProcessMemoryInfo failed")
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


def worker(variant, load):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import extractor
    from pipeline import run_detection
    from tokenizer import tokenize

    paths = paths_for(load)
    peak_after_import, _ = mem()

    if variant == "off":
        gc.disable()
    elif variant == "freeze":
        gc.collect(); gc.freeze()
    elif variant == "g0_5000":
        gc.set_threshold(5000, 10, 10)
    elif variant == "g0_20000":
        gc.set_threshold(20000, 10, 10)
    elif variant == "g0_50000":
        gc.set_threshold(50000, 10, 10)
    elif variant == "g0_100000":
        gc.set_threshold(100000, 10, 10)
    elif variant == "g0_10000":
        gc.set_threshold(10000, 10, 10)
    elif variant == "freeze_g0_50000":
        gc.collect(); gc.freeze(); gc.set_threshold(50000, 10, 10)
    elif variant == "freeze_g0_50000_g1_50":
        gc.collect(); gc.freeze(); gc.set_threshold(50000, 50, 50)

    h = hashlib.sha256()
    t = time.perf_counter()
    c = time.process_time()
    for p in paths:
        d = extractor.extract(p)
        e = run_detection(d, CFG)
        anon, kept = tokenize(d, e, CFG)
        h.update(anon.encode("utf-8"))
    dt = time.perf_counter() - t
    dc = time.process_time() - c
    peak, cur = mem()
    print("@@RESULT@@" + json.dumps({
        "variant": variant, "seconds": dt, "cpu": dc, "peak_mb": peak,
        "peak_import_mb": peak_after_import, "cur_mb": cur, "sha": h.hexdigest(),
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", default="large")
    ap.add_argument("--worker")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    if args.worker:
        worker(args.worker, args.load)
        return

    variants = args.only or VARIANTS
    rows = []
    for v in variants:
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--load", args.load, "--worker", v],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
        line = [l for l in out.stdout.splitlines() if l.startswith("@@RESULT@@")]
        if not line:
            print("!!! вариант %s упал:\n%s\n%s" % (v, out.stdout[-2000:], out.stderr[-2000:]))
            continue
        rows.append(json.loads(line[0][len("@@RESULT@@"):]))

    base = rows[0]["seconds"] if rows else 1
    base_cpu = rows[0]["cpu"] if rows else 1
    base_peak = rows[0]["peak_mb"] if rows else 1
    print("\n=== GC-варианты, нагрузка %s (каждый в своём процессе) ===" % args.load)
    print("%-24s %8s %8s %8s %8s %9s %8s %s"
          % ("вариант", "wall", "дельта", "CPU", "дельта", "пик,МБ", "дельта", "выход"))
    for r in rows:
        print("%-24s %8.2f %+7.1f%% %8.2f %+7.1f%% %9.0f %+7.1f%% %s"
              % (r["variant"], r["seconds"], (r["seconds"] - base) / base * 100,
                 r["cpu"], (r["cpu"] - base_cpu) / base_cpu * 100,
                 r["peak_mb"], (r["peak_mb"] - base_peak) / base_peak * 100,
                 "идентичен" if r["sha"] == rows[0]["sha"] else "!!! ОТЛИЧАЕТСЯ"))


if __name__ == "__main__":
    main()
