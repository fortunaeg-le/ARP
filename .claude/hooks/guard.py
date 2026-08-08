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

Чтение не блокируется НИКОГДА — ни одного из этих файлов.
"""
import json
import re
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


def check_shell(raw_command: str):
    command = strip_payload(raw_command)
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
                check_shell(command)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
