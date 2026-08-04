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


def check_shell(raw_command: str):
    command = strip_payload(raw_command)
    if PUSH.search(command):
        deny(
            "ЗАБЛОКИРОВАНО (замок push). Отправка в удалённый репозиторий — решение\n"
            "владельца, не сессии. Коммить сколько нужно, push не делать.\n"
            "См. CLAUDE.md, запрет 1.\n"
        )

    low = norm(command)
    # Санкционированная дорога к baseline: инструмент сам себе разрешение.
    sanctioned = "promote_baseline.py" in low

    protected = FROZEN_CORPUS + GATE_CONFIG + (() if sanctioned else BASELINE)
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
