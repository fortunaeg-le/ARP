# PyInstaller spec — этап U1 (упаковка десктоп-приложения для конечного
# пользователя без прав администратора и без Python в системе).
#
# --onedir, НЕ --onefile: --onefile распаковывает всё во временную папку при
# КАЖДОМ запуске (медленнее и чаще ловит антивирус на распаковке exe "из
# ниоткуда"), --onedir один раз разворачивает файлы на диск и потом просто их
# запускает — холодный старт быстрее, антивирус спокойнее. См. HANDOFF_U1.
#
# Собирать из корня репозитория:
#   venv\Scripts\pyinstaller.exe packaging\shifrator.spec --distpath dist --workpath build
#
# ГЛАВНАЯ ЛОВУШКА (см. постановку задачи): веса navec/slovnet под Natasha и
# словарь pymorphy2_dicts_ru — файлы ДАННЫХ пакетов, PyInstaller их не находит
# автоматическим анализом байткода (только импорты кода). Забираем ЯВНО через
# collect_data_files. Без этого сборка стартует и падает на первом документе.

import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))
_SRC = os.path.join(_ROOT, "src")
_APP = os.path.join(_ROOT, "app")

# --------------------------------------------------------------------------- #
# ЭТАП U1b: точные версии зависимостей в сборке (не диапазоны).
#
# Численное окружение и версии моделей влияют на РЕЗУЛЬТАТ детекции: собранная
# у клиента поставка должна нести ровно то, что мы измерили. Пины лежат в
# packaging/requirements-build.lock.txt; здесь они ПРОВЕРЯЮТСЯ, иначе файл был
# бы просто документом, который со временем разъедется с окружением.
#
# Проверяем не весь lock, а пакеты, влияющие на результат или на способность
# exe вообще работать; на расхождении сборка ПАДАЕТ (тихая сборка на другой
# версии numpy/natasha — ровно тот класс, ради которого U1b и затеян).
# --------------------------------------------------------------------------- #

_PINNED_CRITICAL = (
    "natasha", "navec", "slovnet", "razdel", "yargy", "pymorphy2",
    "pymorphy2-dicts-ru", "numpy", "lxml", "cryptography", "python-docx",
    "python-pptx", "openpyxl", "PyYAML", "intervaltree", "DAWG-Python",
    "pyinstaller",
)


