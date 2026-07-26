# -*- coding: utf-8 -*-
"""
O2 — ПОЛЬЗОВАТЕЛЬСКИЙ СЦЕНАРИЙ: сколько ждёт человек.

Пайплайн меряется в §1-4 без старта процесса, но пользователь десктопной сборки
платит и за старт: загрузку моделей, прогрев, инициализацию. Здесь меряется весь
путь `shifrator.py encrypt <файл>` целиком — от запуска процесса до записи
сессии, — в двух конфигурациях (O2 включена / выключена восстановлением до-O2
реализаций), каждая в своём процессе, минимум из N.

Хранилище сессий подменяется на временный профиль: ~/.shifrator НЕ трогается.

ЗАПУСК:
    venv/Scripts/python.exe experiments/stage_o2/probe_user_scenario.py --repeat 3
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIX = os.path.join(ROOT, "tests", "fixtures")
DOCS = os.path.join(ROOT, "tests", "corpus", "docs")


def worker(variant, path):
    t0 = time.perf_counter()
    sys.path.insert(0, os.path.join(ROOT, "src"))
    sys.path.insert(0, HERE)
    import extractor
    import ner_detector      # noqa: F401
    import anchor_registry   # noqa: F401
    import tokenizer         # noqa: F401
    import syntax_compound   # noqa: F401
    from pipeline import run_detection
    from tokenizer import tokenize
    from session_store import save_session
    t_import = time.perf_counter() - t0

    if variant == "off_all":
        from ab_bench import disable
        disable("all")

    storage = tempfile.mkdtemp(prefix="o2_user_")
    try:
        t1 = time.perf_counter()
        doc = extractor.extract(path)
        ents = run_detection(doc, os.path.join(ROOT, "entity_types.yaml"))
        anon, kept = tokenize(doc, ents, os.path.join(ROOT, "entity_types.yaml"))
        save_session(kept, session_id=None, ttl_hours=24, storage_dir=storage)
        t_doc = time.perf_counter() - t1
    finally:
        shutil.rmtree(storage, ignore_errors=True)

    print("@@R@@" + json.dumps({
        "variant": variant, "import_s": t_import, "doc_s": t_doc,
        "total_s": t_import + t_doc, "n_masks": len(kept),
        "sha": hashlib.sha256(anon.encode("utf-8")).hexdigest(),
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--worker")
    ap.add_argument("--path")
    args = ap.parse_args()
    if args.worker:
        worker(args.worker, args.path)
        return

    targets = [
        ("крупный договор (5051 сегм.)", os.path.join(FIX, "synthetic_corporate_large.docx")),
        ("многостилье (721 сегм.)", os.path.join(FIX, "synthetic_many_styles.docx")),
        ("обычный документ корпуса", os.path.join(DOCS, "lease_0004.docx")),
    ]
    print("=== ПОЛЬЗОВАТЕЛЬСКИЙ СЦЕНАРИЙ: запуск процесса + шифрование одного файла ===")
    print("%-30s %-9s %9s %9s %9s %s"
          % ("документ", "вариант", "старт, c", "док., c", "ВСЕГО, c", "выход"))
    for title, path in targets:
        if not os.path.exists(path):
            continue
        best = {}
        for _ in range(args.repeat):
            for v in ("off_all", "o2"):
                out = subprocess.run(
                    [sys.executable, os.path.abspath(__file__),
                     "--worker", v, "--path", path],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", cwd=ROOT)
                line = [l for l in out.stdout.splitlines() if l.startswith("@@R@@")]
                if not line:
                    print("!!! %s/%s упал: %s" % (title, v, (out.stderr or out.stdout)[-800:]))
                    continue
                r = json.loads(line[0][5:])
                if v not in best or r["total_s"] < best[v]["total_s"]:
                    best[v] = r
        if len(best) != 2:
            continue
        ref = best["o2"]["sha"]
        for v in ("off_all", "o2"):
            r = best[v]
            print("%-30s %-9s %9.2f %9.2f %9.2f %s"
                  % (title if v == "off_all" else "", v, r["import_s"], r["doc_s"],
                     r["total_s"], "идентичен" if r["sha"] == ref else "!!! ОТЛИЧАЕТСЯ"))
        a, b = best["off_all"], best["o2"]
        print("%-30s %-9s %+8.1f%% %+8.1f%% %+8.1f%%   масок %d"
              % ("", "дельта",
                 (b["import_s"] - a["import_s"]) / a["import_s"] * 100,
                 (b["doc_s"] - a["doc_s"]) / a["doc_s"] * 100,
                 (b["total_s"] - a["total_s"]) / a["total_s"] * 100, b["n_masks"]))
        print()


if __name__ == "__main__":
    main()
