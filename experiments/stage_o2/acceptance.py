# -*- coding: utf-8 -*-
"""
O2 — приёмка: детерминизм и пик памяти.

  --determinism-corpus N   N прогонов ВСЕГО корпуса, sha агрегата обязан совпасть
  --determinism-large N    N прогонов крупной фикстуры, один хеш
  --memory <load>          пик рабочего множества процесса на нагрузке
  --subset                 взять зафиксированный итерационный срез
                           (tests/corpus/subset_iter.json) вместо листинга каталога

Каждый прогон детерминизма — В ОДНОМ процессе (именно так ловились Eprime-A и
дефект _CapsResolver: между процессами всё совпадало, внутри одного — нет).

О `--subset` (добавлен позже, поведение ПО УМОЛЧАНИЮ не изменено)
-----------------------------------------------------------------
Без флага набор строится как раньше: `sorted(os.listdir(DOCS))`, фильтр `.docx`,
префикс `[:--limit]`. Умолчание менять нельзя — агрегатный sha `run_paths()`
считается по ВСЕМУ набору, поэтому смена состава по умолчанию обесценила бы все
исторические хеши детерминизма из отчётов O2 (числа 60/120/162 в
docs/PERF_REPORT.md §4.3 — именно такие префиксы).

С флагом берутся 25 документов зафиксированного среза, включая `.txt`, которых
`acceptance.py` не видел никогда. Для проверки детерминизма это строго лучше:
§1.2 показывает, что `.txt`-половина даёт в 1.55 раза больше сегментов, а
открытая находка Eprime-A наблюдалась именно на многосегментных документах.
Хеши `--subset` и без него — разные величины; сравнивать их между собой нельзя.
"""
import argparse
import ctypes
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
CFG = os.path.join(ROOT, "entity_types.yaml")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")
FIX = os.path.join(ROOT, "tests", "fixtures")


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


def subset_corpus_names():
    """Имена файлов зафиксированного итерационного среза (.docx И .txt).

    Тот же единственный источник состава, что у subsample.py и etalon.py —
    tests/corpus/subset_lib поверх tests/corpus/subset_iter.json."""
    sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
    import subset_lib as SL
    fmt = {d["doc_id"]: d["format"] for d in SL.load_gold()}
    names = []
    for doc_id in SL.load_subset_ids():
        fn = "%s.%s" % (doc_id, fmt[doc_id])
        if os.path.exists(os.path.join(DOCS, fn)):
            names.append(fn)
    return sorted(names)


def corpus_names(use_subset, limit):
    """Набор документов корпуса: срез по флагу либо ПРЕЖНИЙ листинг каталога."""
    if use_subset:
        return subset_corpus_names()
    names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
    return names[:limit] if limit else names


def mem():
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    _gpmi(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 1048576.0, c.WorkingSetSize / 1048576.0


def _sig(anon, kept):
    """Тот же тройной срез, что в etalon.py: текст + сущности В ПОРЯДКЕ выдачи."""
    rows = []
    for e in kept:
        spans = "" if e.spans is None else ";".join("%d:%d" % (a, b) for a, b in e.spans)
        rows.append("\t".join([e.segment_id, str(e.start), str(e.end), spans,
                               e.entity_type, str(e.token), e.detector,
                               str(e.group_key), str(e.canonical), repr(e.original_text)]))
    h = hashlib.sha256()
    h.update(anon.encode("utf-8"))
    h.update(b"\x00")
    h.update("\n".join(rows).encode("utf-8"))
    return h.hexdigest()


def run_paths(paths):
    import extractor
    from pipeline import run_detection
    from tokenizer import tokenize
    agg = hashlib.sha256()
    n_ent = 0
    for p in paths:
        d = extractor.extract(p)
        e = run_detection(d, CFG)
        anon, kept = tokenize(d, e, CFG)
        n_ent += len(kept)
        agg.update(_sig(anon, kept).encode("ascii"))
    return agg.hexdigest(), n_ent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--determinism-corpus", type=int)
    ap.add_argument("--determinism-large", type=int)
    ap.add_argument("--memory")
    ap.add_argument("--limit", type=int,
                    help="взять только первые N документов корпуса")
    ap.add_argument("--subset", action="store_true",
                    help="взять зафиксированный итерационный срез "
                         "(tests/corpus/subset_iter.json, .docx И .txt); "
                         "--limit при этом игнорируется")
    args = ap.parse_args()

    if args.determinism_corpus:
        names = corpus_names(args.subset, args.limit)
        paths = [os.path.join(DOCS, f) for f in names]
        print("ДЕТЕРМИНИЗМ: %d прогонов %s (%d док.) в ОДНОМ процессе"
              % (args.determinism_corpus,
                 "ЗАФИКСИРОВАННОГО СРЕЗА" if args.subset else "ВСЕГО корпуса",
                 len(paths)))
        hs = []
        for i in range(args.determinism_corpus):
            t = time.perf_counter()
            h, n = run_paths(paths)
            hs.append(h)
            print("  прогон %d: %.1f c, масок %d, sha %s" % (i + 1, time.perf_counter() - t, n, h),
                  flush=True)
        print("РАЗНЫХ ХЕШЕЙ: %d  ->  %s" % (len(set(hs)), "OK" if len(set(hs)) == 1 else "ПРОВАЛ"))

    if args.determinism_large:
        p = os.path.join(FIX, "synthetic_corporate_large.docx")
        print("ДЕТЕРМИНИЗМ: %d прогонов %s в ОДНОМ процессе"
              % (args.determinism_large, os.path.basename(p)))
        hs = []
        for i in range(args.determinism_large):
            t = time.perf_counter()
            h, n = run_paths([p])
            hs.append(h)
            print("  прогон %d: %.1f c, масок %d, sha %s" % (i + 1, time.perf_counter() - t, n, h),
                  flush=True)
        print("РАЗНЫХ ХЕШЕЙ: %d  ->  %s" % (len(set(hs)), "OK" if len(set(hs)) == 1 else "ПРОВАЛ"))

    if args.memory:
        load = args.memory
        if load == "large":
            paths = [os.path.join(FIX, "synthetic_corporate_large.docx")]
        elif load == "styles":
            paths = [os.path.join(FIX, "synthetic_many_styles.docx")]
        else:
            limit = int(load.split(":")[1]) if ":" in load else None
            names = corpus_names(args.subset, limit)
            paths = [os.path.join(DOCS, f) for f in names]
        p_imp, _ = mem()
        t = time.perf_counter()
        h, n = run_paths(paths)
        dt = time.perf_counter() - t
        peak, cur = mem()
        print("@@MEM@@" + json.dumps({"load": load, "peak_mb": peak, "cur_mb": cur,
                                      "peak_import_mb": p_imp, "seconds": dt, "sha": h}))


if __name__ == "__main__":
    main()
