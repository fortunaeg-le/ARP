"""Блок 8 — Ядро OOXML (Компонент 2: детокенизация файлов).

Всё, что одинаково для трёх форматов (.docx / .xlsx / .pptx). Модуль ничего не
знает ни про Word, ни про Excel, ни про PowerPoint — он оперирует абстракцией
«группа текстовых кусочков, идущих подряд» (RunGroup) и умеет:
  - читать/пересобирать ZIP-контейнер (read_zip_parts / rewrite_zip);
  - парсить/сериализовать XML-часть (parse_xml / serialize_xml);
  - заменять токены в группе, включая токены, разорванные между узлами
    (replace_tokens_in_group) — это центральный алгоритм компонента.

Инварианты (см. SHIFRATOR_SPEC_FILE_DECRYPT.md, блок 8):
  - меняется только текстовое содержимое текстовых узлов; узлы и атрибуты
    оформления не читаются и не изменяются;
  - неизвестный токен остаётся в тексте как есть и попадает в unresolved;
  - части архива, которые мы не меняли, переносятся в новый архив с тем же
    именем, методом сжатия и датой.

Модуль не выполняет никаких действий при импорте — импорт мгновенный.

TOKEN_RE совпадает с паттерном блока 6 (detokenizer.py) посимвольно; при
изменении формата токенов правятся оба места (импортировать из detokenizer.py
нельзя — это часть чужого блока).

Зависимости: lxml, стандартные zipfile, re, os, io, tempfile.
"""

import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Callable

from lxml import etree

# Тип резолвера, общий для блоков 8-12. Принимает токен целиком, включая скобки
# ("[ORG_1]"), возвращает original_text либо None, если токен неизвестен сессии.
Resolver = Callable[[str], "str | None"]

# Формат токена: [TYPE_N], TYPE — только заглавные латинские буквы и подчёркивания,
# N — целое число. Посимвольно как в блоке 6 (detokenizer.py, _TOKEN_RE).
TOKEN_RE = re.compile(r"\[[A-Z_]+_\d+\]")

# Атрибут xml:space в нотации lxml (Clark notation).
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Порог защиты от zip-бомбы (см. блок 8, «Защита от zip-бомбы»).
_MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024  # 200 МБ распакованного содержимого
_MAX_ENTRIES = 5000                          # число записей в архиве


class OoxmlError(Exception):
    """Битый / недопустимый OOXML-контейнер (не ZIP, повреждён, zip-бомба)."""


@dataclass
class TextUnit:
    node: object    # lxml-элемент текстового узла (<w:t>/<a:t>/<t>), либо None
    text: str       # текущий текст (для псевдо-разделителя — фиксированное значение)
    writable: bool  # True — в узел можно писать; False — псевдо-разделитель


@dataclass
class RunGroup:
    units: "list[TextUnit]"  # строго в порядке следования текста в документе


def replace_tokens_in_group(group: RunGroup, resolve: Resolver) -> "tuple[int, list[str]]":
    """Заменяет токены во всех units группы, включая разорванные между units.

    Алгоритм (см. блок 8 спецификации):
      1. Склеить тексты всех units в одну строку `joined`, запомнив для каждого
         символа, какому unit и какому смещению внутри него он соответствует.
      2. Найти все совпадения TOKEN_RE в `joined`.
      3. resolve(token) is None -> токен не трогаем, добавляем в unresolved.
      4. Иначе всё значение целиком пишется в тот unit, где токен НАЧИНАЛСЯ;
         символы токена, попавшие в последующие units, из них удаляются.
      5. Совпадения применяются от конца к началу (за счёт позиционной модели
         правок оффсеты не сбиваются).
      6. Итоговые тексты пишутся только в units с writable=True.

    Возвращает `(число фактически выполненных замен, unresolved)`, где unresolved —
    список НЕразрешённых токенов в порядке появления (с возможными повторами).
    «Фактически выполненная замена» — токен, для которого resolve вернул не-None
    (такой токен обязательно меняет текст своего unit). Токен, оставленный как
    есть (unresolved), в счётчик не входит. Тексты units при этом мутируются
    (u.text и u.node.text).
    """
    units = group.units
    orig_texts = [(u.text or "") for u in units]

    # Склейка + карта оффсетов: joined-индекс -> (unit_idx, local_idx).
    offmap: "list[tuple[int, int]]" = []
    for ui, t in enumerate(orig_texts):
        for li in range(len(t)):
            offmap.append((ui, li))
    joined = "".join(orig_texts)

    # Позиционная модель правок: какие символы удалить и куда вставить значение.
    removed = [[False] * len(t) for t in orig_texts]
    inserts: "dict[tuple[int, int], str]" = {}  # (unit_idx, local_idx) -> значение

    unresolved: "list[str]" = []
    replaced = 0

    for m in TOKEN_RE.finditer(joined):
        token = m.group(0)
        value = resolve(token)
        if value is None:
            unresolved.append(token)
            continue
        replaced += 1

        # Значение вставляется как текст lxml-узла — экранирование делает lxml.
        # \n / \r в OOXML внутри текстового узла ведут себя непредсказуемо ->
        # нормализуем в пробел (согласовано с build_plain_text блока 4).
        value = value.replace("\r", " ").replace("\n", " ")

        start, end = m.start(), m.end()
        su, si = offmap[start]  # unit и смещение, где токен начинался

        # Все символы токена помечаем к удалению из их units.
        for pos in range(start, end):
            ui, li = offmap[pos]
            removed[ui][li] = True

        # Всё значение целиком — перед первым символом токена в стартовом unit.
        inserts[(su, si)] = value

    # Пересборка текста каждого writable-unit из «оставшихся» символов + вставок.
    for ui, u in enumerate(units):
        if not u.writable or u.node is None:
            continue
        orig = orig_texts[ui]
        rem = removed[ui]
        out: "list[str]" = []
        for li in range(len(orig)):
            ins = inserts.get((ui, li))
            if ins is not None:
                out.append(ins)
            if not rem[li]:
                out.append(orig[li])
        new_text = "".join(out)
        if new_text != orig:
            u.node.text = new_text
            _preserve_space_if_needed(u.node, new_text)
            u.text = new_text

    return replaced, unresolved


