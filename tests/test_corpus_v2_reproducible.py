# -*- coding: utf-8 -*-
"""
Побайтная воспроизводимость корпуса V2.

ЗАЧЕМ ОТДЕЛЬНЫЙ ТЕСТ, И ПОЧЕМУ ПРЕЖНИХ ДОКАЗАТЕЛЬСТВ НЕ ХВАТАЛО.

Корпус обязан пересобираться байт в байт: на этом держатся `MANIFEST.sha256`,
сравнимость замеров между сессиями и вся сверка «корпус не менялся до и после
прогона». Доказывалось это раньше так: пересобрали — `gold_v2.json` совпал по
sha256. Проверка мимо цели: **разметка не содержит всего, что попадает в
файл**. Идентификатор фигуры текстбокса в неё не попадает, он считался
встроенным `hash(str)` — а тот рандомизируется при каждом запуске
интерпретатора (PYTHONHASHSEED). Десять `.docx` выходили разными байтами при
каждой пересборке, `gold_v2.json` при этом совпадал, и «доказательство»
показывало зелёное.

Второй суррогат — `test_docx_bytes_do_not_depend_on_the_process` в
`test_corpus_v2_structure_groups.py`: он пересобирает XML из модели В ТОМ ЖЕ
процессе и только для документов с текстбоксом. Он ловит именно `hash()`,
потому что автор знал, где искать; класс дефектов целиком он не закрывает.

Здесь проверка прямая и тупая: **корпус собирается ДВАЖДЫ, двумя разными
процессами, и сравниваются контрольные суммы ВСЕХ порождённых файлов** —
`.docx` целиком (вместе с zip-обёрткой, временами файлов и порядком записей),
`.txt`, модели и `gold_v2.json`. Расхождение печатает имя файла.

Сборка идёт во ВРЕМЕННЫЙ каталог: рабочий корпус тест не трогает и не может
испортить, даже упав.

Семена хеширования задаются ЯВНО и РАЗНЫЕ. По умолчанию Python и так их
рандомизирует, но тогда тест ловил бы дефект случайно — с вероятностью, что
два прогона не совпали. Явные разные семена делают обнаружение обязательным.
"""
import hashlib
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_V2 = os.path.join(ROOT, "tests", "corpus_v2")

# Исходники генератора. Всё остальное (docs/, _model/, gold_v2.json, манифест)
# — результат сборки, копировать его во временный каталог нельзя: тогда
# сравнивалась бы копия с копией.
SOURCES = ("generate.py", "corpus_lib.py", "values.py", "data.py",
           # ЭТАП TYPE-FACTORY-2: генератор читает файлы-описания типов
           "typedef_lib.py")

#: Каталог файлов-описаний — тоже ИСХОДНИК генератора (фабричные типы
#: вплетаются из typedefs/*.yaml). Копируется деревом.
SOURCE_DIRS = ("typedefs",)

# Разные семена хеширования — см. шапку.
SEEDS = ("0", "12345")


def _sha_tree(root):
    """{относительный путь -> sha256} по всем порождённым файлам."""
    out = {}
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and d not in SOURCE_DIRS]
        for n in sorted(names):
            if n.endswith(".pyc") or n in SOURCES:
                continue
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, root).replace("\\", "/")
            with open(p, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def _build(tmpdir, seed):
    work = os.path.join(tmpdir, "seed" + seed)
    os.makedirs(work)
    for name in SOURCES:
        shutil.copy(os.path.join(CORPUS_V2, name), os.path.join(work, name))
    for d in SOURCE_DIRS:
        src_dir = os.path.join(CORPUS_V2, d)
        if os.path.isdir(src_dir):
            shutil.copytree(src_dir, os.path.join(work, d))
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "generate.py"], cwd=work, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert r.returncode == 0, (
        "Сборка корпуса с PYTHONHASHSEED=%s упала:\n%s\n%s"
        % (seed, r.stdout[-2000:], r.stderr[-2000:]))
    return _sha_tree(work)


def test_corpus_rebuilds_byte_for_byte(tmp_path):
    """Две сборки разными процессами обязаны дать одинаковые байты."""
    a = _build(str(tmp_path), SEEDS[0])
    b = _build(str(tmp_path), SEEDS[1])

    assert a, "Первая сборка не породила ни одного файла — тест ничего не проверил."
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    assert not only_a and not only_b, (
        "Сборки породили РАЗНЫЕ НАБОРЫ файлов.\n"
        "  только в первой: %s\n  только во второй: %s"
        % (only_a[:10], only_b[:10]))

    differing = sorted(k for k in a if a[k] != b[k])
    assert not differing, (
        "Корпус НЕ воспроизводится побайтно: %d файлов из %d различаются между "
        "двумя сборками (PYTHONHASHSEED=%s и %s).\n%s\n"
        "На побайтной воспроизводимости держатся MANIFEST.sha256 и сравнимость "
        "замеров между сессиями. Ищите значение, зависящее от состояния "
        "интерпретатора или от времени, а не от модели."
        % (len(differing), len(a), SEEDS[0], SEEDS[1],
           "\n".join("    " + f for f in differing[:20])))


def test_rebuild_matches_the_committed_corpus(tmp_path):
    """Собранное заново совпадает с тем, что лежит в репозитории.

    Иначе воспроизводимость была бы «сама с собой»: две одинаковые сборки, обе
    расходящиеся с зафиксированным корпусом. Именно так выглядела бы
    незамеченная правка генератора без пересборки — и `MANIFEST.sha256`
    рассказывал бы про корпус, которого генератор больше не порождает.
    """
    fresh = _build(str(tmp_path), SEEDS[0])
    committed = _sha_tree(CORPUS_V2)
    # MANIFEST.sha256 и README сборкой не порождаются — сравнивать нечего.
    committed = {k: v for k, v in committed.items() if k in fresh}

    missing = sorted(set(fresh) - set(committed))
    differing = sorted(k for k in committed if committed[k] != fresh[k])
    assert not missing, (
        "Генератор порождает файлы, которых нет в репозитории: %s.\n"
        "Корпус не пересобран после правки генератора." % missing[:10])
    assert not differing, (
        "%d файлов в репозитории отличаются от того, что порождает генератор "
        "СЕЙЧАС:\n%s\n"
        "Либо генератор правили без пересборки корпуса, либо корпус правили "
        "мимо генератора. И то и другое обесценивает MANIFEST.sha256."
        % (len(differing), "\n".join("    " + f for f in differing[:20])))
