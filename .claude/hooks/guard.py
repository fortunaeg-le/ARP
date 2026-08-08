# -*- coding: utf-8 -*-
"""Сторож PreToolUse: превращает запреты CLAUDE.md в физическую блокировку.

CLAUDE.md — это контекст, а не конфигурация: модель его читает и обычно
слушается, но проигнорировать может. Всё, что в проекте объявлено «нельзя
никогда», проверяется здесь и блокируется до выполнения инструмента.

Контракт хука: на stdin — JSON вызова (`tool_name`, `tool_input`), выход 0 —
пропустить, выход 2 — ЗАБЛОКИРОВАТЬ (текст со stderr возвращается модели).
Любая внутренняя ошибка сторожа — выход 0: сторож не имеет права остановить
работу из-за собственной поломки, он только запрещает названное.

Что блокируется:
  1. `git push` в любом виде.
  2. Запись/правка/удаление в замороженном корпусе v1: `tests/corpus/docs/**`
     и `tests/corpus/gold.json`.
  3. Правка констант допусков `tests/corpus/gate_config.py`.
  4. Правка точки отсчёта `tests/corpus/results_baseline.json` и
     `tests/corpus/MANIFEST.sha256` мимо `promote_baseline.py`.
  5. (этап AUDIT) Пересъёмка точки отсчёта СРЕЗА
     `tests/corpus/results_iter_baseline.json` мимо `promote_iter_baseline.py`,
     включая обе обходные дороги — прямой запуск `run_iter_baseline.py` и
     `update_manifest.py` (последний без `--check`).
  6. (этап AUDIT) Запись защищённого файла ИНТЕРПРЕТАТОРОМ. Замеряно живьём:
     `python -c "open('tests/corpus/gold.json','w')"` проходил все замки —
     в команде нет ни глагола оболочки, ни перенаправления, а имя файла хук
     видел и молчал. Теперь такая команда считается изменяющей.
  7. (этап FIX-2) Попадание РЕАЛЬНОГО ДОКУМЕНТА в историю — по СОДЕРЖИМОМУ,
     а не по имени: `git add`/`git commit`/`git stash` осматривают то, что
     уедет в коммит. До этого запрет 7 держался списком игнорирования по
     именам и уже не сработал (`DOG-REPO-BLOB`: договор попал в публичную
     историю под чужим именем). Признаки — см. блок `OFFICE_EXT` ниже.

Чтение не блокируется НИКОГДА — ни одного из этих файлов.
"""
import json
import os
import re
import subprocess
import sys

# Текст блокировки уходит в stderr и оттуда — модели. Консоль Windows по
# умолчанию cp1251: без этого кириллица приезжает мусором.
for _stream in (sys.stderr, sys.stdout):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# --- защищаемые пути (в нормализованном виде: '/' и нижний регистр) ---------

FROZEN_CORPUS = (
    "tests/corpus/docs/",
    "tests/corpus/gold.json",
)
GATE_CONFIG = ("tests/corpus/gate_config.py",)
BASELINE = (
    "tests/corpus/results_baseline.json",
    "tests/corpus/manifest.sha256",
)
#: Точка отсчёта быстрого набора. Двигается только `promote_iter_baseline.py`,
#: который дописывает запись в `iter_baseline_ledger.json` (этап AUDIT: до него
#: срез можно было переснять бесследно — журнал вёлся только для полного корпуса).
ITER_BASELINE = ("tests/corpus/results_iter_baseline.json",)
#: Скрипты, которые ДВИГАЮТ охраняемое сами по себе: их запуск и есть правка.
#: `update_manifest.py --check` ничего не пишет и разрешён отдельно.
ITER_TOOLS = (
    "tests/corpus/run_iter_baseline.py",
    "run_iter_baseline.py",
    "tests/corpus/update_manifest.py",
    "update_manifest.py",
)