def _preserve_space_if_needed(node, text: str) -> None:
    """Выставить xml:space="preserve", если текст начинается/заканчивается пробелом.

    Без этого Word/Excel обрежут ведущие/хвостовые пробелы. Уже стоящий атрибут
    не снимаем ни при каких условиях.
    """
    if text and (text[0].isspace() or text[-1].isspace()):
        if node.get(_XML_SPACE) is None:
            node.set(_XML_SPACE, "preserve")


def read_zip_parts(src_path: str) -> "dict[str, bytes]":
    """Открывает OOXML-архив, возвращает {имя_части: байты}.

    Битый / не-ZIP файл -> OoxmlError. FileNotFoundError пробрасывается наверх
    (его обрабатывает блок 12). Архив никогда не распаковывается на диск, только
    в память, поэтому zip-slip (имена вида ../../) неприменим.

    Защита от zip-бомбы: до чтения содержимого суммируем распакованные размеры по
    оглавлению; при превышении порогов кидаем OoxmlError, ничего не распаковывая.

    Опорная проверка контейнера: в корне архива обязан присутствовать
    `[Content_Types].xml` — без него это не OOXML-контейнер вообще (см. блок 8).
    Проверка выполняется здесь, до любой другой работы над частями.
    """
    try:
        with zipfile.ZipFile(src_path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ENTRIES:
                raise OoxmlError(
                    f"OOXML-контейнер отклонён: {len(infos)} записей в архиве "
                    f"превышают допустимый предел {_MAX_ENTRIES} (защита от zip-бомбы): {src_path}"
                )
            total = sum(info.file_size for info in infos)
            if total > _MAX_TOTAL_UNCOMPRESSED:
                raise OoxmlError(
                    f"OOXML-контейнер отклонён: суммарный распакованный размер "
                    f"{total} байт превышает предел {_MAX_TOTAL_UNCOMPRESSED} байт "
                    f"(защита от zip-бомбы): {src_path}"
                )
            if not any(info.filename == "[Content_Types].xml" for info in infos):
                raise OoxmlError(
                    f"Файл не является корректным OOXML-контейнером (нет [Content_Types].xml): {src_path}"
                )
            return {info.filename: zf.read(info) for info in infos}
    except zipfile.BadZipFile:
        raise OoxmlError(
            f"Файл не является корректным OOXML-контейнером (повреждён или неверный формат): {src_path}"
        )


def rewrite_zip(src_path: str, dst_path: str, modified: "dict[str, bytes]") -> None:
    """Создаёт новый архив dst_path на основе src_path, подменяя части из `modified`.

    Перебирает записи исходного архива В ИСХОДНОМ ПОРЯДКЕ: если имя записи есть в
    `modified` — пишет новые байты, иначе копирует старые. Для каждой записи
    сохраняются исходные compress_type и date_time. Новые записи не добавляются,
    существующие не удаляются. Запись идёт во временный файл в директории
    назначения и публикуется через os.replace — прерывание не оставит
    недописанный файл под финальным именем.
    """
    dst_dir = os.path.dirname(os.path.abspath(dst_path))
    fd, tmp_path = tempfile.mkstemp(dir=dst_dir, suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(src_path) as zin, \
                zipfile.ZipFile(tmp_path, "w") as zout:
            for item in zin.infolist():
                data = modified.get(item.filename)
                if data is None:
                    data = zin.read(item)
                # Свежий ZipInfo с сохранением метаданных исходной записи, но без
                # переноса flag_bits (например бита data-descriptor), который мог
                # бы рассинхронизироваться с тем, как writestr пишет запись.
                zi = zipfile.ZipInfo(filename=item.filename, date_time=item.date_time)
                zi.compress_type = item.compress_type
                zi.external_attr = item.external_attr
                zi.internal_attr = item.internal_attr
                zi.create_system = item.create_system
                zi.create_version = item.create_version
                zi.extract_version = item.extract_version
                zout.writestr(zi, data)
        os.replace(tmp_path, dst_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def parse_xml(data: bytes):
    """Парсит XML-часть в lxml ElementTree, не меняя документ сверх наших правок.

    Пробельные узлы, комментарии и PI сохраняются (remove_blank_text=False и т.д.).
    Внешние сущности не резолвятся (без сети/файловых обращений). Возвращается
    ElementTree (а не корневой элемент), чтобы сохранить комментарии/PI вокруг корня.
    """
    parser = etree.XMLParser(
        remove_blank_text=False,
        remove_comments=False,
        remove_pis=False,
        resolve_entities=False,
        strip_cdata=False,
        recover=False,
    )
    return etree.parse(io.BytesIO(data), parser)


def serialize_xml(tree) -> bytes:
    """Сериализует ElementTree в байты с обязательной XML-декларацией.

    Office требует ровно `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    и без standalone некоторые версии Excel считают файл повреждённым. Декларацию
    формируем вручную (lxml печатает её в одинарных кавычках), тело — через lxml,
    который сам сохраняет префиксы пространств имён.
    """
    body = etree.tostring(tree, xml_declaration=False, encoding="UTF-8")
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + body
