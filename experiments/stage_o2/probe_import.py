# -*- coding: utf-8 -*-
"""
O2 — стоимость СТАРТА процесса: время импорта стека и память моделей.

Меряет одноразовую часть (её платит каждый запуск CLI и exe, и она же —
холодный старт десктопной сборки), отдельно от обработки документов.

Варианты:
  after   — как сейчас: один NewsEmbedding на процесс (O2-6);
  before  — как было: syntax_compound строил СВОЙ NewsEmbedding; воспроизводим
            это, достроив вторую копию поверх текущего стека.
Каждый вариант — свой процесс (пик рабочего множества не сбрасывается).
"""
import ctypes
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


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
    _gpmi(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 1048576.0, c.WorkingSetSize / 1048576.0


def worker(variant):
    """Обе конфигурации собираются ЯВНО и одинаково, отличается ровно один шаг —
    на КАКОМ эмбеддинге строятся морфо-тэггер и синтаксический парсер.

    `syntax_compound` намеренно НЕ импортируется: иначе «before» получил бы и
    общий эмбеддинг с его тэггерами, и вторую копию сверху — то есть больше, чем
    было в коде до O2, и выигрыш оказался бы завышен."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    t = time.perf_counter()
    import extractor          # noqa: F401
    import ner_detector       # тут живёт общий _emb + NER-тэггер + AddrExtractor
    import anchor_registry    # noqa: F401
    import tokenizer          # noqa: F401
    import natasha
    from natasha import Doc

    if variant == "before":
        emb = natasha.NewsEmbedding()          # ВТОРАЯ копия — как было до O2-6
    else:
        emb = ner_detector._emb                # общая — как стало
    segmenter = natasha.Segmenter()
    morph_tagger = natasha.NewsMorphTagger(emb)
    syntax_parser = natasha.NewsSyntaxParser(emb)
    warm = Doc("прогрев модели")               # тот же прогрев, что в модуле
    warm.segment(segmenter)
    warm.tag_morph(morph_tagger)
    warm.parse_syntax(syntax_parser)
    dt = time.perf_counter() - t
    globals()["_keep"] = (emb, morph_tagger, syntax_parser)
    peak, cur = mem()
    print("@@R@@" + json.dumps({"variant": variant, "import_s": dt,
                                "peak_mb": peak, "cur_mb": cur}))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        worker(sys.argv[2])
        return
    rows = []
    for v in ("before", "after"):
        best = None
        for _ in range(3):
            out = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", v],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", cwd=ROOT)
            line = [l for l in out.stdout.splitlines() if l.startswith("@@R@@")]
            if not line:
                print("!!! %s упал:\n%s\n%s" % (v, out.stdout[-1200:], out.stderr[-1200:]))
                break
            r = json.loads(line[0][5:])
            if best is None or r["import_s"] < best["import_s"]:
                best = r
        if best:
            rows.append(best)
    print("\n=== СТАРТ ПРОЦЕССА: импорт стека детекции (минимум из 3) ===")
    print("%-8s %14s %12s %12s" % ("вариант", "импорт, c", "пик, МБ", "текущ, МБ"))
    for r in rows:
        print("%-8s %14.2f %12.0f %12.0f" % (r["variant"], r["import_s"], r["peak_mb"], r["cur_mb"]))
    if len(rows) == 2:
        b, a = rows
        print("O2-6: импорт %.2f -> %.2f c (%+.1f%%), память %.0f -> %.0f МБ (%.0f МБ, %+.1f%%)"
              % (b["import_s"], a["import_s"],
                 (a["import_s"] - b["import_s"]) / b["import_s"] * 100,
                 b["cur_mb"], a["cur_mb"], a["cur_mb"] - b["cur_mb"],
                 (a["cur_mb"] - b["cur_mb"]) / b["cur_mb"] * 100))


if __name__ == "__main__":
    main()
