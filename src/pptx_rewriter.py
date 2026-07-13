"""Блок 10 — Адаптер PowerPoint (Компонент 2: детокенизация файлов).

Открывает произвольный `.pptx`, находит в нём токены вида `[ORG_1]` и заменяет
их на исходные значения через переданный `resolve`, не трогая ни одного узла
оформления. Вся логика поиска/замены токенов (включая склейку разорванных
между run'ами токенов) находится в `ooxml_core.replace_tokens_in_group` —
здесь описано только "какие части архива открыть, какие узлы считать текстом,
как разбить их на группы" (см. SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 10).

Модуль не выполняет никаких действий при импорте — импорт мгновенный.

Зависимости: ooxml_core (блок 8), lxml. python-pptx запрещён (пересобирает
презентацию по своей модели и теряет неизвестную ему разметку).
"""

import fnmatch
import re

from ooxml_core import (
    OoxmlError,
    Resolver,
    RunGroup,
    TextUnit,
    parse_xml,
    read_zip_parts,
    replace_tokens_in_group,
    rewrite_zip,
    serialize_xml,
)

# Опорная часть PowerPoint: без хотя бы одного слайда презентацию нечего
# обрабатывать (см. блок 10). Заметки к слайдам опциональны.
_SLIDE_MASK = "ppt/slides/slide*.xml"

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_A_P = _A + "p"
_A_T = _A + "t"
_A_BR = _A + "br"

# Части архива, которые могут содержать пользовательский текст (см. блок 10).
# Мастера и макеты (slideMasters, slideLayouts) намеренно не обрабатываются —
# там лежат шаблонные плейсхолдеры, а не пользовательский текст.
_PART_MASKS = (
    "ppt/slides/slide*.xml",
    "ppt/notesSlides/notesSlide*.xml",
)

_TRAILING_NUM_RE = re.compile(r"(\d+)(?=\.xml$)")


def _is_target_part(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, mask) for mask in _PART_MASKS)


def _part_sort_key(name: str):
    # slide* (по числовому номеру) -> notesSlide* (по числовому номеру), как
    # перечислено в спецификации; номер сравнивается числом, а не строкой,
    # чтобы slide10.xml не встал раньше slide2.xml.
    if fnmatch.fnmatchcase(name, "ppt/slides/slide*.xml"):
        group = 0
    else:
        group = 1
    m = _TRAILING_NUM_RE.search(name)
    num = int(m.group(1)) if m else -1
    return (group, num, name)


def _build_groups(root) -> "list[RunGroup]":
    """Строит по одной RunGroup на каждый <a:p> части.

    Units — все <a:t> внутри абзаца в порядке следования. Обход идёт по всем
    <a:p> части в порядке документа, что естественным образом покрывает
    <a:txBody> любой фигуры, включая фигуры внутри таблиц <a:tbl> и внутри
    групп фигур — это всё то же дерево. <a:br> между текстовыми узлами
    превращается в псевдо-разделитель (writable=False).
    """
    groups = []
    for p in root.iter(_A_P):
        units = []
        for node in p.iter(_A_T, _A_BR):
            if node.tag == _A_T:
                units.append(TextUnit(node=node, text=node.text or "", writable=True))
            else:
                units.append(TextUnit(node=None, text="\n", writable=False))
        if units:
            groups.append(RunGroup(units=units))
    return groups


def rewrite(src_path: str, dst_path: str, resolve: Resolver) -> "tuple[int, list[str]]":
    """Раскрывает токены в `.pptx` и пишет результат в `dst_path`.

    Возвращает `(число выполненных замен, unresolved)`, где unresolved — токены в
    порядке первого появления в документе (слайды по возрастанию номера, затем
    заметки к слайдам по возрастанию номера), с возможными повторами —
    дедупликацию делает блок 12.

    Если в архиве нет ни одного слайда `ppt/slides/slide*.xml` — это не корректный
    `.pptx`, кидаем OoxmlError и файл назначения не создаём (см. блок 10).
    """
    parts = read_zip_parts(src_path)
    if not any(fnmatch.fnmatchcase(n, _SLIDE_MASK) for n in parts):
        raise OoxmlError(
            f"Файл не является корректным .pptx: не найдена ни одна обязательная "
            f"часть {_SLIDE_MASK}: {src_path}"
        )

    modified: "dict[str, bytes]" = {}
    unresolved: "list[str]" = []
    replaced = 0

    names = sorted((n for n in parts if _is_target_part(n)), key=_part_sort_key)
    for name in names:
        tree = parse_xml(parts[name])
        for group in _build_groups(tree.getroot()):
            n, unres = replace_tokens_in_group(group, resolve)
            replaced += n
            unresolved.extend(unres)
        modified[name] = serialize_xml(tree)

    rewrite_zip(src_path, dst_path, modified)
    return replaced, unresolved
