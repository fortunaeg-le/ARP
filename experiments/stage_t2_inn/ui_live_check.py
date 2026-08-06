# -*- coding: utf-8 -*-
"""
ui_live_check.py — ЭТАП T2-INN, шаг 4: приёмка экрана ЖИВЫМ HTTP-запросом.

ПОЧЕМУ НЕ ПРЯМОЙ ВЫЗОВ `core.settings_view()`. Урок этапа T1-UI (docs/archive/reports/T1_UI_REPORT.md):
на Windows `http.server` умел молча забиндиться на занятый порт, и на запросы
отвечал СТАРЫЙ процесс со старым кодом — прямой вызов функции в этом случае
показывал бы правильный ответ, которого пользователь не видит. Поэтому здесь
поднимается настоящий сервер на СВОБОДНОМ порту, и всё общение идёт по HTTP.

Хранилище настроек и сессий подменено на временную директорию (`USERPROFILE`/
`HOME` — реальный `~/.shifrator` не трогается вообще).

Что проверяется (числа печатаются, не утверждаются словами):
  1. `GET /api/settings` — новый тип ПОЯВИЛСЯ на экране сам, с человеческой
     подписью, и сервер сам считает, в какие наборы он входит;
  2. набор «только персональные данные» на документе корпуса, где есть ОБА ИНН:
     ИНН человека замаскирован, ИНН организации — нет;
  3. галочка «ИНН организации» включает второй; галочка «ИНН человека»
     выключает первый — маски появляются и исчезают.

Запуск:
    venv/Scripts/python.exe experiments/stage_t2_inn/ui_live_check.py
"""
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APP = os.path.join(ROOT, "app")
CORPUS_DOCS = os.path.join(ROOT, "tests", "corpus", "docs")

DOC_ID = "agency_0003"          # .txt корпуса, где есть и 10-значный, и 12-значный ИНН


#: АДРЕС СЕРВЕРА БЕРЁТСЯ ИЗ ЕГО СОБСТВЕННОГО ВЫВОДА, а не задаётся клиентом.
#: На машине владельца уже живут ЧУЖИЕ, давно запущенные серверы (проверено:
#: порты 8791 и 8828 заняты процессами со старым кодом — один отдаёт подписи
#: типов, загруженные до правок этапа, другой вовсе не знает про /api/settings).
#: `server.py` при занятом порте молча берёт следующий (`find_free_port`), и
#: клиент, постучавшийся по ЗАРАНЕЕ ВЫБРАННОМУ порту, разговаривает с чужим
#: процессом — ровно это и случилось на первом прогоне этой проверки.
#: Поэтому: сервер запускается с `-u` (иначе строка адреса зависает в буфере
#: pipe'а), адрес читается из его stdout, и дополнительно сверяется, что
#: `settings_path` лежит в НАШЕЙ временной домашней директории — то есть отвечает
#: именно наш процесс с нашим окружением. Сверять pid НЕЛЬЗЯ:
#: `venv/Scripts/python.exe` — стаб-лаунчер, он запускает настоящий интерпретатор
#: ОТДЕЛЬНЫМ процессом, и `Popen.pid` никогда не совпадёт с `os.getpid()` сервера.
_URL_RE = __import__("re").compile(r"http://127\.0\.0\.1:(\d+)/")


def wait_for_url(proc, timeout=90):
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise SystemExit("сервер завершился, не напечатав адрес")
            continue
        m = _URL_RE.search(line.decode("utf-8", "replace"))
        if m:
            return int(m.group(1))
    raise SystemExit("сервер не напечатал адрес за %d с" % timeout)