# --- замок на РЕАЛЬНЫЕ ДОКУМЕНТЫ ПО СОДЕРЖИМОМУ (этап FIX-2) ---------------
#
# ПОЧЕМУ ИМЁН НЕ ХВАТИЛО. Запрет 7 корневого CLAUDE.md («реальные документы не
# коммитить») держался списком игнорирования по ИМЕНАМ — и уже не сработал:
# реальный договор попал в историю публичного репозитория под чужим именем
# (`DOG-REPO-BLOB`, вычищен локально 2026-08-05, на сервере остаётся). Дорога
# обхода была известна заранее — собственный комментарий запрета просит не
# носить дампы в служебные каталоги, — но проверки на неё не существовало.
#
# ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НАРОЧНО НЕТ. Это ДЕШЁВЫЙ РУБЕЖ, а не поиск ПДн:
# совершенство здесь хуже отсутствия — тяжёлая проверка на каждом коммите
# будет отключена первой же сессией, а отключённый замок не ловит ничего.
# Ловится очевидное, тремя признаками:
#   1. ОФИСНЫЙ ФОРМАТ ВНЕ ИЗВЕСТНЫХ ПУТЕЙ КОРПУСА — по расширению;
#   2. РАЗМЕР ВНЕ ДИАПАЗОНА КОРПУСНЫХ — порождённые документы 4,4-20 КБ,
#      порог взят с запасом; настоящий договор в этот диапазон не помещается;
#   3. ПРИЗНАКИ ВНУТРИ АРХИВА — файл с ЛЮБЫМ именем, начинающийся сигнатурой
#      ZIP и содержащий `word/document.xml` (или лист Excel, или слайды).
#      Это ровно дорога `DOG-REPO-BLOB`: договор под чужим именем.
#
# Чтение и локальная работа с реальным документом НЕ ограничиваются — замок
# стоит только на `git add`/`git commit`, то есть на попадании В ИСТОРИЮ.
OFFICE_EXT = (".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx", ".potx",
              ".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods", ".odp", ".pdf")
#: Пути, где офисные файлы законны: порождённые корпуса и фикстуры OOXML.
CORPUS_PATHS = ("tests/corpus/docs/", "tests/corpus_v2/docs/", "tests/fixtures/")
#: Верхняя граница размера корпусного документа. Факт на 2026-08-08: корпус v1
#: 4423-10231 байт, корпус v2 7486-20087. Порог с запасом — рубеж должен ловить
#: договор, а не краснеть на выросшем на килобайт генераторе.
CORPUS_MAX_BYTES = 64 * 1024
#: Внутренние имена OOXML: по ним архив опознаётся документом, как бы он ни
#: назывался снаружи.
OOXML_MARKERS = ("word/document.xml", "xl/workbook.xml", "ppt/presentation.xml")
#: Команда, вводящая файл В ИСТОРИЮ. `git commit` без путей тоже считается:
#: коммитится индекс, а в нём уже может лежать добавленное раньше.
GIT_ADDING = re.compile(
    r"(^|[\s|;&(])git(\.exe)?\s+(-\S+\s+|--\S+\s+)*(add|commit|stash)(\s|$)",
    re.IGNORECASE,
)

WRITE_TOOLS = ("Write", "Edit", "NotebookEdit", "MultiEdit")
SHELL_TOOLS = ("Bash", "PowerShell")

#: Глаголы, означающие «этот путь сейчас изменят». Чтение (cat/head/grep/python
#: на чтение) в список не входит намеренно.
MUTATING = re.compile(
    r"(^|[\s|;&(])("
    r"rm|del|erase|mv|move|cp|copy|touch|truncate|tee|dd|"
    r"sed\s+-i|perl\s+-i|"
    r"git\s+(rm|checkout|restore|clean|reset)|"
    r"set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|clear-content"
    r")($|[\s'\"])",
    re.IGNORECASE,
)
#: Перенаправление вывода в файл: `> путь`, `>> путь`.
REDIRECT = re.compile(r">>?\s*['\"]?([^\s'\";|&]+)")