def _assert_pinned_environment():
    from importlib.metadata import PackageNotFoundError, version

    lock_path = os.path.join(os.path.dirname(SPEC), "requirements-build.lock.txt")
    pinned = {}
    with open(lock_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            name, ver = line.split("==", 1)
            pinned[name.strip().lower().replace("_", "-")] = ver.strip()

    problems = []
    for name in _PINNED_CRITICAL:
        key = name.lower().replace("_", "-")
        want = pinned.get(key)
        if want is None:
            problems.append(f"{name}: нет пина в requirements-build.lock.txt")
            continue
        try:
            have = version(name)
        except PackageNotFoundError:
            problems.append(f"{name}: не установлен (ожидалось {want})")
            continue
        if have != want:
            problems.append(f"{name}: установлено {have}, зафиксировано {want}")

    if problems:
        raise SystemExit(
            "СБОРКА ОСТАНОВЛЕНА — окружение не совпадает с "
            "packaging/requirements-build.lock.txt:\n  " + "\n  ".join(problems) +
            "\nПочините окружение (pip install -r packaging/requirements-build.lock.txt) "
            "или осознанно перевыпустите lock — но не собирайте поставку клиенту на "
            "версиях, на которых ничего не измеряли."
        )
    print(f"[spec] окружение совпадает с lock ({len(_PINNED_CRITICAL)} пакетов), "
          f"python {sys.version.split()[0]}")


_assert_pinned_environment()

datas = []
datas += collect_data_files("natasha")            # navec/slovnet .tar веса
datas += collect_data_files("pymorphy2_dicts_ru")  # словарь морфологии
# ЛОВУШКА №2: pymorphy2 находит словарь ru НЕ по файлам, а через entry_points
# пакета pymorphy2_dicts_ru (pkg_resources.iter_entry_points('pymorphy2_dicts')),
# т.е. по dist-info метаданным, которых collect_data_files не берёт — без этого
# сборка падает на "Can't find a dictionary for language 'ru'".
datas += copy_metadata("pymorphy2_dicts_ru")
datas += [
    (os.path.join(_ROOT, "entity_types.yaml"), "."),
    (os.path.join(_APP, "index.html"), "app"),
]

# ЭТАП DESIGN-1: шрифты макета едут отдельными файлами (app/fonts/*.woff2) и
# раздаются маршрутом /fonts/ в app/server.py. Продукт офлайновый — подтянуть
# их из сети в собранной программе нельзя, забытый файл означает молча
# «поехавший» интерфейс у юриста. Поэтому список берётся не глобом, а РОВНО из
# того, на что ссылается сама разметка: так расходятся с ошибкой и пустая
# папка, и переименованное начертание.
_FONTS_DIR = os.path.join(_APP, "fonts")
with open(os.path.join(_APP, "index.html"), encoding="utf-8") as _f:
    _referenced = sorted(set(re.findall(r"url\(/fonts/([^)]+)\)", _f.read())))
if not _referenced:
    raise SystemExit(
        "СБОРКА ОСТАНОВЛЕНА — app/index.html не ссылается ни на один /fonts/*: "
        "либо разметка потеряла @font-face, либо шрифты снова вшиты в base64."
    )
_absent = [n for n in _referenced if not os.path.exists(os.path.join(_FONTS_DIR, n))]
if _absent:
    raise SystemExit(
        "СБОРКА ОСТАНОВЛЕНА — разметка ссылается на шрифты, которых нет в "
        "app/fonts:\n  " + "\n  ".join(_absent)
    )
datas += [(os.path.join(_FONTS_DIR, n), "app/fonts") for n in _referenced]
print(f"[spec] шрифты интерфейса на месте ({len(_referenced)} шт.)")

# ЭТАП U1b: файла данных, который «есть в спеке», но исчез с диска, PyInstaller
# не заметит — просто не положит его в поставку. Отказ снова был бы тихим.
_missing_datas = sorted({src for src, _dest in datas if not os.path.exists(src)})
if _missing_datas:
    raise SystemExit(
        "СБОРКА ОСТАНОВЛЕНА — файлы данных из спеки отсутствуют на диске:\n  "
        + "\n  ".join(_missing_datas)
    )
print(f"[spec] файлы данных на месте ({len(datas)} шт.)")

hiddenimports = [
    # Плоские импорты src/ (см. STATE.md §6) — PyInstaller обычно находит
    # их статическим анализом байткода, но перечисляем явно на случай
    # ленивых импортов внутри функций, до которых анализатор не дошёл.
    "extractor", "pipeline", "tokenizer", "storage", "session_store",
    "models", "normalizer", "regex_detector", "ner_detector", "config_cache",
    "anchor_registry", "syntax_compound", "multispan", "detokenizer",
    "file_detokenizer", "unread_zones", "ooxml_core", "docx_rewriter",
    "pptx_rewriter", "xlsx_rewriter", "type_policy",
    # PDF-ARCH: `pdf_completeness` импортируется ЛЕНИВО из shifrator.cmd_encrypt
    # (тот же паттерн, что и остальной список) — без него в собранном exe
    # PDF-путь тихо терял бы предупреждение о непрочитанных страницах.
    "pdf_completeness",
    # ЭТАП LAYOUT: ремонтный проход импортируется лениво из pipeline/tokenizer
    "layout_repair",
    # ЭТАП STORE: `vault` (обёртка ключа DPAPI и шифрование сайдкаров) и
    # `markup_record` (структурная запись разметки) импортируются ЛЕНИВО —
    # внутри функций storage.py. Без них в собранном exe не открылась бы ни
    # одна сессия: ключ хранится завёрнутым, разворачивает его именно vault.
    "vault", "markup_record",
    # ЭТАП MARKUP-SPREAD: `markup_spread` импортируется ЛЕНИВО из
    # `core._spread_occurrences`, и там же исключение ГАСИТСЯ (сбой надстройки
    # не имеет права отменить правку человека). Без строки в этом списке
    # собранный exe потерял бы распространение ручной разметки МОЛЧА: маска
    # встала бы на одно выделенное мышкой вхождение, остальные ушли бы в LLM
    # открытыми, и никакой ошибки пользователь бы не увидел — ровно тот класс
    # отказа, ради которого список существует.
    "markup_spread",
    # ЭТАП U4: `report` импортируется ЛЕНИВО (внутри функций core/server) —
    # ровно тот случай, ради которого этот список и существует. Без него в
    # собранном exe экран «Аналитика» отвалился бы, а `core._markup_census`
    # молча (он ловит Exception) перестал бы писать слепок знаменателя — то
    # есть метрика деградировала бы БЕЗ ошибки, что хуже падения.
    "core", "report", "selfcheck", "procutil", "server", "paths",
    # CIRCLE-UI: единый источник форматов/полей предупреждений (см. app/capabilities.py).
    "capabilities",
    # ЭТАП DESKTOP-FIT: `appwindow` — открытие интерфейса отдельным окном
    # приложения (`--app=` у Chromium). Импортируется из launcher.py.
    "appwindow",
    "pymorphy2_dicts_ru",
]


def _assert_every_local_module_listed():
    """ЭТАП U1b: спека обязана знать про КАЖДЫЙ модуль src/ и app/.

    U4 показал класс отказа: новый модуль, импортируемый ЛЕНИВО внутри функции,
    не попадает в сборку, и exe не падает, а ТИХО теряет функциональность.
    Зеркало в tests/test_u1_packaging.py проверяет то же самое, но его можно не
    запустить перед сборкой; здесь проверка стоит на самом пути сборки, и
    собрать заведомо неполный exe нельзя в принципе.
    """
    listed = set(hiddenimports)
    missing = []
    for folder, skip in ((_SRC, set()), (_APP, {"launcher"})):  # launcher — точка входа
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            mod = name[:-3]
            if mod in skip or mod in listed:
                continue
            missing.append(f"{os.path.basename(folder)}/{name}")
    if missing:
        raise SystemExit(
            "СБОРКА ОСТАНОВЛЕНА — модули не перечислены в hiddenimports:\n  "
            + "\n  ".join(missing) +
            "\nВ собранном exe их не окажется, а ленивый импорт внутри функции "
            "даст ТИХУЮ деградацию вместо явной ошибки (находка этапа U4)."
        )
    print(f"[spec] hiddenimports покрывает все модули src/ и app/ ({len(listed)} имён)")


_assert_every_local_module_listed()

a = Analysis(
    [os.path.join(_APP, "launcher.py")],
    pathex=[_SRC, _APP],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SHIFRATOR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,       # без консоли — пользователь запускает двойным кликом,
                          # статус/ошибки показывает окно tkinter (launcher.py)
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="SHIFRATOR",
)
