# -*- coding: utf-8 -*-
"""Этап AUDIT — сторожа приборов: то, что раньше проверялось только руками.

Повод (задание этапа AUDIT): за один блок нашлось ТРИ инструмента, проверявших
не то, что заявлено. Общее у всех трёх — заявление живёт в документе, а
проверка либо слабее заявления, либо не запускается вовсе. Здесь собраны те
проверки, которые обязаны идти обычным `pytest -q`:

  1. **Замки хука** (`.claude/hooks/guard.py`). Само-тест хука существовал
     (`.claude/hooks/selftest.py`), но запускался ТОЛЬКО руками — то есть
     замки, объявленные в `CLAUDE.md` как «нельзя никогда», не проверялись
     ничем в обычном прогоне. Здесь он вызывается как подпроцесс.
  2. **Журнал точки отсчёта среза** (`tests/corpus/iter_baseline_ledger.json`).
     `results_iter_baseline.json` можно было переснять бесследно. Теперь его
     хеш обязан быть назван последней записью журнала.

Строки команд собираются из кусков там, где иначе сторож заблокировал бы сам
файл теста (`git pu` + `sh`) — тот же приём, что в `selftest.py`.
"""
import json
import os
import subprocess
import sys
import zipfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GUARD = os.path.join(_ROOT, ".claude", "hooks", "guard.py")
_SELFTEST = os.path.join(_ROOT, ".claude", "hooks", "selftest.py")
_CORPUS = os.path.join(_ROOT, "tests", "corpus")

sys.path.insert(0, _CORPUS)


def _guard(tool, arg, cwd=None):
    """Код возврата хука: 2 — заблокировал, 0 — пропустил."""
    payload = {"tool_name": tool,
               "tool_input": ({"command": arg} if tool in ("Bash", "PowerShell")
                              else {"file_path": arg})}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    p = subprocess.run([sys.executable, _GUARD],
                       input=json.dumps(payload).encode("utf-8"),
                       capture_output=True)
    return p.returncode, p.stderr.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# 1. Замки хука идут обычным прогоном, а не по памяти автора
# --------------------------------------------------------------------------- #

def test_hook_selftest_passes():
    """Само-тест хука — в обычном прогоне.

    Раньше он лежал мёртвым: файл есть, в `pytest` его никто не звал, и
    сломанный замок обнаружился бы только у того, кто вспомнит про ручной
    запуск. Это ровно класс «прибор, который не включают».
    """
    p = subprocess.run([sys.executable, _SELFTEST], capture_output=True)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")


BLOCKED = [
    # Обычная отправка разрешена с 2026-08-17 (CLAUDE.md, запрет 1) — замок
    # сужен до ПРИНУДИТЕЛЬНОЙ: только --force/--force-with-lease/-f/+refspec.
    ("Bash", "git pu" + "sh --force origin main"),
    ("Bash", "git pu" + "sh -f origin main"),
    ("Bash", "git pu" + "sh origin +main"),
    ("Edit", "tests/corpus/docs/agency_0001.txt"),
    ("Write", "tests/corpus/gold.json"),
    ("Edit", "tests/corpus/gate_config.py"),
    ("Write", "tests/corpus/results_baseline.json"),
    # ЭТАП AUDIT: срезовая точка отсчёта раньше не охранялась вовсе.
    ("Write", "tests/corpus/results_iter_baseline.json"),
    ("Bash", "cp d.json tests/corpus/results_iter_baseline.json"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/run_iter_baseline.py"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/update_manifest.py"),
    # ЭТАП AUDIT: запись защищённого файла ИНТЕРПРЕТАТОРОМ, мимо глаголов оболочки.
    ("Bash", "venv/Scripts/python.exe -c \"open('tests/corpus/gold.json','w')\""),
    ("Bash", "python -c \"import shutil;shutil.copy('d.json','tests/corpus/results_baseline.json')\""),
]

ALLOWED = [
    # Обычная отправка разрешена — CLAUDE.md, запрет 1 (решение владельца
    # 2026-08-17). Заблокирована только принудительная (см. BLOCKED выше).
    ("Bash", "git pu" + "sh origin main"),
    ("Bash", "head -5 tests/corpus/gold.json"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/gate.py"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/subsample.py"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/update_manifest.py --check"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/promote_iter_baseline.py "
             "--author a --reason b --delta x"),
    ("Bash", "venv/Scripts/python.exe tests/corpus/promote_baseline.py d.json "
             "--author a --reason b"),
    ("Edit", "src/regex_detector.py"),
    ("Write", "docs/STATE.md"),
]


@pytest.mark.parametrize("tool,arg", BLOCKED, ids=[a[:48] for _, a in BLOCKED])
def test_guard_blocks(tool, arg):
    code, err = _guard(tool, arg)
    assert code == 2, "хук ПРОПУСТИЛ то, что объявлено запретом: %s" % arg
    assert "ЗАБЛОКИРОВАНО" in err


@pytest.mark.parametrize("tool,arg", ALLOWED, ids=[a[:48] for _, a in ALLOWED])
def test_guard_allows(tool, arg):
    """Сторож не имеет права мешать работе: чтение и штатные дороги проходят."""
    code, err = _guard(tool, arg)
    assert code == 0, "хук заблокировал разрешённое: %s\n%s" % (arg, err)