def _post(url, payload=None, raw=None, headers=None):
    data = raw if raw is not None else json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    home = tempfile.mkdtemp(prefix="shifrator_t2_ui_")
    env = dict(os.environ)
    env["USERPROFILE"] = home      # Path.home() на Windows смотрит сюда
    env["HOME"] = home
    env["SHIFRATOR_UI_PORT"] = "8901"   # предпочтение; фактический порт читаем из вывода
    env["PYTHONIOENCODING"] = "utf-8"

    # `server.py` при старте открывает браузер. Подменяем BROWSER на безвредную
    # команду: окно проверяющему не нужно, а сам сервер обязан быть настоящим.
    env["BROWSER"] = os.path.join(ROOT, "venv", "Scripts", "python.exe") + " -c pass"
    proc = subprocess.Popen(
        [os.path.join(ROOT, "venv", "Scripts", "python.exe"), "-u",
         os.path.join(APP, "server.py")],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        port = wait_for_url(proc)
        base = f"http://127.0.0.1:{port}"
        ping = _get(base + "/api/ping")
        probe = _get(base + "/api/settings")
        if not str(probe.get("settings_path", "")).startswith(home):
            raise SystemExit(
                "на порту %d отвечает ЧУЖОЙ процесс (его настройки: %s, ждали внутри "
                "%s) — проверка недействительна" % (port, probe.get("settings_path"), home))
        print("сервер наш: build=%s pid=%s порт=%d, настройки в %s"
              % (ping["build_mark"], ping["pid"], port, probe["settings_path"]))

        # --- 1. Экран настроек -------------------------------------------- #
        view = _get(base + "/api/settings")
        rows = {t["type"]: t for t in view["types"]}
        print("\n1. ЭКРАН «Что маскировать» — ответ сервера, %d типов:" % len(view["types"]))
        for t in ("INN", "INN_PERSON"):
            r = rows.get(t)
            print("   %-11s %s" % (t, "ОТСУТСТВУЕТ НА ЭКРАНЕ" if r is None else r))
        print("   наборы:")
        for p in view["profiles"]:
            print("     %-22s %s" % (p["id"], p["hint"]))

        # --- 2/3. Наборы на живом документе -------------------------------- #
        path = os.path.join(CORPUS_DOCS, DOC_ID + ".txt")
        raw = open(path, "rb").read()
        gold = {d["doc_id"]: d for d in json.load(
            open(os.path.join(ROOT, "tests", "corpus", "gold.json"), encoding="utf-8"))}[DOC_ID]
        inn10 = [e["text"] for e in gold["entities"] if e["type"] == "INN"]
        inn12 = [e["text"] for e in gold["entities"] if e["type"] == "INN_PER"]
        print("\nДокумент %s: ИНН организаций %s, ИНН человека %s"
              % (DOC_ID, inn10, inn12))

        cases = [
            ("только персональные данные", "personal", {}),
            ("то же + галочка «ИНН организации»", "personal", {"INN": True}),
            ("то же − галочка «ИНН человека»", "personal", {"INN_PERSON": False}),
            ("максимум", "maximum", {}),
        ]
        for title, profile, overrides in cases:
            saved = _post(base + "/api/settings", {"profile": profile, "types": overrides})
            assert saved["status"] == "ok", saved
            res = _post(base + "/api/encrypt", raw=raw,
                        headers={"X-Filename": DOC_ID + ".txt",
                                 "Content-Type": "application/octet-stream"})
            assert res["status"] == "ok", res
            counts = collections.Counter(r["entity_type"] for r in res["replacements"])
            anon = res["anon_text"]
            print("\n%s:" % title)
            print("   масок всего: %d;  ИНН организации: %d;  ИНН человека: %d"
                  % (sum(counts.values()), counts.get("INN", 0), counts.get("INN_PERSON", 0)))
            for v in inn10:
                print("   ИНН организации %s в тексте: %s" % (v, "ОСТАЛСЯ" if v in anon else "замаскирован"))
            for v in inn12:
                print("   ИНН человека    %s в тексте: %s" % (v, "ОСТАЛСЯ" if v in anon else "замаскирован"))
            pol = res["policy"]
            print("   экран проверки: набор «%s», полный: %s" % (pol["profile_label"], pol["is_full"]))
            print("   осталось намеренно: %s" % (", ".join(pol["disabled_labels"]) or "—"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
