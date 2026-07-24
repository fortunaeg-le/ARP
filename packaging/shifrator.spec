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

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))
_SRC = os.path.join(_ROOT, "src")
_APP = os.path.join(_ROOT, "app")

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

a = Analysis(
    [os.path.join(_APP, "launcher.py")],
    pathex=[_SRC, _APP],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Плоские импорты src/ (см. STATE.md §6) — PyInstaller обычно находит
        # их статическим анализом байткода, но перечисляем явно на случай
        # ленивых импортов внутри функций, до которых анализатор не дошёл.
        "extractor", "pipeline", "tokenizer", "storage", "session_store",
        "models", "normalizer", "regex_detector", "ner_detector",
        "anchor_registry", "syntax_compound", "multispan", "detokenizer",
        "file_detokenizer", "unread_zones", "ooxml_core", "docx_rewriter",
        "pptx_rewriter", "xlsx_rewriter",
        "core", "selfcheck", "procutil", "server", "paths",
        "pymorphy2_dicts_ru",
    ],
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
