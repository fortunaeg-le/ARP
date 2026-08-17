"""Круг приёмки на СОБРАННОЙ программе (правило ACCEPT-FIX, см. CLAUDE.md).

Одна команда: (пере)собирает exe, поднимает его с ИЗОЛИРОВАННЫМ профилем
(подменённый USERPROFILE — реальный ~/.shifrator не трогается), гоняет
шифрование/восстановление/повторное открытие сессии по id через тот же
HTTP API, что и десктоп-интерфейс, гасит процесс. Печатает ПРОШЁЛ/УПАЛ и
шаг, на котором упало — тесты и гейт этот путь не видят по построению,
они ходят по рабочему дереву (см. FIX-3, docs/JOURNAL.md).

Запуск:
    venv\\Scripts\\python.exe packaging\\accept_circle.py               # собрать и прогнать
    venv\\Scripts\\python.exe packaging\\accept_circle.py --skip-build  # прогнать существующий dist/
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_EXE = ROOT / "dist" / "SHIFRATOR" / "SHIFRATOR.exe"
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
PYINSTALLER = ROOT / "venv" / "Scripts" / "pyinstaller.exe"
SPEC = ROOT / "packaging" / "shifrator.spec"
SAMPLE_DOC = ROOT / "tests" / "corpus" / "docs" / "agency_0003.txt"
# ЭТАП METRIC-FIX, часть 3.2 (долг AUD1-CIRCLE-THIN). Круг гонял ОДИН .txt и
# проверял «масок больше нуля»: деградация 28 -> 1 прошла бы зелёной, а .docx
# через собранную программу не проходил вовсе — то есть python-docx, lxml,
# чтение колонтитулов и сканер зон в СБОРКЕ не проверялись ничем.
SAMPLE_DOCX = ROOT / "tests" / "corpus" / "docs" / "agency_0002.docx"

# PDF-ARCH: круг также гоняет синтетический PDF с текстовым слоем через
# СОБРАННУЮ программу — до этого шага pypdf (и весь PDF-путь) в exe не
# проверялся ничем: тесты и рабочее дерево видят его, сборка — нет (тот же
# класс риска, что и AUD1-CIRCLE-THIN у .docx). Реальных документов в
# репозитории быть не может (запрет 7) — PDF строится на лету низкоуровневыми
# объектами pypdf (см. _make_synthetic_pdf_bytes), без reportlab/внешних
# сервисов.
SYNTHETIC_PDF_PII = "Кузнецова Мария Дмитриевна, ИНН 7707083893"

# ОЖИДАЕМЫЕ ЗНАЧЕНИЯ, а не «сколько-нибудь масок». Взяты из синтетического
# документа корпуса (реальных ПДн в репозитории нет и быть не может). Каждое
# обязано ИСЧЕЗНУТЬ из анонимного текста и вернуться БАЙТ-В-БАЙТ после
# восстановления. Список короткий намеренно: он держит смысл круга (значение
# спрятано и возвращено), а полноту меряет гейт на корпусе.
# ВАЖНО: круг ходит через интерфейс, а интерфейс работает НАБОРОМ ПО УМОЛЧАНИЮ
# («Только персональные данные», type_policy.PERSONAL). Значения вне этого
# набора (ИНН организации, счёт, ОГРН) в анонимном тексте остаются ОТКРЫТЫМИ —
# и это работа по правилам, а не утечка. Первая же редакция этого списка на
# том и споткнулась: ИНН юрлица честно остался в тексте.
EXPECTED_VALUES_TXT = [
    "Кузнецова Мария Дмитриевна",     # ФИО
    "13 10 006794",                   # паспорт
    "+7 (495) 409-87-66",             # телефон
]
# БАЙТ-В-БАЙТ проверяются НЕ ВСЕ спрятанные значения, и это не недосмотр.
# Восстановление текстового пути подставляет КАНОН сущности: «Кузнецовой Марии
# Дмитриевне» возвращается как «Кузнецова Мария Дмитриевна». Это РЕШЕНИЕ о
# продукте (владелец получает документ с именами в именительном), а не дефект,
# и требовать от круга побайтного равенства на склоняемом ФИО значило бы
# требовать отмены канона. Поэтому ФИО обязано ИСЧЕЗНУТЬ (список выше), а
# байт-в-байт спрашивается с несклоняемых значений (список ниже).
# Отдельно: канон обязан сохранять ЛИЧНОСТЬ. Женская фамилия, ставшая мужской
# («Кузнецова» -> «Кузнецов»), — это уже не форма, а чужое имя; находка
# MFIX-CANON-FEMSURN вскрыта этим кругом и ЗАКРЫТА, страж — шаг ниже.
BYTE_EXACT_TXT = ["13 10 006794", "+7 (495) 409-87-66"]
# Канон ФИО обязан вернуть ЭТОГО человека, а не однофамильца другого пола
# (MFIX-CANON-FEMSURN). Проверяется на восстановленном тексте .txt.
CANON_MUST_APPEAR_TXT = ["Кузнецова Мария Дмитриевна"]
CANON_MUST_NOT_APPEAR_TXT = ["Кузнецов Мария", "Кузнецов Марии"]
# У .docx список свой, и в нём НАМЕРЕННО есть значение из ВЕРХНЕГО КОЛОНТИТУЛА
# (почта): именно эту зону система научилась читать этапом NODES, а собранная
# программа её до сих пор не проходила ни разу.
EXPECTED_VALUES_DOCX = [
    "Беляев Олег Олегович",           # ФИО ВНУТРИ ИМЕНИ ИП (MFIX-PROFILE-IP-PER)
    "Беляеву Олегу Олеговичу",        # ФИО из тела (косвенный падеж)
    "366822495272",                   # ИНН физлица (это ПДн, набор по умолчанию)
    "sidorova@mail.ru",               # почта из ВЕРХНЕГО КОЛОНТИТУЛА
]
# «Беляев Олег Олегович» стоит в списке ПЕРВЫМ намеренно: это ФИО внутри имени
# ИП. До починки оно уходило открытым именно в наборе по умолчанию — на
# максимуме его закрывала маска ORG («ИП Беляев Олег Олегович»), а в наборе
# «Только персональные данные» ORG выключен и закрывать было нечем
# (MFIX-PROFILE-IP-PER). Гейт этот класс не видел ПО ПОСТРОЕНИЮ — он меряет на
# максимуме; нашёл его этот круг. Теперь он же его и стережёт.
BYTE_EXACT_DOCX = ["366822495272", "sidorova@mail.ru"]

# Ожидаемое число масок круга. Сверяется ТОЧНО: цифра, не «больше нуля».
# Двигать её можно только вместе с объяснением, что изменилось в системе, —
# ровно как планку гейта. Снята на build-20260729 живым прогоном круга.
EXPECTED_MASKS_TXT = 28
# .docx: 22 -> 24 после починки MFIX-PROFILE-IP-PER. Две новые маски — ровно
# два ФИО внутри имён ИП, которые прежде уходили открытыми; проверено прогоном
# обоих наборов на этом же документе.
EXPECTED_MASKS_DOCX = 24

def _make_synthetic_pdf_bytes(text: str) -> bytes:
    """Минимальный валидный однострочный PDF с текстовым слоем (Helvetica,
    ASCII — латиница/цифры, WinAnsi без встроенного шрифта кириллицу не
    несёт). Строится объектами pypdf (уже в lock, task 1) — reportlab и
    прочие сторонние генераторы не нужны и не заведены.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject()
    font.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    resources = DictionaryObject()
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})
    page[NameObject("/Resources")] = resources

    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
    content_ref = writer._add_object(content)
    page[NameObject("/Contents")] = content_ref

    import io
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


