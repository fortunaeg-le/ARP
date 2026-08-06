# -*- coding: utf-8 -*-
"""Хронометр: автоматический замер длительности КАЖДОЙ команды оболочки.

Зачем. Хронометраж сессии до сих пор вёлся памятью и оценкой, и этап A2 на этом
уже обжёгся: разбивка времени была реконструирована задним числом и подана как
измерение. Прибор должен работать сам, без пункта в задании и без того, чтобы о
нём помнили.

Как. Один и тот же файл вешается на два события:

    PreToolUse  (Bash|PowerShell) — кладёт заявку с меткой старта;
    PostToolUse (Bash|PowerShell) — снимает заявку, считает разность,
                                    дописывает строку в журнал.

Ключ заявки — sha1(session_id + текст команды). На один ключ может висеть
несколько заявок одновременно (одинаковая команда в фоне) — поэтому заявка
хранится СТОПКОЙ, и Post снимает самую раннюю (FIFO): длительности не
перепутываются местами, даже если совпали команды.

ЕДИНЫЙ ИСТОЧНИК ВРЕМЕНИ — системные часы ОС, читаемые `time.time()`. Их же
читает `git commit`, `date`, `Get-Date` и сам Python: расхождения между ними
нет и быть не может (проверено этапом A3, см. JOURNAL). «Расхождение ~25 минут»,
записанное этапом A2, было не сдвигом часов, а фактически прошедшим временем
между двумя вызовами.

Журнал — `.chrono/<session_id>.tsv` внутри проекта, каталог в `.gitignore`:
данные конкретного прогона на конкретной машине, в истории им не место.

Контракт хука: на stdin JSON, выход ВСЕГДА 0. Прибор не имеет права остановить
работу из-за собственной поломки — он только записывает.
"""
import hashlib
import json
import os
import sys
import time

# --- где живёт журнал -------------------------------------------------------

PROJECT = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
CHRONO_DIR = os.path.join(PROJECT, ".chrono")
PENDING_DIR = os.path.join(CHRONO_DIR, "_pending")

#: Колонки журнала. Первая строка файла — заголовок, дальше по строке на команду.
HEADER = ("start", "end", "sec", "tool", "bg", "ok", "command")

#: Команда в журнале — одной строкой и с потолком: журнал читают глазами.
CMD_LIMIT = 400


def stamp(t: float) -> str:
    """Метка времени из ЕДИНСТВЕННОГО источника — системных часов, с явным
    смещением пояса. Без смещения строки из разных сессий несравнимы."""
    local = time.localtime(t)
    off = -(time.altzone if local.tm_isdst else time.timezone)
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return "%s.%03d%s%02d:%02d" % (
        time.strftime("%Y-%m-%dT%H:%M:%S", local),
        int((t % 1) * 1000),
        sign,
        off // 3600,
        (off % 3600) // 60,
    )


def key_of(session: str, command: str) -> str:
    return hashlib.sha1((session + "\x00" + command).encode("utf-8")).hexdigest()[:16]


def flatten(command: str) -> str:
    """Многострочная команда (heredoc, python -c) — в одну строку с потолком."""
    one = " ".join(command.split())
    if len(one) > CMD_LIMIT:
        one = one[: CMD_LIMIT - 1] + "…"
    return one


def push_start(path: str, started: float) -> None:
    """Заявка кладётся ДОПИСЫВАНИЕМ: одинаковые команды в фоне не затирают
    друг друга, а выстраиваются в стопку."""
    os.makedirs(PENDING_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("%.6f\n" % started)


def pop_start(path: str):
    """Снимает САМУЮ РАННЮЮ заявку (FIFO) и возвращает её метку.

    Возвращает None, если заявки нет: так бывает, когда Pre не отработал
    (хук поставлен посреди сессии) — тогда строка пишется без длительности,
    а не пропадает молча.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    first, rest = lines[0], lines[1:]
    try:
        if rest:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(rest) + "\n")
        else:
            os.remove(path)
    except OSError:
        pass
    try:
        return float(first)
    except ValueError:
        return None


def log_path(session: str) -> str:
    safe = "".join(c for c in session if c.isalnum() or c in "-_") or "session"
    return os.path.join(CHRONO_DIR, "%s.tsv" % safe)


def append(session: str, row) -> None:
    os.makedirs(CHRONO_DIR, exist_ok=True)
    path = log_path(session)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("\t".join(HEADER) + "\n")
        fh.write("\t".join(row) + "\n")


def outcome(payload) -> str:
    """«ok» по самоотчёту инструмента. Форма ответа у разных версий харнесса
    разная, поэтому судим осторожно и при незнании пишем «?»."""
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("isError"):
            return "err"
        code = resp.get("exit_code", resp.get("exitCode"))
        if isinstance(code, int):
            return "ok" if code == 0 else "err%d" % code
        if resp.get("interrupted"):
            return "int"
        return "ok"
    return "?"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name", "")
    if tool not in ("Bash", "PowerShell"):
        sys.exit(0)

    args = payload.get("tool_input") or {}
    command = args.get("command") or ""
    if not command:
        sys.exit(0)

    session = str(payload.get("session_id") or "session")
    event = payload.get("hook_event_name") or ""
    path = os.path.join(PENDING_DIR, key_of(session, command))
    now = time.time()

    if event == "PreToolUse":
        push_start(path, now)
        sys.exit(0)

    started = pop_start(path)
    # Фон: инструмент возвращается сразу, а команда продолжает жить. Замер
    # честен только для переднего плана — помечаем, чтобы не сложить одно с другим.
    bg = "bg" if args.get("run_in_background") else "-"
    append(
        session,
        (
            stamp(started) if started is not None else "?",
            stamp(now),
            "%.3f" % (now - started) if started is not None else "?",
            tool,
            bg,
            outcome(payload),
            flatten(command),
        ),
    )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Прибор молчит о своих поломках, но никогда не роняет работу.
        sys.exit(0)
