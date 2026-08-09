"""Круг приёмки на СОБРАННОЙ программе (правило ACCEPT-FIX, см. CLAUDE.md).

Одна команда: (пере)собирает exe, поднимает его с ИЗОЛИРОВАННЫМ профилем
(подменённый USERPROFILE — реальный ~/.shifrator не трогается), гоняет
шифрование/восстановление/повторное открытие сессии по id через тот же
HTTP API, что и десктоп-интерфейс, гасит процесс. Печатает ПРОШЁЛ/УПАЛ и
шаг, на котором упало — тесты и гейт этот путь не видят по построению,
они ходят по рабочему дереву (см. FIX-3, docs/JOURNAL.md).

Запуск:
    venv\\Scripts\\python.exe packaging\\accept_circle.py               # собрать и прогнать
    venv\\Scripts\\python.exe packaging\\accept_circle.py --skip-build  # прогнать существующий dist/
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_EXE = ROOT / "dist" / "SHIFRATOR" / "SHIFRATOR.exe"
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
PYINSTALLER = ROOT / "venv" / "Scripts" / "pyinstaller.exe"
SPEC = ROOT / "packaging" / "shifrator.spec"
SAMPLE_DOC = ROOT / "tests" / "corpus" / "docs" / "agency_0003.txt"

STEP = 0


def report(ok, name, detail=""):
    global STEP
    STEP += 1
    mark = "ОК" if ok else "ПАДЕНИЕ"
    print(f"[{STEP}] {name}: {mark}" + (f" — {detail}" if detail else ""))
    if not ok:
        print(f"КРУГ УПАЛ на шаге {STEP}: {name}")
        sys.exit(1)


def build():
    proc = subprocess.run(
        [str(PYINSTALLER), str(SPEC), "--distpath", str(ROOT / "dist"),
         "--workpath", str(ROOT / "build"), "--noconfirm"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900,
    )
    ok = proc.returncode == 0 and DIST_EXE.exists()
    if not ok:
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
    report(ok, "сборка pyinstaller")


def wait_for_lock(profile_dir, timeout=30):
    lock_path = profile_dir / ".shifrator" / "launcher.lock"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if lock_path.exists():
            try:
                return json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        time.sleep(0.2)
    return None


def http(method, url, payload=None, headers=None, timeout=15):
    data = None
    if payload is not None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    if not args.skip_build:
        build()
    else:
        report(DIST_EXE.exists(), "dist/SHIFRATOR.exe присутствует (--skip-build)")

    profile_dir = Path(tempfile.mkdtemp(prefix="shifrator_accept_"))
    env = dict(os.environ)
    env["USERPROFILE"] = str(profile_dir)
    env["HOME"] = str(profile_dir)  # Path.home() на Windows смотрит USERPROFILE; HOME для симметрии

    proc = None
    try:
        proc = subprocess.Popen([str(DIST_EXE)], cwd=str(profile_dir), env=env)

        lock = wait_for_lock(profile_dir)
        report(lock is not None, "лаунчер поднялся (launcher.lock)")
        port = lock["port"]

        pinged = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                pinged = http("GET", f"http://127.0.0.1:{port}/api/ping")
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.3)
        report(pinged is not None, "сервер отвечает на /api/ping", str(pinged))

        original = SAMPLE_DOC.read_text(encoding="utf-8")
        body = original.encode("utf-8")
        try:
            enc = http("POST", f"http://127.0.0.1:{port}/api/encrypt", payload=body,
                        headers={"X-Filename": "agency_0003.txt", "X-Allow-Lossy": "0",
                                 "Content-Type": "text/plain"}, timeout=120)
        except urllib.error.HTTPError as e:
            enc = {"status": "error", "message": f"HTTP {e.code}: {e.read()[:500]}"}
        ok = enc.get("status") == "ok" and enc.get("session_id") and enc.get("anon_text")
        report(ok, "шифрование документа", enc.get("message", ""))
        session_id = enc["session_id"]
        anon_text = enc["anon_text"]

        n_masks = len(re.findall(r"\[[A-Z_]+_\d+\]", anon_text))
        report(n_masks > 0, "маски расставлены", f"{n_masks} шт.")

        dec = http("POST", f"http://127.0.0.1:{port}/api/decrypt",
                    payload={"session_id": session_id, "text": anon_text})
        ok = dec.get("status") == "ok" and not dec.get("unresolved")
        report(ok, "восстановление из ответа", f"unresolved={dec.get('unresolved')}")
        leftover = len(re.findall(r"\[[A-Z_]+_\d+\]", dec.get("restored", "")))
        report(leftover == 0, "токенов в восстановленном тексте нет", f"{leftover} шт.")

        sessions = http("GET", f"http://127.0.0.1:{port}/api/sessions")
        ids = [s.get("session_id") for s in sessions.get("sessions", [])]
        report(session_id in ids, "сессия видна в списке")

        dec2 = http("POST", f"http://127.0.0.1:{port}/api/decrypt",
                     payload={"session_id": session_id, "text": anon_text})
        report(dec2.get("status") == "ok" and dec2.get("restored") == dec.get("restored"),
               "сессия повторно открывается по id")

        print("КРУГ ПРОЙДЕН")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
