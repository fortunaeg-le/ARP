"""ЭТАП DESKTOP-FIT — окно приложения НА СОБРАННОЙ ПРОГРАММЕ (живая проба).

Круг приёмки ходит по HTTP и по безголовому браузеру — лаунчер и его окно он не
трогает вовсе. А класс «в рабочем дереве работает, в сборке нет» у этого проекта
уже был (FIX-3: путь к entity_types.yaml терялся в sys._MEIPASS). Поэтому окно
проверяется на самом dist/SHIFRATOR.exe:

  1. exe поднимается с ИЗОЛИРОВАННЫМ профилем (подменённый USERPROFILE),
  2. на профиле окна появляются процессы браузера — окно открылось,
  3. exe убивается принудительно (худший случай, не штатный «Выход»),
  4. процессы окна обязаны уйти вместе с ним — объект задания срабатывает
     и при аварийном конце процесса, не только при штатном.

Запуск: venv/Scripts/python.exe experiments/desktop_fit_built_probe.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "SHIFRATOR" / "SHIFRATOR.exe"


def procs_on(profile_mark: str) -> int:
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedge.exe' "
           "-and $_.CommandLine -like '*" + profile_mark + "*' } | Measure-Object | % Count")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True).stdout.strip()
    try:
        return int(out or 0)
    except ValueError:
        return -1


def main():
    if not EXE.exists():
        raise SystemExit(f"нет собранной программы: {EXE}")

    home = tempfile.mkdtemp(prefix="shf-window-")
    env = dict(os.environ, USERPROFILE=home, HOME=home)
    mark = os.path.basename(home)
    print(f"изолированный профиль: {home}")

    proc = subprocess.Popen([str(EXE)], env=env, cwd=str(EXE.parent))
    try:
        opened = 0
        for _ in range(120):
            time.sleep(1.0)
            opened = procs_on(mark)
            if opened > 0:
                break
        print(f"процессов окна на профиле программы: {opened}")
        print("ОКНО:", "открылось собранной программой" if opened > 0 else "НЕ ОТКРЫЛОСЬ")

        lock = Path(home) / ".shifrator" / "launcher.lock"
        print(f"файл-замок на месте: {lock.exists()}")
        wprof = Path(home) / ".shifrator" / "window-profile"
        print(f"профиль окна рядом с замком: {wprof.is_dir()}")
    finally:
        # Убиваем ЖЁСТКО: проверяем именно аварийный конец программы.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3.0)

    left = procs_on(mark)
    print(f"после смерти программы осталось процессов окна: {left}")
    print("ЗОМБИ:", "нет" if left == 0 else "ЕСТЬ — окно пережило программу")


if __name__ == "__main__":
    main()
