"""СТОРОЖ РАСХОЖДЕНИЯ «движок умеет — интерфейс не пускает» (этап CIRCLE-UI, задача 3).

ЗАЧЕМ. Дважды за проект возможность была сделана, проверена и сдана, но
человеку недоступна, и оба раза механизм один: список жил в ДВУХ копиях, движок
правили, копию в интерфейсе — нет.

  * PDF читался движком с этапа PDF-ARCH, а интерфейс его не принимал вовсе:
    `_ALLOWED_EXT` в server.py и `accept=` в index.html про него не знали.
  * Предупреждение о непрочитанных страницах PDF не показывалось НИКОГДА:
    экран читал только поле `zones` (.docx) и не знал про `pdf_issues`.
    Договор со страницей-сканом обрабатывался, страница молча не читалась,
    человек считал документ обезличенным. Это ГЛАВНЫЙ риск системы.
  * Раньше того же класса: 14 типов сущностей в интерфейсе против 26 в
    конфигурации.

Ни полный набор тестов, ни гейт, ни круг этот класс не видели: все они ходят
мимо экрана. Здесь стоят проверки, которые падают ИМЕННО на расхождении.

Принцип: у движка СПРАШИВАЮТ (пробой или чтением его собственных структур), а
не переписывают его ответ во второй раз. Единственное объявленное место —
`app/capabilities.py`.
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "app"), os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import capabilities  # noqa: E402

INDEX_HTML = os.path.join(ROOT, "app", "index.html")
SERVER_PY = os.path.join(ROOT, "app", "server.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# --- 1. ЗАГРУЗКА: что движок читает, то интерфейс обязан пускать -------------

# Расширения, которые пробуются у движка. Список НАМЕРЕННО шире поддерживаемого:
# в нём есть и форматы, которые движок читать не должен (.rtf, .odt), и те, что
# он умеет только на восстановлении (.xlsx, .pptx). Проба обязана развести их
# сама — если завтра extractor научится .rtf, а capabilities промолчит, красным
# станет ИМЕННО эта проверка.
_PROBE_EXTS = [".docx", ".txt", ".pdf", ".rtf", ".odt", ".doc",
               ".xlsx", ".pptx", ".md", ".html", ".csv"]


def _engine_reads(ext: str) -> bool:
    """Спрашивает У ДВИЖКА, берётся ли он за такое расширение.

    Не читает исходник и не парсит if/elif — зовёт `extractor.extract` на пустом
    файле нужного расширения. «Неподдерживаемый формат» = не читает; ЛЮБОЙ
    другой исход (в том числе «Пустой файл» или ошибка разбора) = берётся,
    просто содержимое негодное.
    """
    from extractor import extract

    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        try:
            extract(path)
        except ValueError as e:
            return "Неподдерживаемый формат" not in str(e)
        except Exception:  # noqa: BLE001 — любой разбор = формат взят в работу
            return True
        return True
    finally:
        os.remove(path)


def test_input_formats_match_engine():
    """Набор форматов загрузки == набор, за который берётся сам движок.

    Это ровно та проверка, которая покраснела бы на дефекте PDF: движок читал
    .pdf, `capabilities`/`_ALLOWED_EXT` — нет.
    """
    engine = {e for e in _PROBE_EXTS if _engine_reads(e)}
    declared = set(capabilities.input_extensions())
    assert engine == declared, (
        f"движок читает {sorted(engine)}, интерфейс объявляет {sorted(declared)}. "
        f"Не пускается в интерфейс: {sorted(engine - declared)}; "
        f"объявлено лишнее: {sorted(declared - engine)}"
    )


def test_server_allowlist_has_no_second_copy():
    """У сервера нет СВОЕГО списка — он берёт его из capabilities."""
    import server

    assert server._ALLOWED_EXT == capabilities.input_extensions()
    src = _read(SERVER_PY)
    assert "_ALLOWED_EXT = capabilities.input_extensions()" in src, (
        "_ALLOWED_EXT снова объявлен своим литералом — вернулась вторая копия"
    )


# --- 2. ВОЗВРАТ ФАЙЛОМ: то же самое для восстановления с оформлением ---------

def test_restore_file_formats_are_derived_not_copied():
    from file_detokenizer import _SUPPORTED

    assert capabilities.restore_file_extensions() == tuple(_SUPPORTED), (
        "список форматов возврата файлом разошёлся с file_detokenizer._SUPPORTED"
    )


def test_every_restore_format_has_human_label():
    for f in capabilities.view()["restore_file_formats"]:
        assert f["label"] and f["label"] != f["ext"], (
            f"формат {f['ext']} движок возвращает, а подписи для человека нет — "
            "на экране он появится голым расширением"
        )


def test_format_engine_reads_but_cannot_return_is_named_not_silent():
    """Формат, который читается, но файлом не возвращается, обязан быть ОБЪЯСНЁН.

    Это .pdf: восстановление для него уходит в текст (решение владельца). Пустая
    кнопка или молчаливый отказ здесь недопустимы — человек решит, что сломалось.
    """
    gap = set(capabilities.input_extensions()) - set(capabilities.restore_file_extensions())
    for ext in gap:
        if ext == ".txt":
            continue  # .txt и так возвращается текстом — оформления в нём нет
        msg = capabilities.unsupported_restore_message(ext)
        assert msg, (f"формат {ext} читается, но файлом не возвращается, и объяснения "
                     "для человека нет — заведите его в RESTORE_FILE_UNSUPPORTED")
        assert len(msg) > 80, f"объяснение для {ext} слишком короткое, чтобы быть дорогой"


# --- 3. ЭКРАН не держит своих копий ------------------------------------------

def test_index_html_has_no_hardcoded_accept():
    """`accept=` в index.html обязан выставляться из CAPS, а не литералом."""
    html = _read(INDEX_HTML)
    hard = re.findall(r'accept="\.[^"]*"', html)
    assert not hard, (
        f"в index.html снова захардкожен accept: {hard}. "
        "Он обязан ставиться из /api/capabilities (loadCapabilities)."
    )


def test_index_html_does_not_compare_extensions_by_literal():
    """Проверка расширения на клиенте — по списку из CAPS, не по цепочке !==."""
    html = _read(INDEX_HTML)
    bad = re.findall(r"""ext\s*[!=]==?\s*['"]\.\w+['"]""", html)
    assert not bad, (
        f"в index.html вернулось сравнение расширения с литералом: {bad}. "
        "Это вторая копия списка форматов — она и отстала от движка на .pdf."
    )


