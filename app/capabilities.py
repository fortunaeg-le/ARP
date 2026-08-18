"""ЕДИНЫЙ ИСТОЧНИК того, что интерфейс обязан пускать (этап CIRCLE-UI, задача 3).

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ. Дважды за проект возможность была «сделана,
проверена и сдана», но человеку недоступна, и оба раза причина одна — список
жил в ДВУХ копиях, движок правили, копию в интерфейсе нет:

  * PDF: движок читает его с этапа PDF-ARCH, а `app/server.py::_ALLOWED_EXT` и
    `accept=` в `index.html` про него не знали — интерфейс не пускал файл вовсе;
  * предупреждение о непрочитанных страницах PDF: `renderVerify` читал только
    поле `zones` (.docx) и не знал про `pdf_issues`, поэтому договор со
    страницей-сканом молча терял страницу, а человек считал его обезличенным;
  * раньше того же класса: 14 типов сущностей в интерфейсе против 26 в
    конфигурации.

Правило теперь такое: список форматов и список полей «прочитано не всё»
объявляются ЗДЕСЬ и НИГДЕ БОЛЬШЕ. Сервер отдаёт их наружу маршрутом
`/api/capabilities`, `index.html` строит из ответа и `accept=`, и проверку
расширения, и подписи под кнопкой. Расхождение «движок умеет, интерфейс не
пускает» ловит `tests/test_ui_capability_parity.py` — он спрашивает у САМОГО
ДВИЖКА (пробой, а не чтением текста), что тот умеет, и сверяет со списком ниже.

Что здесь ВЫВОДИТСЯ, а не переписывается руками:
  * форматы восстановления в файл берутся прямо из `file_detokenizer._SUPPORTED`
    — второй копии нет физически;
  * форматы загрузки перечислены (в `extractor.extract` они лежат ветками
    if/elif, объявленного списка там нет), и ровно поэтому их стережёт проба.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import app_root  # noqa: E402

_SRC = os.path.join(app_root(), "src")
if _SRC not in sys.path:
    sys.path.insert(1, _SRC)


# --- 1. Что можно ЗАГРУЗИТЬ на обезличивание ---------------------------------
# Соответствует веткам `extractor.extract`. Сторож — проба движком, см. шапку.
INPUT_FORMATS = [
    {"ext": ".docx", "label": "Word (.docx)"},
    {"ext": ".pdf", "label": "PDF (.pdf)"},
    {"ext": ".txt", "label": "текст (.txt)"},
]

# --- 2. Что можно получить ОБРАТНО ФАЙЛОМ с сохранённым оформлением ----------
# ВЫВОДИТСЯ из движка: копии нет, разойтись физически не с чем.
_RESTORE_LABELS = {
    ".docx": "Word (.docx)",
    ".xlsx": "Excel (.xlsx)",
    ".pptx": "PowerPoint (.pptx)",
}


def _restore_formats() -> list[dict]:
    from file_detokenizer import _SUPPORTED

    return [{"ext": e, "label": _RESTORE_LABELS.get(e, e)} for e in _SUPPORTED]


# --- 3. Форматы, которые движок ЧИТАЕТ, но обратно файлом НЕ отдаёт ----------
# Это не дыра, а решение владельца: у PDF нет структуры, куда вставить исходное
# значение на место токена с сохранением оформления. Дорога есть, и человеку её
# надо НАЗВАТЬ, а не показать пустую кнопку — текст отсюда идёт на экран.
RESTORE_FILE_UNSUPPORTED = {
    ".pdf": ("Договор в PDF вернуть файлом нельзя: в PDF нет структуры, куда "
             "поставить исходное значение вместо метки, не сломав вёрстку. "
             "Восстановление для PDF возвращается ТЕКСТОМ — вставьте ответ "
             "нейросети в поле выше и нажмите «Восстановить оригинал»."),
}

# --- 4. Поля ответа, которыми сервер говорит «прочитано НЕ ВСЁ» --------------
# ГЛАВНЫЙ риск системы — тихая утечка: часть документа не прочитана, значит не
# обезличена, а человек об этом не знает. Каждое такое поле обязано быть
# ПОКАЗАНО на экране. Список стережётся тестом: и на полноту (движок не завёл
# третье поле молча), и на то, что `index.html` читает КАЖДОЕ из них.
INCOMPLETENESS_FIELDS = [
    {
        "field": "zones",
        "applies_to": ".docx",
        # `core.run_encrypt` кладёт зоны в ответ только при allow_lossy —
        # без него документ ОТКЛОНЯЕТСЯ (status="refused"), это отдельный экран.
        "source": "app.core.scan_zones",
    },
    {
        "field": "pdf_issues",
        "applies_to": ".pdf",
        # У PDF отказа не бывает — ВСЕГДА lossy, поэтому единственная защита
        # человека от молча потерянной страницы-скана — плашка на экране.
        "source": "app.core.scan_pdf_issues",
    },
]


# --- 5. Расширение файла, который программа ОТДАЁТ САМА ----------------------
# Обезличенный текст сохраняется как `{session_id}.txt` (index.html::downloadAnon),
# и поле «загрузить его обратно» обязано принимать ровно это. Литерала в
# index.html быть не должно даже здесь: одна дырка в правиле — и через год в ней
# опять окажется отставший список.
ANON_TEXT_EXT = ".txt"


def input_extensions() -> tuple[str, ...]:
    return tuple(f["ext"] for f in INPUT_FORMATS)


def restore_file_extensions() -> tuple[str, ...]:
    return tuple(f["ext"] for f in _restore_formats())


def incompleteness_fields() -> tuple[str, ...]:
    return tuple(f["field"] for f in INCOMPLETENESS_FIELDS)


def unsupported_restore_message(ext: str) -> str | None:
    return RESTORE_FILE_UNSUPPORTED.get(ext.lower())


def input_formats_sentence() -> str:
    """Подпись под кнопкой загрузки — тоже отсюда, чтобы не разошлась с accept=."""
    return " · ".join(f["label"] for f in INPUT_FORMATS)


def view() -> dict:
    """То, что уезжает в браузер маршрутом /api/capabilities."""
    return {
        "input_formats": INPUT_FORMATS,
        "input_accept": ",".join(input_extensions()),
        "input_sentence": input_formats_sentence(),
        "restore_file_formats": _restore_formats(),
        "restore_file_accept": ",".join(restore_file_extensions()),
        "restore_file_sentence": " · ".join(f["label"] for f in _restore_formats()),
        "restore_file_unsupported": RESTORE_FILE_UNSUPPORTED,
        "incompleteness_fields": INCOMPLETENESS_FIELDS,
        "anon_text_accept": ANON_TEXT_EXT,
        "max_upload_mb": 50,
    }
