# -*- coding: utf-8 -*-
"""exe_live_check.py — ЭТАП BUILD, шаг 4: приёмка СОБРАННОГО приложения.

Запускается именно `dist/SHIFRATOR/SHIFRATOR.exe`, а не исходники: смысл этапа —
убедиться, что накопленные изменения (T1-UI, T2-INN, ADDR-B, U4-FIX) попали в
поставку. Общение — по HTTP с живым сервером внутри exe.

ПОЧЕМУ АДРЕС НЕ ЗАДАЁТСЯ КЛИЕНТОМ. На машине владельца висят чужие старые
копии (проверено netstat: 8765, 8791, 8828 заняты). `find_free_port` возьмёт
следующий свободный, и клиент, постучавшийся по заранее выбранному порту,
поговорит с ЧУЖИМ процессом — это уже случалось на этапе T2-INN. Поэтому порт
читается из `launcher.lock`, который exe пишет в СВОЮ (временную) домашнюю
директорию, и дополнительно сверяется, что `/api/settings` отдаёт путь внутри
этой же временной директории, а `/api/ping` — ожидаемую метку сборки.

Запуск:
    venv/Scripts/python.exe experiments/stage_build/exe_live_check.py
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
EXE = os.path.join(ROOT, "dist", "SHIFRATOR", "SHIFRATOR.exe")
CORPUS = os.path.join(ROOT, "tests", "corpus", "docs")
GOLD = os.path.join(ROOT, "tests", "corpus", "gold.json")

EXPECTED_BUILD = "build-20260729"
DOC_ID = "agency_0003"          # .txt: есть и 10-значный ИНН, и 12-значный


def _get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_raw(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _post(url, payload=None, raw=None, headers=None, timeout=300):
    data = raw if raw is not None else json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_for_lock(lock_path, proc, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(lock_path):
            try:
                data = json.loads(open(lock_path, encoding="utf-8").read())
                if int(data.get("port", -1)) > 0:
                    return data
            except (OSError, ValueError):
                pass
        if proc.poll() is not None:
            raise SystemExit("exe завершился, не написав launcher.lock (код %s)" % proc.poll())
        time.sleep(0.5)
    raise SystemExit("launcher.lock не появился за %d с" % timeout)


def _zone_doc():
    """Первый .docx корпуса, у которого gold объявляет непрочитанную зону."""
    # признаки корпуса -> вид зоны (тот же словарь, что в tests/test_unread_zones.py)
    zone_features = {"hdr_pii", "ftr_pii", "footnote_pii", "textbox_pii"}
    for d in json.load(open(GOLD, encoding="utf-8")):
        if d["format"] == "docx" and zone_features & set(d["features"]):
            return d["doc_id"]
    return None


def main():
    if not os.path.exists(EXE):
        raise SystemExit("нет собранного приложения: %s" % EXE)

    home = tempfile.mkdtemp(prefix="shifrator_build_")
    env = dict(os.environ)
    env["USERPROFILE"] = home
    env["HOME"] = home
    env["SHIFRATOR_UI_PORT"] = "8934"    # предпочтение; фактический берём из lock
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE), env=env)
    lock_path = os.path.join(home, ".shifrator", "launcher.lock")
    ok = True
    try:
        lock = wait_for_lock(lock_path, proc)
        port = int(lock["port"])
        base = "http://127.0.0.1:%d" % port
        ping = _get(base + "/api/ping")
        probe = _get(base + "/api/settings")
        if not str(probe.get("settings_path", "")).startswith(home):
            raise SystemExit("на порту %d отвечает ЧУЖОЙ процесс (настройки %s)"
                             % (port, probe.get("settings_path")))
        print("=" * 78)
        print("ПРОВЕРКА 1. Приложение запустилось, интерфейс отдаётся.")
        print("  порт: %d   метка сборки в /api/ping: %s   (в launcher.lock: %s)"
              % (port, ping["build_mark"], lock.get("build_mark")))
        html = _get_raw(base + "/").decode("utf-8", "replace")
        print("  GET / : %d байт, метка в HTML: %s"
              % (len(html), EXPECTED_BUILD if EXPECTED_BUILD in html else "НЕ НАЙДЕНА"))
        ok &= ping["build_mark"] == EXPECTED_BUILD and EXPECTED_BUILD in html

        # --- 2. Обезличивание документа корпуса --------------------------- #
        raw = open(os.path.join(CORPUS, DOC_ID + ".txt"), "rb").read()
        res = _post(base + "/api/encrypt", raw=raw,
                    headers={"X-Filename": DOC_ID + ".txt",
                             "Content-Type": "application/octet-stream"})
        print("\nПРОВЕРКА 2. Обезличивание документа корпуса %s (набор по умолчанию)." % DOC_ID)
        print("  статус: %s;  масок: %d;  сессия: %s"
              % (res["status"], len(res["replacements"]), res.get("session_id")))
        print("  типы масок: %s"
              % dict(collections.Counter(r["entity_type"] for r in res["replacements"])))
        ok &= res["status"] == "ok" and len(res["replacements"]) > 0
        session_id = res["session_id"]
        anon_text = res["anon_text"]

        # --- 3. Экран выбора набора ---------------------------------------- #
        view = _get(base + "/api/settings")
        rows = {t["type"]: t for t in view["types"]}
        print("\nПРОВЕРКА 3. Экран «Что маскировать».")
        print("  наборов: %d" % len(view["profiles"]))
        for p in view["profiles"]:
            print("     %-22s %s" % (p["id"], p["label"]))
        print("  галочек типов: %d, среди них:" % len(view["types"]))
        for t in ("INN", "INN_PERSON"):
            print("     %-11s %s" % (t, rows.get(t, "ОТСУТСТВУЕТ")))
        ok &= len(view["profiles"]) == 4 and "INN" in rows and "INN_PERSON" in rows

        # --- 4. Переключение набора меняет результат ----------------------- #
        gold = {d["doc_id"]: d for d in json.load(open(GOLD, encoding="utf-8"))}[DOC_ID]
        inn10 = [e["text"] for e in gold["entities"] if e["type"] == "INN"]
        inn12 = [e["text"] for e in gold["entities"] if e["type"] == "INN_PER"]
        print("\nПРОВЕРКА 4. Переключение набора. ИНН организаций %s, ИНН человека %s"
              % (inn10, inn12))
        seen = {}
        for title, profile, overrides in (
            ("только персональные данные", "personal", {}),
            ("то же + галочка «ИНН организации»", "personal", {"INN": True}),
            ("то же − галочка «ИНН человека»", "personal", {"INN_PERSON": False}),
            ("максимум", "maximum", {}),
        ):
            saved = _post(base + "/api/settings", {"profile": profile, "types": overrides})
            assert saved["status"] == "ok", saved
            r = _post(base + "/api/encrypt", raw=raw,
                      headers={"X-Filename": DOC_ID + ".txt",
                               "Content-Type": "application/octet-stream"})
            c = collections.Counter(x["entity_type"] for x in r["replacements"])
            txt = r["anon_text"]
            seen[title] = (c.get("INN", 0), c.get("INN_PERSON", 0))
            print("  %s:" % title)
            print("     масок %d; ИНН орг. масок %d; ИНН чел. масок %d"
                  % (sum(c.values()), c.get("INN", 0), c.get("INN_PERSON", 0)))
            for v in inn10:
                print("     ИНН организации %s: %s" % (v, "ОСТАЛСЯ в тексте" if v in txt else "замаскирован"))
            for v in inn12:
                print("     ИНН человека    %s: %s" % (v, "ОСТАЛСЯ в тексте" if v in txt else "замаскирован"))
            print("     экран проверки: набор «%s», намеренно не маскируется: %s"
                  % (r["policy"]["profile_label"],
                     ", ".join(r["policy"]["disabled_labels"]) or "—"))
        ok &= (seen["только персональные данные"][0] == 0
               and seen["то же + галочка «ИНН организации»"][0] > 0
               and seen["то же − галочка «ИНН человека»"][1] == 0)

        # --- 5. Отчёт ------------------------------------------------------- #
        # Отчёт считается ПО ЗАПИСЯМ РАЗМЕТКИ (состав включённых типов берётся
        # из слепка `census.policy` записи), поэтому сначала — щелчок по маске
        # «это не ПДн», как это делает пользователь на экране проверки. Без
        # него отчёт пуст: profile=unknown, by_build=[] — это не дефект сборки.
        m = __import__("re").search(
            r'<mark class="ent t-(\w+)" data-ri="\d+" data-seg="([^"]+)" data-start="(\d+)" '
            r'data-end="(\d+)" data-token="([^"]+)"', res["anon_html"])
        if m is None:
            print("\nПРОВЕРКА 5: не удалось найти маску в разметке anon-панели")
            ok = False
        else:
            fp = _post(base + "/api/markup/false-positive",
                       {"session_id": session_id, "segment_id": m.group(2),
                        "start": int(m.group(3)), "end": int(m.group(4)), "token": m.group(5)})
            print("\n  (подготовка отчёта: отмечена ложная маска %s -> %s)"
                  % (m.group(5), fp.get("status")))
        rep = _get(base + "/api/report?scope=all", timeout=120)
        print("\nПРОВЕРКА 5. Отчёт программы.")
        if rep.get("status") == "error":
            print("  ОШИБКА: %s" % rep["message"])
            ok = False
        else:
            r = rep["report"]
            print("  статус: ok;  tool_build: %s;  разбивка by_build: %s"
                  % (r["tool_build"], list(r["by_build"].keys())))
            print("  состав включённых типов (policy): набор=%s, включено=%s"
                  % (r["policy"]["profile"], r["policy"]["enabled_types"]))
            head = "\n".join(rep["md_text"].splitlines()[:16])
            print("  --- начало текста отчёта ---")
            print("\n".join("  " + s for s in head.splitlines()))
            ok &= r["tool_build"] == EXPECTED_BUILD

        # --- 6. Восстановление --------------------------------------------- #
        back = _post(base + "/api/decrypt", {"session_id": session_id, "text": anon_text})
        plain = open(os.path.join(CORPUS, DOC_ID + ".txt"), encoding="utf-8").read()
        print("\nПРОВЕРКА 6. Восстановление текста из токенов (сессия %s)." % session_id)
        print("  статус: %s" % back.get("status"))
        restored = back.get("restored", "")
        print("  восстановлено символов: %d;  нерешённых токенов: %s"
              % (len(restored), back.get("unresolved")))
        # Побайтного равенства с исходным документом НЕ ждём: канон восстановления
        # этапа E возвращает ФИО в КАНОНИЧЕСКОЙ форме (им. падеж, инициалы
        # раскрыты) — «Виноградова Павла Юрьевича» -> «Виноградов Павел Юрьевич».
        # Проверено: то же самое даёт прогон на ИСХОДНИКАХ, к сборке отношения не
        # имеет. Содержательный критерий: не осталось нерешённых токенов и ни
        # одной маски в тексте.
        print("  побайтно совпадает с исходным документом: %s (канон E — не ждём)"
              % (restored.strip() == plain.strip()))
        left = [r["token"] for r in res["replacements"] if r["token"] in restored]
        print("  токенов-масок осталось в тексте: %d %s" % (len(left), left))
        vals = [r["original_text"] for r in res["replacements"]]
        print("  исходных значений вернулось на место: %d из %d"
              % (sum(1 for v in vals if v in restored), len(vals)))
        ok &= (back.get("status") == "ok" and not back.get("unresolved")
               and not left and len(restored) > 0)

        # --- 7. Непрочитанные зоны ------------------------------------------ #
        zd = _zone_doc()
        print("\nПРОВЕРКА 7. Документ с непрочитанными зонами (%s)." % zd)
        if zd is None:
            print("  в корпусе не нашлось документа с зонами — проверка НЕ выполнена")
            ok = False
        else:
            zraw = open(os.path.join(CORPUS, zd + ".docx"), "rb").read()
            zres = _post(base + "/api/encrypt", raw=zraw,
                         headers={"X-Filename": zd + ".docx",
                                  "Content-Type": "application/octet-stream"})
            print("  статус: %s" % zres["status"])
            for z in zres.get("zones", []):
                print("     зона: %s (%s), символов %s" % (z["kind_label"], z["part"], z["char_count"]))
            ok &= zres["status"] == "refused" and len(zres.get("zones", [])) > 0

        print("\n" + "=" * 78)
        print("ИТОГ: %s" % ("все семь проверок пройдены" if ok else "ЕСТЬ ПРОВАЛЕННЫЕ ПРОВЕРКИ"))
        print("проверялось на порту %d" % port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
