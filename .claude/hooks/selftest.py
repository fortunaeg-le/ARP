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
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py")

#: Временные каталоги проб этапа FIX-2 (см. блок DOC_CASES ниже).
_TMP = tempfile.mkdtemp(prefix="guard_docs_")
_NEUTRAL = tempfile.mkdtemp(prefix="guard_neutral_")   # не репозиторий вовсе

PUSH = "git pu" + "sh origin main"          # собрано, чтобы не ловить себя
#: Замок сужен до ПРИНУДИТЕЛЬНОЙ отправки (CLAUDE.md, запрет 1, решение
#: владельца 2026-08-17) — обычный PUSH выше теперь РАЗРЕШЁН, см. CASES ниже.
FORCE_PUSH = "git pu" + "sh --force origin main"
PUSH_QUOTED = 'bash -c "git pu' + 'sh --force"'
BASELINE = "tests/corpus/results_" + "baseline.json"
GOLD = "tests/corpus/" + "gold.json"
GATECFG = "tests/corpus/gate_" + "config.py"
ITER = "tests/corpus/results_iter_" + "baseline.json"

CASES = [
    # (что проверяем, payload, ожидаемый код)
    ("push — force, прямой",
     {"tool_name": "Bash", "tool_input": {"command": FORCE_PUSH}}, 2),
    ("push — force, спрятанный в кавычки",
     {"tool_name": "Bash", "tool_input": {"command": PUSH_QUOTED}}, 2),
    ("push — обычная отправка разрешена",
     {"tool_name": "Bash", "tool_input": {"command": PUSH}}, 0),
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

    # --- этап AUDIT: точка отсчёта СРЕЗА и запись интерпретатором ---
    ("срез: прямая запись точки отсчёта",
     {"tool_name": "Write", "tool_input": {"file_path": ITER}}, 2),
    ("срез: перезапись копированием",
     {"tool_name": "Bash", "tool_input": {"command": "cp d.json " + ITER}}, 2),
    ("срез: пересъёмка мимо журнала",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/run_iter_" + "baseline.py"}}, 2),
    ("манифест: пересборка мимо инструмента",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/update_" + "manifest.py"}}, 2),
    ("эталон: запись интерпретатором мимо глаголов оболочки",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe -c \"open('" + GOLD + "','w')\""}}, 2),
    ("точка отсчёта: копирование средствами python",
     {"tool_name": "Bash", "tool_input": {"command":
      "python -c \"import shutil;shutil.copy('d.json','" + BASELINE + "')\""}}, 2),

    # --- разрешённое: сторож не имеет права мешать работе ---
    ("РАЗРЕШЕНО: promote_baseline.py — санкционированная дорога",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/promote_baseline.py d.json --author a --reason b"}}, 0),
    ("РАЗРЕШЕНО: чтение эталона",
     {"tool_name": "Bash", "tool_input": {"command": "head -5 " + GOLD}}, 0),
    ("РАЗРЕШЕНО: прогон гейта",
     {"tool_name": "Bash", "tool_input": {"command": "venv/Scripts/python.exe tests/corpus/gate.py"}}, 0),
    ("РАЗРЕШЕНО: обычный коммит",
     {"tool_name": "Bash", "tool_input": {"command": "git commit -m \"правка доков\""},
      "cwd": _NEUTRAL}, 0),
    ("РАЗРЕШЕНО: коммит, ТЕКСТ которого описывает замок",
     {"tool_name": "Bash", "tool_input": {"command":
      "git commit -F - <<EOF\nDOC: замки\n\n  - git pu" + "sh в любом виде;\nEOF"}}, 0),
    ("РАЗРЕШЕНО: правка документа вне защищённых зон",
     {"tool_name": "Write", "tool_input": {"file_path": "docs/STATE.md"}}, 0),
    ("РАЗРЕШЕНО: правка исходника детектора",
     {"tool_name": "Edit", "tool_input": {"file_path": "src/regex_detector.py"}}, 0),
    ("РАЗРЕШЕНО: promote_iter_baseline.py — санкционированная дорога среза",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/promote_iter_" + "baseline.py "
      "--author a --reason b --delta x"}}, 0),
    ("РАЗРЕШЕНО: сверка манифеста без записи",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/update_" + "manifest.py --check"}}, 0),
    ("РАЗРЕШЕНО: чтение эталона средствами python",
     {"tool_name": "Bash", "tool_input": {"command":
      "python -c \"import json;print(len(json.load(open('" + GOLD + "'))))\""}}, 0),
    ("РАЗРЕШЕНО: быстрый набор",
     {"tool_name": "Bash", "tool_input": {"command":
      "venv/Scripts/python.exe tests/corpus/subsample.py"}}, 0),
]