#: Запуск интерпретатора: `python`, `python.exe`, `venv/Scripts/python.exe`, `py`.
INTERPRETER = re.compile(r"(^|[\s|;&(\\/])(python(3|\.exe)?|py)($|[\s'\"])", re.IGNORECASE)
#: Признак записи внутри кода/команды интерпретатора. Намеренно ШИРОКИЙ по
#: письму и УЗКИЙ по чтению: `json.load(open(gold))` обязан проходить, иначе
#: сторож начнёт мешать разбору, ради которого корпус и читают.
PY_WRITE = re.compile(
    r"(open\s*\([^)]*['\"][wax]b?\+?['\"]|\.write\s*\(|json\.dump|"
    r"shutil\.(copy|move)|os\.(remove|unlink|rename|replace)|"
    r"Path\([^)]*\)\.(write_text|write_bytes|unlink|rename|replace))",
    re.IGNORECASE,
)

#: Кавычки в начальном классе — намеренно: `bash -c "git push"` обязан ловиться.
PUSH = re.compile(
    r"""(^|[\s|;&("'])git(\.exe)?\s+(-\S+\s+|--\S+\s+)*push(\s|["']|$)""",
    re.IGNORECASE,
)

#: Тело heredoc: всё между `<<EOF` / `<<'EOF'` и строкой-закрывашкой.
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\n.*?\n\1\s*$", re.DOTALL | re.MULTILINE)
#: Значение аргумента сообщения коммита.
MESSAGE_ARG = re.compile(r"(-m|--message=?)\s*(['\"])(?:\\.|(?!\2).)*\2", re.DOTALL)


def strip_payload(command: str) -> str:
    """Убирает из команды ТЕКСТ, который заведомо не является исполняемым: тело
    heredoc и значение -m/--message.

    Зачем: первое же живое срабатывание сторожа было ЛОЖНЫМ — коммит-сообщение
    описывало сам замок словами «git push», и сторож заблокировал коммит.
    Кавычки в целом НЕ вырезаются: `bash -c "git push"` обязан остаться
    заблокированным, а это ровно кавычки.
    """
    text = HEREDOC.sub(" ", command)
    text = MESSAGE_ARG.sub(" ", text)
    return text


def norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def hits(text: str, targets) -> str | None:
    low = norm(text)
    for t in targets:
        if t in low:
            return t
    return None


def deny(reason: str):
    sys.stderr.write(reason)
    sys.exit(2)


def check_write(path: str):
    where = norm(path)
    t = hits(where, FROZEN_CORPUS)
    if t:
        deny(
            "ЗАБЛОКИРОВАНО (замок корпуса v1). Путь %s лежит в замороженном\n"
            "корпусе: tests/corpus/docs/** и tests/corpus/gold.json не редактируются,\n"
            "не дополняются и не удаляются — на них держится MANIFEST.sha256 и вся\n"
            "точка отсчёта гейта. Чтение разрешено. См. CLAUDE.md, запрет 2.\n" % path
        )
    t = hits(where, GATE_CONFIG)
    if t:
        deny(
            "ЗАБЛОКИРОВАНО (замок допусков гейта). tests/corpus/gate_config.py несёт\n"
            "константы допусков; все они нулевые, и это измеренный факт, а не\n"
            "осторожность. Ослабление допуска = молчаливое принятие регресса.\n"
            "Если планку действительно надо двигать — это promote_baseline.py с\n"
            "обоснованием, а не правка константы. См. CLAUDE.md, запрет 3.\n"
        )
    t = hits(where, BASELINE)
    if t:
        deny(
            "ЗАБЛОКИРОВАНО (замок точки отсчёта). %s меняется ТОЛЬКО через\n"
            "  venv/Scripts/python.exe tests/corpus/promote_baseline.py <dump.json> \\\n"
            "      --author \"кто\" --reason \"почему рост законен\"\n"
            "Инструмент оценит дамп действующим гейтом, откажется при красноте любой\n"
            "линии кроме «д», допишет запись в overmask_ledger.json и пересоберёт\n"
            "манифест. Прямая правка не оставляет ни автора, ни причины.\n"
            "См. CLAUDE.md, запрет 3.\n" % path
        )
    t = hits(where, ITER_BASELINE)
    if t:
        deny(
            "ЗАБЛОКИРОВАНО (замок точки отсчёта СРЕЗА). %s меняется ТОЛЬКО через\n"
            "  venv/Scripts/python.exe tests/corpus/promote_iter_baseline.py \\\n"
            "      --author \"кто\" --reason \"почему\" --delta \"расхождение\" …\n"
            "Инструмент дописывает запись в iter_baseline_ledger.json: автор,\n"
            "причина, хеши до/после и ВСЕ расхождения — в обе стороны. Пересъёмка\n"
            "без записи делает прибор отладки самоподтверждающимся.\n" % path
        )


