# -*- coding: utf-8 -*-
"""measure_all.py — ОДИН ПРОГОН, из которого собирается отчёт METRICS-FULL.

    venv/Scripts/python.exe experiments/metrics_full/measure_all.py [--skip pytest,...]

ЗАЧЕМ. Требование задания METRICS-FULL: у каждого числа отчёта обязано быть
сказано, на чём оно замерено, и ни одно число не переносится из журнала или из
памяти. Значит замеры обязаны быть сняты ОДНИМ прогоном и оставить после себя
машиночитаемые дампы — их потом читает `collect_metrics.py`. Этот файл — только
запуск и хронометраж: он ничего не считает сам и ничего не правит.

ЧТО ЗАПУСКАЕТ (в этом порядке, последовательно — параллельный запуск исказил бы
хронометраж, а он сам по себе число отчёта, раздел 12):

  gate_v1     tests/corpus/gate.py                — полный гейт, 324 док.;
              пишет tests/corpus/results_gate_current.json (штатное поведение
              гейта, точку отсчёта он не трогает никогда);
  profiles    experiments/metrics_full/run_profiles.py — четыре набора типов;
  v2          tests/corpus_v2/measure_v2.py       — корпус v2, 138 док.;
  by_entity   tests/corpus/by_entity.py           — счёт ПО СУЩНОСТЯМ по дампу
              гейта (быстро, корпус заново не гоняется);
  subsample   tests/corpus/subsample.py           — быстрый срез, ради числа
              «сколько идёт срез» (раздел 12);
  pytest      набор тестов — ради числа тестов и времени набора (раздел 12).

Ненулевой код возврата у гейта (красные линии) и у pytest ОЖИДАЕМ и прогон не
обрывает: отчёт обязан описать красное, а не отказаться сниматься из-за него.
Обрывает только неспособность запустить шаг вовсе.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):  # не-Windows рабочее дерево
    PY = os.path.join(ROOT, "venv", "bin", "python")
RUNS = os.path.join(HERE, "runs.json")

STEPS = [
    ("gate_v1",   [PY, os.path.join(ROOT, "tests", "corpus", "gate.py")],
     "run_gate_v1.log", "полный гейт корпуса v1 (324 док.)"),
    ("profiles",  [PY, os.path.join(HERE, "run_profiles.py")],
     "run_profiles.log", "четыре набора типов на корпусе v1 (324 док.)"),
    ("v2",        [PY, os.path.join(ROOT, "tests", "corpus_v2", "measure_v2.py"),
                   "--out", os.path.join(HERE, "results_v2.json")],
     "run_v2.log", "замер корпуса v2 (138 док.)"),
    ("by_entity", [PY, os.path.join(ROOT, "tests", "corpus", "by_entity.py"),
                   os.path.join(ROOT, "tests", "corpus", "results_gate_current.json")],
     "run_by_entity.log", "счёт по сущностям поверх дампа гейта"),
    ("subsample", [PY, os.path.join(ROOT, "tests", "corpus", "subsample.py")],
     "run_subsample.log", "быстрый срез (33 док.) — ради хронометража"),
    ("pytest",    [PY, "-m", "pytest", "-q"],
     "run_pytest.log", "полный набор тестов — ради числа тестов и времени"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="", help="имена шагов через запятую")
    ap.add_argument("--only", default="", help="имена шагов через запятую")
    args = ap.parse_args()
    skip = {s for s in args.skip.split(",") if s}
    only = {s for s in args.only.split(",") if s}

    runs = {}
    if os.path.exists(RUNS):
        runs = json.load(open(RUNS, encoding="utf-8"))

    for name, cmd, log, what in STEPS:
        if name in skip or (only and name not in only):
            print("== пропуск: %s" % name, flush=True)
            continue
        log_path = os.path.join(HERE, log)
        print("== %s (%s)\n   %s" % (name, what, " ".join(cmd)), flush=True)
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        t0 = time.time()
        with open(log_path, "w", encoding="utf-8") as fh:
            rc = subprocess.call(cmd, cwd=ROOT, stdout=fh,
                                 stderr=subprocess.STDOUT)
        elapsed = time.time() - t0
        runs[name] = {"cmd": cmd, "what": what, "log": log,
                      "started_utc": started, "elapsed_sec": round(elapsed, 1),
                      "exit_code": rc}
        print("   -> код %d, %.1f с, лог %s" % (rc, elapsed, log), flush=True)
        json.dump(runs, open(RUNS, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    print("\nхронометраж -> %s" % RUNS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
