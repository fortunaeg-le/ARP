"""Блок 11 — Адаптер Excel (Компонент 2: детокенизация файлов).

Открывает произвольный `.xlsx`, находит в нём токены вида `[ORG_1]` и заменяет
их на исходные значения через переданный `resolve`, не трогая ни одного узла
оформления. Вся логика поиска/замены токенов (включая склейку разорванных
между run'ами токенов) находится в `ooxml_core.replace_tokens_in_group` —
здесь описано только "какие части архива открыть, какие узлы считать текстом,
как разбить их на группы" (см. SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 11).

Главное отличие xlsx от docx/pptx: строковое значение ячейки, как правило, не
хранится в самой ячейке. В `xl/worksheets/sheet*.xml` ячейка выглядит как
`<c r="B2" t="s" s="3"><v>17</v></c>`, где `17` — индекс в общей таблице строк
`xl/sharedStrings.xml`; сам текст лежит там. Поэтому основная обрабатываемая
часть — `xl/sharedStrings.xml` (группа = одна запись `<si>`), а дополнительно
обрабатываются inline-строки `<is>` внутри `<c t="inlineStr">` в листах.

Что НЕ трогаем (см. блок 11): формулы `<f>`, значения/индексы `<v>`, атрибут `s`
у `<c>`, `xl/styles.xml`, `t`-атрибут ячейки. Мы меняем только текст узлов `<t>`
внутри `<si>` и `<is>` — за счёт этого стиль ячейки, формат числа и тип ячейки
сохраняются автоматически (токен-строка остаётся строкой; конвертация в число
запрещена — ИНН/счёт/телефон не должны терять ведущие нули).

Модуль не выполняет никаких действий при импорте — импорт мгновенный.

Зависимости: ooxml_core (блок 8), lxml. openpyxl запрещён (пересобирает книгу
по своей модели и теряет часть разметки — диаграммы, сводные таблицы, условное
форматирование).
"""

import fnmatch
import re

from ooxml_core import (
    Resolver,
    RunGroup,
    TextUnit,
    parse_xml,
    read_zip_parts,
    replace_tokens_in_group,
    rewrite_zip,
    serialize_xml,
)

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_S_SI = _S + "si"   # запись общей таблицы строк
_S_IS = _S + "is"   # inline-строка внутри ячейки
_S_T = _S + "t"     # текстовый узел

_SHARED_STRINGS = "xl/sharedStrings.xml"
_SHEET_MASK = "xl/worksheets/sheet*.xml"

_TRAILING_NUM_RE = re.compile(r"(\d+)(?=\.xml$)")


def _is_sheet_part(name: str) -> bool:
    return fnmatch.fnmatchcase(name, _SHEET_MASK)


def _sheet_sort_key(name: str):
    # Листы упорядочиваются по числовому номеру в имени (sheet2.xml перед
    # sheet10.xml — сравнение числом, а не строкой, чтобы не сбить порядок
    # unresolved на книгах от 10 листов).
    m = _TRAILING_NUM_RE.search(name)
    num = int(m.group(1)) if m else -1
    return (num, name)


def _build_groups(root, container_tag: str) -> "list[RunGroup]":
    """Строит по одной RunGroup на каждый контейнер `container_tag` части.

    container_tag — `<si>` для sharedStrings.xml либо `<is>` для листа. Units —
    все `<t>` внутри контейнера в порядке следования (простой `<si><t>…` и rich
    text `<si><r><t>…</t></r>…` покрываются одинаково: токен, разорванный между
    `<r>`, собирается ядром так же, как в docx). Псевдо-разделителей в xlsx нет:
    перенос строки внутри ячейки — это литеральный `\n` в тексте `<t>`, а не
    отдельный узел, поэтому склейку через границу run'ов ничто не разрывает.

    Мы намеренно обходим только `<t>` ВНУТРИ `<si>`/`<is>`: `<v>` (число или
    индекс sharedStrings) и `<f>` (формула) в units не попадают — их мы не трогаем.
    """
    groups = []
    for container in root.iter(container_tag):
        units = []
        for node in container.iter(_S_T):
            units.append(TextUnit(node=node, text=node.text or "", writable=True))
        if units:
            groups.append(RunGroup(units=units))
    return groups


def rewrite(src_path: str, dst_path: str, resolve: Resolver) -> "list[str]":
    """Раскрывает токены в `.xlsx` и пишет результат в `dst_path`.

    Возвращает unresolved-токены в порядке первого появления: сначала по
    `xl/sharedStrings.xml` (в порядке `<si>`), затем по листам
    `xl/worksheets/sheet*.xml` (по возрастанию номера, `<is>` в порядке дерева),
    с возможными повторами — дедупликацию делает блок 12.
    """
    parts = read_zip_parts(src_path)
    modified: "dict[str, bytes]" = {}
    unresolved: "list[str]" = []

    # 1) Общая таблица строк — основное место замен.
    if _SHARED_STRINGS in parts:
        tree = parse_xml(parts[_SHARED_STRINGS])
        for group in _build_groups(tree.getroot(), _S_SI):
            unresolved.extend(replace_tokens_in_group(group, resolve))
        modified[_SHARED_STRINGS] = serialize_xml(tree)

    # 2) Inline-строки в листах (некоторые генераторы пишут <c t="inlineStr">,
    #    минуя sharedStrings).
    sheet_names = sorted((n for n in parts if _is_sheet_part(n)), key=_sheet_sort_key)
    for name in sheet_names:
        tree = parse_xml(parts[name])
        groups = _build_groups(tree.getroot(), _S_IS)
        if not groups:
            # В листе нет inline-строк — не переписываем часть, чтобы сохранить
            # её байт-в-байт (rewrite_zip перенесёт оригинал).
            continue
        for group in groups:
            unresolved.extend(replace_tokens_in_group(group, resolve))
        modified[name] = serialize_xml(tree)

    rewrite_zip(src_path, dst_path, modified)
    return unresolved
