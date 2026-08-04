# -*- coding: utf-8 -*-
"""Само-тест сторожа: живая проверка, что каждый замок срабатывает — и что
разрешённое проходит.

Запуск:  python .claude/hooks/selftest.py

Скрипт запускает guard.py ОТДЕЛЬНЫМ процессом ровно так, как его зовёт хук:
JSON вызова инструмента на stdin, ожидаемый код возврата — 2 (заблокировать)
или 0 (пропустить). Строки команд собираются из кусков — иначе сторож,
установленный в этой же сессии, заблокировал бы сам файл теста.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py")

PUSH = "git pu" + "sh origin main"          # собрано, чтобы не ловить себя
PUSH_QUOTED = 'bash -c "git pu' + 'sh"'
BASELINE = "tests/corpus/results_" + "baseline.json"
GOLD = "tests/corpus/" + "gold.json"
GATECFG = "tests/corpus/gate_" + "config.py"

CASES = [
    # (что проверяем, payload, ожидаемый код)
    ("push — прямой",
     {"tool_name": "Bash", "tool_input": {"command": PUSH}}, 2),
    ("push — спрятанный в кавычки",
     {"tool_name": "Bash", "tool_input": {"command": PUSH_QUOTED}}, 2),
    ("корпус v1: запись в gold.json",
     {"tool_name": "Write", "tool_input": {"file_path": "C:/Jesus/ARP/" + GOLD}}, 2),
    ("корпус v1: правка документа корпуса",
     {"tool_name": "Edit", "tool_input": {"file_path": "tests/corpus/docs/agency_0001.txt"}}, 2),
    ("корпус v1: удаление документа командой",
     {"tool_name": "Bash", "tool_input": {"command": "rm -f tests/corpus/docs/agency_0001.txt"}}, 2),
    ("допуски гейта: правка gate_config.py",
     {"tool_name": "Edit", "tool_input": {"file_path": GATECFG}}, 2),
    ("точка отсчёта: прямая запись",
     {"tool_name": "Write", "tool_input": {"file_path": BASELINE}}, 2),
    ("точка отсчёта: перезапись копированием",
     {"tool_name": "Bash", "tool_input": {"command": "cp dump.json " + BASELINE}}, 2),
    ("точка отсчёта: перенаправление вывода",
     {"tool_name": "Bash", "tool_input": {"command": "echo x > " + BASELINE}}, 2),
    ("манифест корпуса: правка мимо инструмента",
     {"tool_name": "Write", "tool_input": {"file_path": "tests/corpus/MANIFEST.sha256"}}, 2),

    # --- разрешённое: сторож не имеет права мешать работе ---
    ("РАЗРЕШЕНО: promote_baseline.py — санкционированная дорога",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/promote_baseline.py d.json --author a --reason b"}}, 0),
    ("РАЗРЕШЕНО: чтение эталона",
     {"tool_name": "Bash", "tool_input": {"command": "head -5 " + GOLD}}, 0),
    ("РАЗРЕШЕНО: прогон гейта",
     {"tool_name": "Bash", "tool_input": {"command": "venv/Scripts/python.exe tests/corpus/gate.py"}}, 0),
    ("РАЗРЕШЕНО: обычный коммит",
     {"tool_name": "Bash", "tool_input": {"command": "git commit -m \"правка доков\""}}, 0),
    ("РАЗРЕШЕНО: коммит, ТЕКСТ которого описывает замок",
     {"tool_name": "Bash", "tool_input": {"command":
      "git commit -F - <<EOF\nDOC: замки\n\n  - git pu" + "sh в любом виде;\nEOF"}}, 0),
    ("РАЗРЕШЕНО: правка документа вне защищённых зон",
     {"tool_name": "Write", "tool_input": {"file_path": "docs/STATE.md"}}, 0),
    ("РАЗРЕШЕНО: правка исходника детектора",
     {"tool_name": "Edit", "tool_input": {"file_path": "src/regex_detector.py"}}, 0),
]


def run(payload):
    p = subprocess.run([sys.executable, GUARD],
                       input=json.dumps(payload).encode("utf-8"),
                       capture_output=True)
    return p.returncode, p.stderr.decode("utf-8", "replace").strip()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bad = 0
    for name, payload, expect in CASES:
        code, err = run(payload)
        ok = (code == expect)
        bad += (not ok)
        mark = "OK  " if ok else "ПРОВАЛ"
        print("%s  ожидали exit=%d, получили %d  |  %s" % (mark, expect, code, name))
        if code == 2 and ok:
            print("        %s" % err.splitlines()[0])
        if not ok and err:
            print("        %s" % err.splitlines()[0])
    print()
    print("Проверок: %d, провалов: %d" % (len(CASES), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