# --- этап FIX-2: замок на реальные документы ПО СОДЕРЖИМОМУ ------------------
# Пробы делаются в СВОЁМ временном репозитории, а не в рабочем дереве: иначе
# результат зависел бы от того, что в дереве валяется в момент прогона, и
# «разрешено» краснело бы через раз. Каталог убирается в finally.


def _ooxml(path):
    """Минимальный .docx: ZIP с внутренним именем word/document.xml."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")


def _build_probe_repo():
    subprocess.run(["git", "init", "-q"], cwd=_TMP, capture_output=True)
    os.makedirs(os.path.join(_TMP, "docs"), exist_ok=True)
    os.makedirs(os.path.join(_TMP, "tests", "corpus", "docs"), exist_ok=True)
    _ooxml(os.path.join(_TMP, "docs", "dogovor.docx"))          # признак 1
    _ooxml(os.path.join(_TMP, "docs", "notes.bin"))             # признак 3
    with open(os.path.join(_TMP, "tests", "corpus", "docs", "big.docx"), "wb") as f:
        f.write(b"PK" + b"0" * (70 * 1024))           # признак 2
    with open(os.path.join(_TMP, "readme.txt"), "w", encoding="utf-8") as f:
        f.write("обычный текстовый файл")


def _probe_repo_with(keep):
    """Оставляет в пробном репозитории ровно один подозрительный файл."""
    for rel in ("docs/dogovor.docx", "docs/notes.bin",
                "tests/corpus/docs/big.docx"):
        full = os.path.join(_TMP, rel)
        if rel != keep and os.path.exists(full):
            os.remove(full)


DOC_CASES = [
    ("реальный документ: офисный формат вне корпуса (признак 1)",
     "docs/dogovor.docx", {"tool_name": "Bash",
                           "tool_input": {"command": "git add -A"},
                           "cwd": _TMP}, 2),
    ("реальный документ: размер вне диапазона корпусных (признак 2)",
     "tests/corpus/docs/big.docx", {"tool_name": "Bash",
                                    "tool_input": {"command": "git commit -m x"},
                                    "cwd": _TMP}, 2),
    ("реальный документ: договор под чужим именем — признаки внутри архива (признак 3)",
     "docs/notes.bin", {"tool_name": "Bash",
                        "tool_input": {"command": "git add docs/"},
                        "cwd": _TMP}, 2),
    ("РАЗРЕШЕНО: коммит без офисных файлов",
     None, {"tool_name": "Bash", "tool_input": {"command": "git add -A"},
            "cwd": _TMP}, 0),
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

    # --- замок на реальные документы: каждая проба в своём состоянии дерева ---
    _build_probe_repo()
    try:
        for name, keep, payload, expect in DOC_CASES:
            _build_probe_repo()
            _probe_repo_with(keep)
            code, err = run(payload)
            ok = (code == expect)
            bad += (not ok)
            print("%s  ожидали exit=%d, получили %d  |  %s"
                  % ("OK  " if ok else "ПРОВАЛ", expect, code, name))
            if err:
                print("        %s" % err.splitlines()[0])
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
        shutil.rmtree(_NEUTRAL, ignore_errors=True)

    print()
    print("Проверок: %d, провалов: %d" % (len(CASES) + len(DOC_CASES), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