# --------------------------------------------------------------------------- #
# 2. Точка отсчёта среза не двигается мимо журнала
# --------------------------------------------------------------------------- #

def test_iter_baseline_matches_its_ledger():
    """`results_iter_baseline.json` обязан быть назван последней записью журнала.

    Замок независим от хука и от прогона корпуса: пересъёмка любым способом —
    инструментом, скриптом, руками — меняет хеш, и, если записи нет, этот тест
    краснеет. Именно этого не хватало: движение полного корпуса журналируется
    (`overmask_ledger.json`), а движение среза до этапа AUDIT не оставляло следа.
    """
    import promote_iter_baseline as PIB
    ok, msg = PIB.check(verbose=False)
    assert ok, msg


def test_iter_ledger_entries_are_justified():
    """Каждая запись журнала — с автором, причиной и хешем.

    И ни одна причина не построена на цвете гейта: единственное основание не
    вносить запись — «запись не про этот предмет».
    """
    import promote_iter_baseline as PIB
    ledger = PIB.load_ledger()
    assert ledger["entries"], "журнал пуст"
    for e in ledger["entries"]:
        for field in ("date", "author", "kind", "sha256", "reason", "evidence"):
            assert e.get(field), "запись %s без поля %s" % (e.get("date"), field)
        assert not PIB._REFUSED_REASON.search(e["reason"]), (
            "обоснование записи %s построено на цвете гейта" % e["date"])
    # Цепочка хешей: каждая следующая запись знает предыдущую.
    for prev, cur in zip(ledger["entries"], ledger["entries"][1:]):
        assert cur.get("sha256_before") == prev["sha256"], (
            "разрыв цепочки журнала на записи %s: между ней и предыдущей было "
            "неучтённое движение точки отсчёта" % cur["date"])


# --------------------------------------------------------------------------- #
# 3. Правило 7 (реальные документы, guard.py:check_documents) — таблица
#    «вход -> блок/пуск» на все семь правил guard.py в обычном pytest (долг
#    GUARD-SELFTEST). Раньше её гоняла руками только сессия GUARD-PUSH через
#    `.claude/hooks/selftest.py` (DOC_CASES) — здесь ровно те же три признака,
#    но как утверждения pytest, а не аггрегированный exit-код скрипта.
#
#    Правила 1-6 — в BLOCKED/ALLOWED выше. Пробы правила 7 идут в СВОЁМ
#    временном репозитории (`tmp_path`, вне рабочего дерева): git add/commit —
#    настоящие изменяющие команды, гонять их по рабочему дереву означало бы
#    пачкать его сам тест. pytest убирает `tmp_path` сам — дерево репозитория
#    результат пробы не видит вообще.
# --------------------------------------------------------------------------- #

def _ooxml(path):
    """Минимальный .docx: ZIP с внутренним именем word/document.xml."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")


def _init_probe_repo(root):
    subprocess.run(["git", "init", "-q"], cwd=str(root), capture_output=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "corpus" / "docs").mkdir(parents=True, exist_ok=True)


def test_guard_blocks_real_document_office_extension_outside_corpus(tmp_path):
    """Признак 1: офисный формат вне известных путей корпуса."""
    _init_probe_repo(tmp_path)
    _ooxml(tmp_path / "docs" / "dogovor.docx")
    code, err = _guard("Bash", "git add -A", cwd=tmp_path)
    assert code == 2, "хук ПРОПУСТИЛ офисный файл вне корпуса"
    assert "ЗАБЛОКИРОВАНО" in err


def test_guard_blocks_real_document_oversized_corpus_file(tmp_path):
    """Признак 2: файл в каталоге корпуса вне диапазона корпусных размеров."""
    _init_probe_repo(tmp_path)
    (tmp_path / "tests" / "corpus" / "docs" / "big.docx").write_bytes(
        b"PK" + b"0" * (70 * 1024))
    code, err = _guard("Bash", "git commit -m x", cwd=tmp_path)
    assert code == 2, "хук ПРОПУСТИЛ корпусный файл вне диапазона размеров"
    assert "ЗАБЛОКИРОВАНО" in err


def test_guard_blocks_real_document_disguised_as_other_extension(tmp_path):
    """Признак 3: OOXML-архив под чужим (не офисным) расширением."""
    _init_probe_repo(tmp_path)
    _ooxml(tmp_path / "docs" / "notes.bin")
    code, err = _guard("Bash", "git add docs/", cwd=tmp_path)
    assert code == 2, "хук ПРОПУСТИЛ договор под чужим именем"
    assert "ЗАБЛОКИРОВАНО" in err


def test_guard_allows_commit_without_office_files(tmp_path):
    """Сторож не имеет права мешать коммиту без офисных файлов."""
    _init_probe_repo(tmp_path)
    (tmp_path / "readme.txt").write_text("обычный текстовый файл", encoding="utf-8")
    code, err = _guard("Bash", "git add -A", cwd=tmp_path)
    assert code == 0, "хук заблокировал коммит без офисных файлов\n%s" % err
