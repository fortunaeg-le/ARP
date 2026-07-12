"""Блок 9 — Адаптер Word (Компонент 2: детокенизация файлов).

Открывает произвольный `.docx`, находит в нём токены вида `[ORG_1]` и заменяет
их на исходные значения через переданный `resolve`, не трогая ни одного узла
оформления. Вся логика поиска/замены токенов (включая склейку разорванных
между run'ами токенов) находится в `ooxml_core.replace_tokens_in_group` —
здесь описано только "какие части архива открыть, какие узлы считать текстом,
как разбить их на группы" (см. SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 9).

Модуль не выполняет никаких действий при импорте — импорт мгновенный.

Зависимости: ooxml_core (блок 8), lxml. python-docx запрещён (пересобирает
документ по своей модели и теряет неизвестную ему разметку).
"""

import fnmatch

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

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_P = _W + "p"
_W_T = _W + "t"
_W_BR = _W + "br"
_W_TAB = _W + "tab"

# Части архива, которые могут содержать пользовательский текст (см. блок 9).
_PART_MASKS = (
    "word/document.xml",
    "word/header*.xml",
    "word/footer*.xml",
    "word/footnotes.xml",
    "word/endnotes.xml",
)


def _is_target_part(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, mask) for mask in _PART_MASKS)


def _part_sort_key(name: str):
    # document.xml -> header* -> footer* -> footnotes -> endnotes, как перечислено
    # в спецификации; внутри своей группы части упорядочены по имени.
    if name == "word/document.xml":
        return (0, name)
    if fnmatch.fnmatchcase(name, "word/header*.xml"):
        return (1, name)
    if fnmatch.fnmatchcase(name, "word/footer*.xml"):
        return (2, name)
    if name == "word/footnotes.xml":
        return (3, name)
    if name == "word/endnotes.xml":
        return (4, name)
    return (5, name)


def _build_groups(root) -> "list[RunGroup]":
    """Строит по одной RunGroup на каждый <w:p> части.

    Units — все <w:t> внутри абзаца в порядке следования (включая вложенные в
    <w:hyperlink>/<w:smartTag>/<w:ins> и подобные обёртки — `iter` обходит всё
    поддерево абзаца, а не только прямых детей). <w:br>/<w:tab> между текстовыми
    узлами превращаются в псевдо-разделители (writable=False). Удалённый текст
    рецензирования (<w:delText>) в units не попадает — мы ищем только тег <w:t>,
    а не <w:delText>, поэтому он просто не встречается при обходе.
    """
    groups = []
    for p in root.iter(_W_P):
        units = []
        for node in p.iter(_W_T, _W_BR, _W_TAB):
            if node.tag == _W_T:
                units.append(TextUnit(node=node, text=node.text or "", writable=True))
            else:
                units.append(TextUnit(node=None, text="\n", writable=False))
        if units:
            groups.append(RunGroup(units=units))
    return groups


def rewrite(src_path: str, dst_path: str, resolve: Resolver) -> "list[str]":
    """Раскрывает токены в `.docx` и пишет результат в `dst_path`.

    Возвращает unresolved-токены в порядке первого появления в документе
    (document.xml, затем header*/footer*/footnotes/endnotes), с возможными
    повторами — дедупликацию делает блок 12.
    """
    parts = read_zip_parts(src_path)
    modified: "dict[str, bytes]" = {}
    unresolved: "list[str]" = []

    names = sorted((n for n in parts if _is_target_part(n)), key=_part_sort_key)
    for name in names:
        tree = parse_xml(parts[name])
        for group in _build_groups(tree.getroot()):
            unresolved.extend(replace_tokens_in_group(group, resolve))
        modified[name] = serialize_xml(tree)

    rewrite_zip(src_path, dst_path, modified)
    return unresolved