STEP = 0


def report(ok, name, detail=""):
    global STEP
    STEP += 1
    mark = "ОК" if ok else "ПАДЕНИЕ"
    print(f"[{STEP}] {name}: {mark}" + (f" — {detail}" if detail else ""))
    if not ok:
        print(f"КРУГ УПАЛ на шаге {STEP}: {name}")
        sys.exit(1)


def note(ok, name, detail=""):
    """Диагностическая строка круга: печатается всегда, круг НЕ роняет.

    Нужна ровно для одного случая — факт, который обязан быть на виду, но
    объяснён чужим открытым долгом. Прятать его нельзя (тогда долг забудется),
    ронять на нём круг тоже нельзя (вечно красный круг перестают читать)."""
    print(f"[-] {name}: {'ОК' if ok else 'ЗАМЕЧАНИЕ'}" + (f" — {detail}" if detail else ""))


def build():
    proc = subprocess.run(
        [str(PYINSTALLER), str(SPEC), "--distpath", str(ROOT / "dist"),
         "--workpath", str(ROOT / "build"), "--noconfirm"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900,
    )
    ok = proc.returncode == 0 and DIST_EXE.exists()
    if not ok:
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
    report(ok, "сборка pyinstaller")


def wait_for_lock(profile_dir, timeout=30):
    lock_path = profile_dir / ".shifrator" / "launcher.lock"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if lock_path.exists():
            try:
                return json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        time.sleep(0.2)
    return None


def http(method, url, payload=None, headers=None, timeout=15):
    data = None
    if payload is not None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    if not args.skip_build:
        build()
    else:
        report(DIST_EXE.exists(), "dist/SHIFRATOR.exe присутствует (--skip-build)")

    profile_dir = Path(tempfile.mkdtemp(prefix="shifrator_accept_"))
    env = dict(os.environ)
    env["USERPROFILE"] = str(profile_dir)
    env["HOME"] = str(profile_dir)  # Path.home() на Windows смотрит USERPROFILE; HOME для симметрии

    proc = None
    try:
        proc = subprocess.Popen([str(DIST_EXE)], cwd=str(profile_dir), env=env)

        lock = wait_for_lock(profile_dir)
        report(lock is not None, "лаунчер поднялся (launcher.lock)")
        port = lock["port"]

        pinged = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                pinged = http("GET", f"http://127.0.0.1:{port}/api/ping")
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.3)
        report(pinged is not None, "сервер отвечает на /api/ping", str(pinged))

        original = SAMPLE_DOC.read_text(encoding="utf-8")
        body = original.encode("utf-8")
        try:
            enc = http("POST", f"http://127.0.0.1:{port}/api/encrypt", payload=body,
                        headers={"X-Filename": "agency_0003.txt", "X-Allow-Lossy": "0",
                                 "Content-Type": "text/plain"}, timeout=120)
        except urllib.error.HTTPError as e:
            enc = {"status": "error", "message": f"HTTP {e.code}: {e.read()[:500]}"}
        ok = enc.get("status") == "ok" and enc.get("session_id") and enc.get("anon_text")
        report(ok, "шифрование документа", enc.get("message", ""))
        session_id = enc["session_id"]
        anon_text = enc["anon_text"]

        n_masks = len(re.findall(r"\[[A-Z_]+_\d+\]", anon_text))
        report(n_masks == EXPECTED_MASKS_TXT, "масок ровно столько, сколько ждём (.txt)",
               f"{n_masks} шт., ожидалось {EXPECTED_MASKS_TXT}")

        leaked = [v for v in EXPECTED_VALUES_TXT if v in anon_text]
        report(not leaked, "ожидаемые значения исчезли из анонимного текста (.txt)",
               f"осталось открытым: {leaked}" if leaked else f"{len(EXPECTED_VALUES_TXT)} значений")

        dec = http("POST", f"http://127.0.0.1:{port}/api/decrypt",
                    payload={"session_id": session_id, "text": anon_text})
        ok = dec.get("status") == "ok" and not dec.get("unresolved")
        report(ok, "восстановление из ответа", f"unresolved={dec.get('unresolved')}")
        restored = dec.get("restored", "")
        leftover = len(re.findall(r"\[[A-Z_]+_\d+\]", restored))
        report(leftover == 0, "токенов в восстановленном тексте нет", f"{leftover} шт.")

        lost = [v for v in BYTE_EXACT_TXT if v not in restored]
        report(not lost, "значения вернулись БАЙТ-В-БАЙТ (.txt)",
               f"не вернулось: {lost}" if lost else f"{len(BYTE_EXACT_TXT)} значений")
        bad_canon = [v for v in CANON_MUST_NOT_APPEAR_TXT if v in restored]
        missing_canon = [v for v in CANON_MUST_APPEAR_TXT if v not in restored]
        report(not bad_canon and not missing_canon,
               "канон вернул ТОГО ЖЕ человека, а не однофамильца другого пола",
               (f"в тексте чужое имя: {bad_canon}" if bad_canon else
                f"не вернулось: {missing_canon}") if (bad_canon or missing_canon)
               else f"{len(CANON_MUST_APPEAR_TXT)} ФИО")
        note(restored == original, "восстановленный текст побайтно равен исходному (.txt)",
             "" if restored == original else
             "расходится — так и задумано: восстановление подставляет КАНОН "
             "(именительный падеж) вместо исходной падежной формы; "
             f"длины {len(restored)} vs {len(original)}. Посимвольную дельту "
             "здесь не печатаем намеренно: первое же изменение длины сдвигает "
             "весь хвост, и число вышло бы враньём о масштабе")

        sessions = http("GET", f"http://127.0.0.1:{port}/api/sessions")
        ids = [s.get("session_id") for s in sessions.get("sessions", [])]
        report(session_id in ids, "сессия видна в списке")

        dec2 = http("POST", f"http://127.0.0.1:{port}/api/decrypt",
                     payload={"session_id": session_id, "text": anon_text})
        report(dec2.get("status") == "ok" and dec2.get("restored") == dec.get("restored"),
               "сессия повторно открывается по id")

        # --- ДОКУМЕНТ WORD через СОБРАННУЮ программу ---------------------- #
        # До этого шага .docx не проходил через exe вовсе: python-docx, lxml,
        # чтение колонтитулов и сканер непрочитанных зон в сборке не
        # проверялись ничем (долг AUD1-CIRCLE-THIN). Класс дефекта у сборки
        # свой — путь к ресурсу, потерянный hiddenimport, — и рабочее дерево
        # его не видит по построению.
        try:
            encd = http("POST", f"http://127.0.0.1:{port}/api/encrypt",
                        payload=SAMPLE_DOCX.read_bytes(),
                        headers={"X-Filename": "agency_0002.docx",
                                 "X-Allow-Lossy": "1",
                                 "Content-Type": "application/octet-stream"},
                        timeout=180)
        except urllib.error.HTTPError as e:
            encd = {"status": "error", "message": f"HTTP {e.code}: {e.read()[:500]}"}
        ok = encd.get("status") == "ok" and encd.get("session_id") and encd.get("anon_text")
        report(ok, "шифрование документа Word (.docx)", encd.get("message", ""))
        anon_docx = encd["anon_text"]
        sid_docx = encd["session_id"]

        n_docx = len(re.findall(r"\[[A-Z_]+_\d+\]", anon_docx))
        report(n_docx == EXPECTED_MASKS_DOCX, "масок ровно столько, сколько ждём (.docx)",
               f"{n_docx} шт., ожидалось {EXPECTED_MASKS_DOCX}")

        leaked_d = [v for v in EXPECTED_VALUES_DOCX if v in anon_docx]
        report(not leaked_d, "ожидаемые значения исчезли из анонимного текста (.docx)",
               f"осталось открытым: {leaked_d}" if leaked_d else
               f"{len(EXPECTED_VALUES_DOCX)} значений")

        decd = http("POST", f"http://127.0.0.1:{port}/api/decrypt",
                    payload={"session_id": sid_docx, "text": anon_docx})
        ok = decd.get("status") == "ok" and not decd.get("unresolved")
        report(ok, "восстановление документа Word", f"unresolved={decd.get('unresolved')}")
        restored_d = decd.get("restored", "")
        lost_d = [v for v in BYTE_EXACT_DOCX if v not in restored_d]
        report(not lost_d, "значения вернулись БАЙТ-В-БАЙТ (.docx)",
               f"не вернулось: {lost_d}" if lost_d else
               f"{len(BYTE_EXACT_DOCX)} значений")

        # --- PDF с текстовым слоем через СОБРАННУЮ программу (PDF-ARCH) --- #
        # ИНН 7707083893 — 10 цифр, ИНН ЮРЛИЦА: круг ходит через интерфейс на
        # наборе ПО УМОЛЧАНИЮ («Только персональные данные»), где ИНН
        # организации сознательно остаётся открытым (см. предупреждение в
        # шапке файла — «Первая же редакция этого списка на том и споткнулась:
        # ИНН юрлица честно остался в тексте»; PDF-проба наступила на ТУ ЖЕ
        # грабли живым прогоном). Проверяем на исчезновение ТЕЛЕФОН — он
        # персональные данные и маскируется по умолчанию.
        pdf_bytes = _make_synthetic_pdf_bytes(
            "Contract INN 7707083893 phone +7 495 123-45-67"
        )
        try:
            encp = http("POST", f"http://127.0.0.1:{port}/api/encrypt",
                        payload=pdf_bytes,
                        headers={"X-Filename": "synthetic.pdf",
                                 "X-Allow-Lossy": "1",
                                 "Content-Type": "application/octet-stream"},
                        timeout=120)
        except urllib.error.HTTPError as e:
            encp = {"status": "error", "message": f"HTTP {e.code}: {e.read()[:500]}"}
        ok = encp.get("status") == "ok" and encp.get("session_id") and encp.get("anon_text")
        report(ok, "шифрование синтетического PDF (текстовый слой)", encp.get("message", ""))
        anon_pdf = encp["anon_text"]

        n_pdf = len(re.findall(r"\[[A-Z_]+_\d+\]", anon_pdf))
        report(n_pdf > 0, "PDF: хотя бы одна маска в анонимном тексте", f"{n_pdf} шт.")
        report("123-45-67" not in anon_pdf, "PDF: телефон (ПДн) исчез из анонимного текста")

        print("КРУГ ПРОЙДЕН")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
