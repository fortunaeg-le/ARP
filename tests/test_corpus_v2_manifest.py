# -*- coding: utf-8 -*-
"""ЭТАП REG — исполняемая проверка манифеста корпуса v2.

ЗАЧЕМ ФАЙЛ. У манифеста v2 (`tests/corpus_v2/MANIFEST.sha256`) не было ни одной
проверки в наборе: `--check` умел работать, но его никто не звал. Из-за этого
дефект состава жил незамеченным — в манифест попадал `results_v2_current.json`,
который лежит в `.gitignore` и перезаписывается каждым замером. Следствие: у
того, кто гонял замер, манифест одного состава, у того, кто не гонял, — другого
(на MERGE-2 это дало 312 против 311 файлов между ветками). Манифест, состав
которого зависит от истории команд конкретной машины, не удостоверяет ничего.

Два стража, и они проверяют РАЗНОЕ:
  1. `test_manifest_check_passes` — сверка байтов: манифест соответствует
     дереву. Это то, ради чего манифест существует.
  2. `test_manifest_has_no_ignored_files` — сверка СОСТАВА: ни один файл
     манифеста не должен быть игнорируемым в git. Это защита от повторения
     класса, а не от одного файла: следующий артефакт прогона поймается сам.
Второй страж намеренно спрашивает git (`git check-ignore`), а не сверяется со
списком имён: список имён пришлось бы обновлять руками, и он разошёлся бы с
`.gitignore` ровно так же, как разошёлся докстринг `make_manifest.py`.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_V2 = os.path.join(ROOT, "tests", "corpus_v2")
MANIFEST = os.path.join(CORPUS_V2, "MANIFEST.sha256")
MAKE = os.path.join(CORPUS_V2, "make_manifest.py")


def _manifest_rows():
    with open(MANIFEST, encoding="utf-8", newline="") as f:
        rows = []
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            rows.append((digest, rel))
    return rows


def test_manifest_exists_and_nonempty():
    assert os.path.exists(MANIFEST), "манифест корпуса v2 отсутствует"
    assert len(_manifest_rows()) > 100, "манифест подозрительно короткий"


def test_manifest_check_passes():
    """`make_manifest.py --check` — 0 расхождений.

    Это и есть та исполняемая проверка, которой в наборе не было: до неё
    манифест v2 мог разойтись с деревом и никто бы не узнал.
    """
    r = subprocess.run([sys.executable, MAKE, "--check"],
                       capture_output=True, cwd=ROOT)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert r.returncode == 0, "манифест v2 разошёлся с деревом:\n" + out


def test_manifest_has_no_ignored_files():
    """Ни одна строка манифеста не указывает на игнорируемый git-ом файл.

    `git check-ignore --stdin` возвращает подмножество переданных путей —
    те, что игнорируются. Пусто = состав манифеста воспроизводим у любого,
    кто клонировал репозиторий, независимо от того, что он у себя гонял.
    """
    rels = [rel for _d, rel in _manifest_rows()]
    paths = "\n".join("tests/corpus_v2/" + rel for rel in rels)
    r = subprocess.run(["git", "check-ignore", "--stdin"],
                       input=paths.encode("utf-8"),
                       capture_output=True, cwd=ROOT)
    # код 0 — что-то игнорируется, 1 — ничего, 128 — git недоступен/не репозиторий
    if r.returncode == 128:
        pytest.skip("git недоступен — состав манифеста проверить нечем")
    ignored = [x for x in r.stdout.decode("utf-8", "replace").splitlines() if x.strip()]
    assert not ignored, (
        "в манифест корпуса v2 попали игнорируемые git-ом файлы: %s.\n"
        "Состав манифеста стал зависеть от того, гонял ли автор замер: "
        "у одного файл есть, у другого нет, и байтовая сверка перестала быть "
        "общим фактом. Исключите артефакт прогона в make_manifest.SKIP_FILES."
        % ", ".join(sorted(ignored)))