def test_index_html_reads_capabilities_route():
    html = _read(INDEX_HTML)
    assert "/api/capabilities" in html and "loadCapabilities" in html


# --- 4. ПРЕДУПРЕЖДЕНИЯ «прочитано не всё» ------------------------------------
# Главный риск системы: часть документа не прочитана => не обезличена, а человек
# об этом не знает. Каждое поле, которым движок это сообщает, обязано быть и
# объявлено, и ПОКАЗАНО.

# Ключи ответа core.run_encrypt, ЗАМОРОЖЕННЫЕ намеренно. Новый ключ в ответе
# делает этот тест красным — и автор обязан решить: это очередное поле
# «прочитано не всё» (тогда его в INCOMPLETENESS_FIELDS и на экран) или нет
# (тогда сюда). Ровно этого решения не приняли, когда завели pdf_issues.
_KNOWN_ENCRYPT_KEYS = {
    "session_id", "source_name", "zones", "lossy", "pdf_issues", "pdf_incomplete",
    "storage_dir", "policy",
    # из _render_state
    "original_html", "anon_html", "anon_text", "replacements",
    "entities_total", "values_hidden", "segments",
}


def test_no_new_incompleteness_field_slipped_in_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import core

    probe = tmp_path / "probe.txt"
    probe.write_text(
        "Договор с Кузнецовой Марией Дмитриевной, телефон +7 (495) 409-87-66.",
        encoding="utf-8",
    )
    keys = set(core.run_encrypt(str(probe), allow_lossy=True).keys())

    new = keys - _KNOWN_ENCRYPT_KEYS
    assert not new, (
        f"в ответе шифрации появились НЕОБЪЯВЛЕННЫЕ поля: {sorted(new)}. "
        "Если хоть одно из них сообщает «прочитано не всё» — оно обязано попасть "
        "в capabilities.INCOMPLETENESS_FIELDS и на экран, иначе повторится "
        "история pdf_issues: страница молча не читается, человек не знает."
    )
    missing = set(capabilities.incompleteness_fields()) - keys
    assert not missing, (
        f"объявленных полей нет в ответе движка: {sorted(missing)} — "
        "объявление отстало от кода"
    )