def _candidate_paths(cwd: str) -> list[str]:
    """Что сейчас уедет в историю: индекс + неотслеживаемые файлы.

    `git status --porcelain` дешёв (на этом репозитории — доли секунды) и не
    требует знать, какие пути перечислены в самой команде: `git add -A`,
    `git add .` и `git commit -a` тоже видны. Любой сбой -> пустой список:
    сторож не имеет права остановить работу из-за собственной поломки.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=cwd or None, capture_output=True, timeout=10,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    paths = []
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        if len(line) < 4:
            continue
        status, rest = line[:2], line[3:]
        if "D" in status:
            continue                     # удаление файла в историю его не вносит
        if " -> " in rest:               # переименование: интересует назначение
            rest = rest.split(" -> ", 1)[1]
        paths.append(rest.strip().strip('"'))
        if len(paths) >= 500:            # рубеж дешёвый: без обхода всего дерева
            break
    return paths


def _looks_like_office_archive(full: str) -> bool:
    """Сигнатура ZIP + внутреннее имя OOXML. Читается ЗАГОЛОВОК архива, не
    содержимое документа: текст договора сторожу видеть незачем."""
    try:
        with open(full, "rb") as fh:
            if fh.read(4) != b"PK\x03\x04":
                return False
        import zipfile
        with zipfile.ZipFile(full) as z:
            names = set(z.namelist()[:400])
        return any(m in names for m in OOXML_MARKERS)
    except Exception:
        return False


def check_documents(command: str, cwd: str):
    """Замок запрета 7 — по СОДЕРЖИМОМУ, а не по имени. См. шапку блока выше."""
    if not GIT_ADDING.search(command):
        return
    for rel in _candidate_paths(cwd):
        low = norm(rel)
        in_corpus = low.startswith(CORPUS_PATHS)
        full = os.path.join(cwd, rel) if cwd else rel
        try:
            size = os.path.getsize(full)
        except Exception:
            continue
        ext_office = low.endswith(OFFICE_EXT)

        if ext_office and not in_corpus:
            deny(
                "ЗАБЛОКИРОВАНО (замок реальных документов, признак 1 — офисный\n"
                "формат вне корпуса). В коммит уходит %s (%d байт).\n\n"
                "Репозиторий ПУБЛИЧНЫЙ, и в его истории УЖЕ была утечка договора\n"
                "(DOG-REPO-BLOB): прошлый запрет держался списком имён и не\n"
                "сработал — документ попал под чужим именем. Офисные файлы\n"
                "законны только в порождённых корпусах: %s\n\n"
                "Если это действительно корпусный документ — положить по месту.\n"
                "Если реальный — он не коммитится вообще, отчёт обезличенно.\n"
                "См. CLAUDE.md, запрет 7.\n"
                % (rel, size, ", ".join(CORPUS_PATHS))
            )
        if in_corpus and size > CORPUS_MAX_BYTES:
            deny(
                "ЗАБЛОКИРОВАНО (замок реальных документов, признак 2 — размер вне\n"
                "диапазона корпусных). %s весит %d байт при потолке %d.\n"
                "Порождённые документы корпуса — 4,4-20 КБ; файл такого размера\n"
                "в каталоге корпуса означает, что туда положили не корпусный\n"
                "документ. См. CLAUDE.md, запрет 7.\n"
                % (rel, size, CORPUS_MAX_BYTES)
            )
        if not in_corpus and not ext_office and _looks_like_office_archive(full):
            deny(
                "ЗАБЛОКИРОВАНО (замок реальных документов, признак 3 — признаки\n"
                "документа ВНУТРИ архива). %s назван не офисным файлом, но это\n"
                "ZIP, содержащий word/document.xml (или лист Excel, или слайды).\n"
                "Это ровно дорога DOG-REPO-BLOB: договор в истории под чужим\n"
                "именем. См. CLAUDE.md, запрет 7.\n" % rel
            )


def check_shell(raw_command: str, cwd: str = ""):
    command = strip_payload(raw_command)
    check_documents(command, cwd)
    if PUSH.search(command):
        deny(
            "ЗАБЛОКИРОВАНО (замок push). Отправка в удалённый репозиторий — решение\n"
            "владельца, не сессии. Коммить сколько нужно, push не делать.\n"
            "См. CLAUDE.md, запрет 1.\n"
        )

    low = norm(command)
    # Санкционированные дороги: инструмент сам себе разрешение. Проверяется
    # ДЛИННОЕ имя первым — "promote_iter_baseline.py" содержит "baseline.py",
    # но не "promote_baseline.py", так что подмены одного другим не выйдет.
    sanctioned_iter = "promote_iter_baseline.py" in low
    sanctioned = "promote_baseline.py" in low or sanctioned_iter

    # Запуск инструмента, который двигает охраняемое сам: сам вызов и есть правка.
    if not sanctioned_iter and hits(low, ITER_TOOLS):
        if not ("update_manifest.py" in low and "--check" in low):
            deny(
                "ЗАБЛОКИРОВАНО (замок точки отсчёта СРЕЗА). Команда двигает\n"
                "results_iter_baseline.json и/или MANIFEST.sha256 мимо журнала:\n\n"
                "    %s\n\n"
                "Штатная дорога — tests/corpus/promote_iter_baseline.py с --author,\n"
                "--reason и --delta: она пересоберёт срез, допишет запись в\n"
                "iter_baseline_ledger.json и пересоберёт манифест сама.\n"
                "Только сверка манифеста (`update_manifest.py --check`) разрешена.\n"
                % command.strip()
            )

    protected = (FROZEN_CORPUS + GATE_CONFIG
                 + (() if sanctioned else BASELINE)
                 + (() if sanctioned_iter else ITER_BASELINE))
    touched = hits(low, protected)
    if not touched:
        return

    mutating = bool(MUTATING.search(command))
    if not mutating:
        for m in REDIRECT.finditer(command):
            if hits(m.group(1), protected):
                mutating = True
                break
    if not mutating:
        # Этап AUDIT: интерпретатор с признаком записи — тоже правка. Замеряно
        # живьём: раньше `python -c "open('…/gold.json','w')"` проходил все замки.
        mutating = bool(INTERPRETER.search(command) and PY_WRITE.search(command))
    if not mutating:
        return  # чтение защищённого файла — разрешено

    deny(
        "ЗАБЛОКИРОВАНО (замок «%s»). Команда изменяет защищённый файл:\n\n    %s\n\n"
        "Замороженный корпус, константы допусков и точка отсчёта гейта не правятся\n"
        "командой оболочки. Движение планки — только promote_baseline.py с --author\n"
        "и --reason. См. CLAUDE.md, запреты 2 и 3.\n" % (touched, command.strip())
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name", "")
    args = payload.get("tool_input") or {}

    try:
        if tool in WRITE_TOOLS:
            path = args.get("file_path") or args.get("notebook_path") or ""
            if path:
                check_write(path)
        elif tool in SHELL_TOOLS:
            command = args.get("command") or ""
            if command:
                check_shell(command, payload.get("cwd") or os.getcwd())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
