"""Блок 12 — Фасад детокенизации файлов (`file_detokenizer.py`).

Компонент 2, финальный блок. Принимает принесённый пользователем файл
`.docx`/`.xlsx`/`.pptx` со стоящими в нём токенами `[ORG_1]` и session_id,
раскрывает токены в исходные значения из сессии (блок 5, `session_store`),
сохраняя форматирование принесённого файла нетронутым. Своей логики разбора
форматов не содержит: выбирает адаптер по расширению и вызывает его полиморфно
(единая сигнатура блоков 9–11 — `rewrite(src, dst, resolve) -> tuple[int, list[str]]`).

Публичная функция:
  - detokenize_file(src_path, session_id, dst_path=None, storage_dir=None)
        -> (путь_к_созданному_файлу, число_замен, unresolved)

Никакого NER/детекторов/tokenizer здесь нет и не импортируется — детокенизация
файла в них не нуждается, а их импорт тяжёл (см. «Бюджет ресурсов» спеки).
Импорт этого модуля обязан быть мгновенным.
"""

import os

from session_store import load_session

# Соответствие расширения адаптеру. Импорт адаптеров ленивый (внутри функции),
# чтобы импорт самого фасада оставался мгновенным и грузился только нужный формат.
_SUPPORTED = (".docx", ".xlsx", ".pptx")


def _load_adapter(ext: str):
    """Возвращает функцию rewrite соответствующего адаптера по расширению.

    Расширение уже проверено вызывающим (нижний регистр, из _SUPPORTED).
    """
    if ext == ".docx":
        from docx_rewriter import rewrite
    elif ext == ".xlsx":
        from xlsx_rewriter import rewrite
    elif ext == ".pptx":
        from pptx_rewriter import rewrite
    else:  # недостижимо: проверка формата сделана в detokenize_file
        raise AssertionError(f"неизвестное расширение прошло проверку: {ext!r}")
    return rewrite


def _default_dst_path(src_path: str) -> str:
    """Формирует dst рядом с src, добавляя суффикс _restored перед расширением.

    `отчёт.docx` -> `отчёт_restored.docx`.
    """
    root, ext = os.path.splitext(src_path)
    return f"{root}_restored{ext}"


def detokenize_file(
    src_path: str,
    session_id: str,
    dst_path: str | None = None,
    storage_dir: str | None = None,
) -> tuple[str, int, list[str]]:
    """Раскрывает токены в файле `src_path`, сохраняя новый файл в `dst_path`.

    Формат определяется по расширению `src_path` (нижний регистр). Неподдерживаемое
    расширение (в т.ч. `.pdf`) — ValueError. resolve строится из сессии `session_id`
    (маппинг token -> original_text); SessionNotFoundError/SessionExpiredError не
    перехватываются, а пробрасываются наверх (контракт блока 6).

    Возвращает (путь_к_созданному_файлу, число_замен, unresolved) — число_замен —
    сколько токенов фактически раскрыто, unresolved уникальны, в порядке первого
    появления. Исходный файл не модифицируется.

    Исключения:
      - ValueError — неподдерживаемый формат либо dst_path совпадает с src_path;
      - FileNotFoundError — src_path не существует;
      - FileExistsError — dst_path уже существует (молча затирать чужой файл нельзя);
      - OoxmlError — файл не является корректным OOXML-контейнером (из ядра, блок 8);
      - SessionNotFoundError/SessionExpiredError — из session_store, пробрасываются.
    """
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in _SUPPORTED:
        raise ValueError(
            f"Неподдерживаемый формат: {ext}. Поддерживаются: .docx, .xlsx, .pptx"
        )

    if dst_path is None:
        dst_path = _default_dst_path(src_path)

    # Перезаписывать вход запрещено. Сравнение по realpath ловит и относительные
    # пути, и симлинки, указывающие на один и тот же файл.
    if os.path.realpath(src_path) == os.path.realpath(dst_path):
        raise ValueError(
            f"dst_path совпадает с src_path — перезапись входного файла запрещена: {src_path}"
        )

    if os.path.exists(dst_path):
        raise FileExistsError(
            f"Файл назначения уже существует, перезапись запрещена: {dst_path}"
        )

    # Построение resolve из сессии. load_session сам валидирует session_id и кидает
    # SessionNotFoundError/SessionExpiredError — их не перехватываем (пробрасываем).
    session = load_session(session_id, storage_dir=storage_dir)
    mapping = {e["token"]: e["original_text"] for e in session["entities"]}

    def resolve(token: str) -> str | None:
        return mapping.get(token)

    rewrite = _load_adapter(ext)
    replaced, raw_unresolved = rewrite(src_path, dst_path, resolve)

    # Дедупликация unresolved: уникальные, в порядке первого появления (контракт блока 6).
    unresolved: list[str] = []
    seen: set[str] = set()
    for token in raw_unresolved:
        if token not in seen:
            seen.add(token)
            unresolved.append(token)

    return dst_path, replaced, unresolved
