# -*- coding: utf-8 -*-
"""
O2 — A/B-стенд: вклад КАЖДОЙ оптимизации по отдельности.

Почему не «замерил до правки, замерил после»: разброс одиночного прогона на этой
машине ±5%, а несколько оптимизаций дают меньше того. Поэтому:
  * A/B делается НА ОДНОМ И ТОМ ЖЕ коде — нужная оптимизация ВЫКЛЮЧАЕТСЯ
    восстановлением до-O2 реализации (тот же приём, что --simulate-old в
    experiments/stage_eprime_determinism/corpus_anon_sha.py);
  * каждый вариант — в СВОЁМ процессе (пик памяти процесса не сбрасывается);
  * меряется time.process_time() — CPU процесса, а не стенные часы соседей;
  * берётся МИНИМУМ из N прогонов (минимум — наименее засорённая оценка).
Каждый вариант печатает sha256 выхода: если хоть один отличается от базового,
значит «выключатель» задел поведение и числа недействительны.

ЗАПУСК:
    venv/Scripts/python.exe experiments/stage_o2/ab_bench.py --load large --repeat 3
    venv/Scripts/python.exe experiments/stage_o2/ab_bench.py --load corpus --repeat 1
Варианты: o2 (всё включено), off_all (всё выключено = состояние до O2),
          off_gc / off_style / off_norm / off_strip / off_overlap /
          off_matchkey / off_nameparts (выключена одна).
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

VARIANTS = ["o2", "off_gc", "off_style", "off_norm", "off_strip",
            "off_overlap", "off_matchkey", "off_nameparts", "off_all"]


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


def peak_mb():
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    _gpmi(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 1048576.0


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


# --------------------------------------------------------------------------- #
#                 ВЫКЛЮЧАТЕЛИ: восстановление до-O2 реализаций                 #
# --------------------------------------------------------------------------- #
def disable(what):
    import normalizer as NM
    import anchor_registry as AR
    import tokenizer as TK

    if what in ("gc", "all"):
        gc.set_threshold(700, 10, 10)

    if what in ("style", "all"):
        # до O2: `.style` python-docx на каждое обращение, без кэша по styleId
        import extractor as EX
        EX._CapsResolver.style_of = lambda self, holder, style_type: holder.style

    if what in ("norm", "all"):
        NM._is_identity = lambda base: False

    if what in ("strip", "all"):
        def old_strip(text, nl_to_space=False):
            out, idx = [], []
            for i, ch in enumerate(text):
                if ch in AR._BREAKING_CHARS:
                    if nl_to_space and ch in "\n\r":
                        out.append(" ")
                        idx.append(i)
                    continue
                out.append(ch)
                idx.append(i)
            return "".join(out), idx
        AR._strip_breaking = old_strip

    if what in ("overlap", "all"):
        def old_has(entities):
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    if TK._overlaps(entities[i], entities[j]):
                        return True
            return False
        TK._has_overlapping_pair = old_has

    if what in ("matchkey", "all"):
        def old_match_key(self, entities, emitted_src, seg, norm, low, omap,
                          tokens, lemsets, npar, rec, det, group_of):
            klen = len(rec.key)
            n = len(tokens)
            i = 0
            while i + klen <= n:
                ok = all(rec.key[j] in lemsets[i + j] for j in range(klen))
                if not ok:
                    i += 1
                    continue
                s, e = det.expand_match(norm, low, tokens, npar, i, klen)
                src_s, src_e = AR.norm_to_src(omap, s, e)
                if self._covered(emitted_src, seg.id, src_s, src_e):
                    i += klen
                    continue
                if not det.guard(norm, low, s, e):
                    i += klen
                    continue
                gk, _ = group_of(rec.entity_type, rec.key, s, e, norm)
                self._emit(entities, emitted_src, seg, norm, omap, s, e,
                           rec.entity_type, group_key=gk)
                i += klen
        AR.AnchorEngine._match_key = old_match_key

    if what in ("nameparts", "all"):
        AR._nameparts = AR._nameparts.__wrapped__


def worker(variant, load):
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import extractor
    import normalizer      # noqa: F401
    import anchor_registry  # noqa: F401
    import tokenizer       # noqa: F401
    from pipeline import run_detection
    from tokenizer import tokenize

    if variant == "off_all":
        disable("all")
    elif variant.startswith("off_"):
        disable(variant[4:])

    paths = paths_for(load)
    h = hashlib.sha256()
    t, c = time.perf_counter(), time.process_time()
    for p in paths:
        d = extractor.extract(p)
        e = run_detection(d, CFG)
        anon, kept = tokenize(d, e, CFG)
        h.update(anon.encode("utf-8"))
    print("@@RESULT@@" + json.dumps({
        "variant": variant, "wall": time.perf_counter() - t,
        "cpu": time.process_time() - c, "peak_mb": peak_mb(),
        "sha": h.hexdigest(),
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", default="large")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--worker")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    if args.worker:
        worker(args.worker, args.load)
        return

    variants = args.only or VARIANTS
    best = {}
    for rep in range(args.repeat):
        for v in variants:
            out = subprocess.run(
                [sys.executable, os.path.abspath(__file__),
                 "--load", args.load, "--worker", v],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=ROOT)
            line = [l for l in out.stdout.splitlines() if l.startswith("@@RESULT@@")]
            if not line:
                print("!!! %s упал:\n%s\n%s" % (v, out.stdout[-1500:], out.stderr[-1500:]))
                continue
            r = json.loads(line[0][len("@@RESULT@@"):])
            cur = best.get(v)
            if cur is None or r["cpu"] < cur["cpu"]:
                best[v] = r
            print("  [%d] %-14s cpu %7.2f  wall %7.2f  пик %5.0f МБ"
                  % (rep + 1, v, r["cpu"], r["wall"], r["peak_mb"]), flush=True)

    rows = [best[v] for v in variants if v in best]
    if not rows:
        return
    o2 = best.get("o2")
    ref_sha = o2["sha"] if o2 else rows[0]["sha"]
    print("\n=== ВКЛАД ОПТИМИЗАЦИЙ, нагрузка %s (CPU-время, минимум из %d) ==="
          % (args.load, args.repeat))
    print("%-14s %9s %11s %9s %9s %s"
          % ("вариант", "CPU, c", "vs o2", "пик, МБ", "vs o2", "выход"))
    for r in rows:
        d = (r["cpu"] - o2["cpu"]) / o2["cpu"] * 100 if o2 else 0
        dm = (r["peak_mb"] - o2["peak_mb"]) / o2["peak_mb"] * 100 if o2 else 0
        print("%-14s %9.2f %+10.1f%% %9.0f %+8.1f%% %s"
              % (r["variant"], r["cpu"], d, r["peak_mb"], dm,
                 "идентичен" if r["sha"] == ref_sha else "!!! ОТЛИЧАЕТСЯ"))
    print("\nвклад каждой оптимизации = (вариант с ней ВЫКЛЮЧЕННОЙ) - o2;")
    print("положительная дельта = столько времени она экономит.")
    if o2 and "off_all" in best:
        a = best["off_all"]["cpu"]
        print("ИТОГО O2: %.2f c -> %.2f c  (%.1f%% от исходного, ускорение x%.2f)"
              % (a, o2["cpu"], (o2["cpu"] - a) / a * 100, a / o2["cpu"]))


if __name__ == "__main__":
    main()