def test_every_incompleteness_field_is_rendered_on_screen():
    """Каждое объявленное поле обязано ЧИТАТЬСЯ экраном.

    Это ровно та проверка, которая покраснела бы на вчерашнем дефекте:
    `pdf_issues` существовал в ответе, но `renderVerify` его не читал, и плашки
    на экране не было НИКОГДА.
    """
    html = _read(INDEX_HTML)
    for field in capabilities.incompleteness_fields():
        assert re.search(r"\bd\.%s\b" % re.escape(field), html), (
            f"поле «{field}» сообщает, что часть документа НЕ ПРОЧИТАНА, но "
            f"index.html его не читает (нет d.{field}) — предупреждение не "
            "дойдёт до человека, а он решит, что документ обезличен целиком"
        )


# --- 5. МАРШРУТ ЕСТЬ — КНОПКИ НЕТ --------------------------------------------
# Самый общий вид того же дефекта: сервер умеет, экран не зовёт. Ровно так
# прожил decrypt-file — он был в командной строке, но не в интерфейсе.

# Маршруты, которых на экране НЕТ ОСОЗНАННО. Каждому — причина; пустая строка
# не принимается. Список короткий намеренно: он и есть цена молчания.
_NOT_ON_SCREEN = {
    "/api/ping": "проба живости: её зовут лаунчер и круг приёмки, человеку она не нужна",
    "/api/encrypt": "синхронная шифрация: её зовёт круг и внешние вызовы; экран ходит "
                    "через /api/encrypt/start (прогресс)",
    "/api/markup/update": "ОТКРЫТЫЙ ДОЛГ CIRCLE-UI-1 (docs/FINDINGS.md): смена типа у уже "
                          "сохранённой записи разметки есть на сервере и не вызывается "
                          "экраном. Не закрыт этим этапом — зона другая",
}


def _server_routes():
    src = _read(SERVER_PY)
    return (set(re.findall(r'self\.path == "(/api/[a-z/-]+)"', src)) |
            set(re.findall(r'self\.path\.startswith\("(/api/[a-z/-]+)"\)', src)))


def test_every_server_route_is_reachable_from_the_screen():
    html = _read(INDEX_HTML)
    orphans = []
    for route in sorted(_server_routes()):
        if route in _NOT_ON_SCREEN:
            assert _NOT_ON_SCREEN[route].strip(), f"причина для {route} пустая"
            continue
        if route not in html:
            orphans.append(route)
    assert not orphans, (
        f"сервер умеет, а экран не зовёт: {orphans}. Это ровно тот класс, из-за "
        "которого decrypt-file (вернуть договор файлом) прожил весь проект только "
        "в командной строке. Либо кнопка на экране, либо явная причина в "
        "_NOT_ON_SCREEN этого теста."
    )


def test_restore_file_route_is_on_the_screen():
    """Точечный страж задачи 2: возврат файлом обязан быть доступен с экрана."""
    html = _read(INDEX_HTML)
    assert "/api/decrypt-file" in html, "маршрут возврата файлом не вызывается с экрана"
    assert "doDecryptFile" in html, "кнопки возврата файлом на экране нет"
    assert '"/api/decrypt-file"' in _read(SERVER_PY), "маршрута нет на сервере"


# --- 6. Собранная программа тоже обязана знать про capabilities --------------

def test_capabilities_module_is_in_build_spec():
    """Ленивый/новый модуль app/, не попавший в hiddenimports, ТИХО пропадает в exe."""
    spec = _read(os.path.join(ROOT, "packaging", "shifrator.spec"))
    assert '"capabilities"' in spec, (
        "app/capabilities.py не перечислен в hiddenimports — в собранной программе "
        "его не окажется, и интерфейс останется без списка форматов"
    )
