"""Блок 4 — Токенизатор.

Принимает объединённый список Entity из блоков 2 (regex) и 3 (ner), разрешает
пересечения внутри каждого сегмента, присваивает переиспользуемые токены вида
[TYPE_N] (TYPE = token_prefix из entity_types.yaml, нумерация сквозная по префиксу)
и собирает анонимизированный текст всего документа.

Публичные функции:
  - tokenize(doc, entities, config_path, enabled_types=None) -> (анонимизированный
    текст, list[Entity] с token)
  - resolve_for_masking(doc, entities, config_path) -> list[Entity] — дорогая
    первая половина tokenize (границы B3 + разрешение пересечений), общая для
    ВСЕХ наборов типов;
  - apply_masking(doc, kept, config_path, enabled_types=None) -> вторая половина;
    ЭТАП T1: единственное место, где выключенный тип не получает маску.
  - build_plain_text(doc) -> str  — та же сборка текста, но без подстановки токенов
    (эталон для сквозной приёмки блока 7).

dataclass'ы импортируются из models.py, не переопределены.
"""

import os
import sys
import uuid

from config_cache import default_config_path, load_yaml_cached
from models import Entity, SourceDocument, TextSegment

# B3-fix: сколько символов хвоста A / головы B берём в граничное окно детекции.
# 60 с запасом перекрывает самую длинную сущность (счёт из 20 цифр с пробелами-
# разделителями, адрес) — торчащий за окно фрагмент реального смысла не несёт.
_BOUNDARY_WINDOW = 60

# Приоритет типов среди двух regex одинаковой длины (раньше в списке = сильнее).
# Типы вне списка слабее всех перечисленных; между собой — по алфавиту entity_type.
# SNILS выше KPP: 9 схлопнутых цифр СНИЛС — ровно форма КПП, и при РАВНОЙ длине спана
# (вырожденный случай) маска обязана быть своего типа, а не чужого (C-корректность,
# нарушение «SNILS под KPP»). В обычном случае СНИЛС и так длиннее (9+2 цифры).
# BIRTHDATE выше DATE по той же причине (DATE сейчас enabled:false, но порядок фиксируем).
# INN_PERSON стоит рядом с INN (этап T2-INN): длины 10 и 12 взаимно исключают
# друг друга, поэтому эти два между собой не конкурируют никогда, но тип обязан
# быть В СПИСКЕ — иначе он слабее ВСЕХ перечисленных, и при равной длине спана
# с чужим реквизитом маска стала бы чужого типа.
# ЭТАП T2: PERCENT и TERM стоят В САМОМ КОНЦЕ списка — слабее ВСЕХ реквизитов
# при РАВНОЙ длине спана. Обоснование места, а не просто «дописали в хвост»:
# у реквизита есть либо контрольная сумма, либо жёсткая форма значения, либо
# якорь-слово; у процента и срока — только конструкция прозы вокруг числа.
# Значит при совпадении длины прав скорее реквизит, и маска обязана быть его
# типа (тип маски — это то, по чему считается утечка ПО ТИПАМ). Между собой
# PERCENT и TERM не конкурируют: их формы взаимно исключающи (процент требует
# знака/слова процента или формулы ключевой ставки, срок — «в течение»/«не
# позднее»), поэтому их взаимный порядок ни на один исход не влияет и выбран
# алфавитным. NB: правило длины (rule 2) срабатывает РАНЬШЕ приоритета, поэтому
# длинный спан срока мог бы победить реквизит внутри себя — от этого защищает
# не список, а САМ ПАТТЕРН: в хвосте основания цифры запрещены (entity_types.yaml).
#
# ЭТАП TYPE-FACTORY (сессия 2, шаг 1): списки ниже — УМОЛЧАНИЕ (исторические
# 15+3 типа), а рабочий порядок читается из ключа `arbitration_order`
# собранного entity_types.yaml — его пишет tests/corpus_v2/assemble_types.py
# из base-порядка и rank фабричных типов. Одно место истины: новый тип
# получает место в арбитраже из своего файла-описания, а не правкой этого
# файла. Фолбэк на умолчание — только если конфиг недоступен/без ключа
# (например, временный конфиг теста): для дофабричных типов оба источника
# дают ОДИН И ТОТ ЖЕ порядок (страж — tests/test_type_factory.py).
_REGEX_PRIORITY_DEFAULT = [
    "BANK_ACCOUNT", "OGRN", "INN", "INN_PERSON", "BIK", "SNILS", "KPP",
    "PASSPORT", "PHONE", "SUM", "EMAIL", "BIRTHDATE", "DATE",
    "PERCENT", "TERM",
]
_REGEX_PRIORITY = _REGEX_PRIORITY_DEFAULT
# Приоритет типов среди двух ner при равной длине/confidence и неидентичных интервалах.
# ЭТАП T-ARB: список для типов из anchor_registry/ner (method: anchor_registry ИЛИ
# method: ner в entity_types.yaml — Entity таких типов несёт detector="ner", см.
# _winner) — В ОТЛИЧИЕ от _REGEX_PRIORITY выше, этот список НЕ расширялся ни разу с
# момента появления ORG/PERSON (этапы A/B): третий якорный тип молча стал бы слабее
# всех трёх перечисленных при равной длине/confidence — то же самое молчаливое
# поражение, от которого страхует assert_priority_contract ниже. T3 введёт шесть
# новых якорных типов — КАЖДЫЙ обязан появиться здесь явно, иначе сборка упадёт.
_NER_PRIORITY_DEFAULT = ["ADDRESS", "ORG", "PERSON"]
_NER_PRIORITY = _NER_PRIORITY_DEFAULT


def _load_arbitration_order(config_path: str | None = None) -> None:
    """Читает arbitration_order из КОРНЕВОГО собранного entity_types.yaml один
    раз при импорте. Тихий фолбэк на умолчания намеренный: рабочие пути всегда
    идут через корневой конфиг, а тесты с временными конфигами не обязаны
    объявлять порядок (им хватает исторического).

    ЭТАП FIX-3: путь берётся из `config_cache.default_config_path()`, а не из
    `__file__`-арифметики. В PyInstaller-сборке конфиг лежит в `sys._MEIPASS`,
    и прежний путь его НЕ НАХОДИЛ: фолбэк срабатывал молча, фабричные типы
    выпадали из порядка, и собранная программа отказывалась шифровать любой
    документ (ArbitrationContractError). Найдено кругом собранной программы —
    ни один тест и ни один прогон корпуса этого не видел, потому что все они
    ходят по рабочему дереву."""
    global _REGEX_PRIORITY, _NER_PRIORITY
    path = config_path or default_config_path()
    try:
        config = load_yaml_cached(path)
    except Exception:
        return
    order = config.get("arbitration_order")
    if not isinstance(order, dict):
        return
    regex_order = order.get("regex")
    ner_order = order.get("ner")
    if isinstance(regex_order, list) and regex_order:
        _REGEX_PRIORITY = list(regex_order)
    if isinstance(ner_order, list) and ner_order:
        _NER_PRIORITY = list(ner_order)


_load_arbitration_order()


class ArbitrationContractError(Exception):
    """ЭТАП T-ARB: тип entity_types.yaml с token_prefix (значит — маскируется,
    значит — участвует в разрешении пересечений) без места в списке приоритетов
    своего механизма. ДО этого этапа забытый тип молча становился слабее ВСЕХ
    перечисленных (_regex_rank/_ner_rank, ветка «прочие: (1, entity_type)») —
    поражение обнаруживалось только на живом пересечении, никогда при сборке.
    Теперь это ошибка конфигурации, а не тихий проигрыш."""


def _arbitration_mechanism(spec: dict) -> str | None:
    """Каким списком приоритетов _winner арбитрирует пересечения этого типа:
    'regex' -> _REGEX_PRIORITY (method: regex); 'ner' -> _NER_PRIORITY
    (method: ner ИЛИ method: anchor_registry — оба дают Entity.detector=="ner",
    см. anchor_registry.py:1369 и ner_detector.py). None — тип не эмитит Entity
    ни одним из этих двух механизмов (сегодня таких в конфиге нет) и в контракте
    ниже не проверяется."""
    method = spec.get("method")
    if method == "regex":
        return "regex"
    if method in ("ner", "anchor_registry"):
        return "ner"
    return None


def assert_priority_contract(config_path: str) -> None:
    """ЭТАП T-ARB, ШАГ 2 — страж контракта арбитража. Каждый тип entity_types.yaml
    с `token_prefix` (значит замаскируется, значит участвует в _resolve_overlaps)
    ОБЯЗАН стоять в списке приоритетов своего механизма (_REGEX_PRIORITY у
    method: regex; _NER_PRIORITY у method: ner/anchor_registry). Забытый тип —
    ошибка КОНФИГУРАЦИИ (новый тип вписан в entity_types.yaml, но не вписан в
    арбитраж), а не свойство рантайма: роняем явно, с именем типа, а не отдаём
    его как молчаливо слабейшего в _regex_rank/_ner_rank.

    Зовётся из resolve_for_masking — ДО того, как _resolve_overlaps вообще
    посчитает хоть одно пересечение (иначе забытый тип успел бы молча
    проиграть раньше, чем сборка о нём узнает)."""
    config = load_yaml_cached(config_path)

    missing: list[str] = []
    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or "token_prefix" not in spec:
            continue
        mechanism = _arbitration_mechanism(spec)
        if mechanism is None:
            continue
        priority_list = _REGEX_PRIORITY if mechanism == "regex" else _NER_PRIORITY
        if entity_type not in priority_list:
            missing.append(f"{entity_type} (механизм: {mechanism})")

    if missing:
        raise ArbitrationContractError(
            "Тип(ы) конфига без места в списке приоритетов арбитража пересечений "
            "(entity_types.yaml объявляет token_prefix, но src/tokenizer.py не "
            "знает приоритет): " + "; ".join(missing) + ". Впишите тип в "
            "_REGEX_PRIORITY или _NER_PRIORITY (src/tokenizer.py) явно, с "
            "обоснованием места в комментарии — иначе тип молча проигрывает "
            "любое пересечение с уже перечисленными."
        )


# ЭТАП T2 — ИНДЕКС СРАВНИВАЕТСЯ ЧИСЛОМ, А НЕ СТРОКОЙ.
# Было `str(index)`, и второй элемент кортежа сравнивался ЛЕКСИКОГРАФИЧЕСКИ:
# '10' < '2', то есть десятый тип списка оказывался СИЛЬНЕЕ второго. Пока в
# списке было девять с небольшим имён, дефект был латентным (порядок совпадал с
# задуманным на индексах 0..9); с ростом списка он начал переворачивать хвост:
# EMAIL/BIRTHDATE/DATE (индексы 10..12) вставали ВЫШЕ INN/BIK/SNILS/KPP/
# PASSPORT/PHONE/SUM, а новые PERCENT/TERM (13..14) — выше них всех, то есть
# ровно наоборот замыслу (см. комментарий у _REGEX_PRIORITY).
# Дефект НАЙДЕН на этом этапе и чинится здесь же, потому что без починки
# обоснование места новых типов в списке было бы ЛОЖНЫМ.
# Второй элемент оставлен разнотипным (int для перечисленных, str для прочих)
# ТОЛЬКО внутри своей ветки — кортежи (0, int) и (1, str) сравниваются между
# собой по ПЕРВОМУ элементу и до второго не доходят.
def _regex_rank(entity_type: str) -> tuple[int, object]:
    """Меньший кортеж = сильнее. Перечисленные типы: (0, индекс); прочие: (1, entity_type)."""
    if entity_type in _REGEX_PRIORITY:
        return (0, _REGEX_PRIORITY.index(entity_type))
    return (1, entity_type)


def _ner_rank(entity_type: str) -> tuple[int, object]:
    """Меньший кортеж = сильнее (для ner-tie-break по типу)."""
    if entity_type in _NER_PRIORITY:
        return (0, _NER_PRIORITY.index(entity_type))
    return (1, entity_type)


def _overlaps(a: Entity, b: Entity) -> bool:
    """True, если интервалы [start, end) двух entity имеют непустое пересечение."""
    return max(a.start, b.start) < min(a.end, b.end)


def _winner(a: Entity, b: Entity) -> Entity:
    """Возвращает сильнейший из пары по правилу разрешения пересечений (блок 4 ТЗ).
    Тотальный порядок: rule 5 гарантирует детерминированный tie-break без участия id."""
    # rule 1: regex побеждает ner
    if a.detector != b.detector:
        return a if a.detector == "regex" else b

    if a.detector == "regex":
        # rule 2: более длинный span
        la, lb = a.end - a.start, b.end - b.start
        if la != lb:
            return a if la > lb else b
        # rule 3: приоритет типа
        ra, rb = _regex_rank(a.entity_type), _regex_rank(b.entity_type)
        if ra != rb:
            return a if ra < rb else b
        # иначе -> rule 5
    else:  # оба ner
        # ЭТАП T-ARB — B-ADDR-HOMONYM: единственное известное место, где сила
        # ЯКОРЯ обязана решать раньше длины (см. docs/archive/reports/T_ARB_REPORT.md). ADDRESS
        # эмитится ТОЛЬКО когда _addr_span_strong уже подтвердил якорь
        # (существование ADDRESS-сущности САМО есть доказательство силы) — а
        # PERSON без единого внутреннего пробела/точки в original_text не несёт
        # своей формы ФИО («фамилия+имя/отчество»/«фамилия+инициалы» — те ВСЕГДА
        # многословны): это либо голая фамилия по внешнему маркеру, либо
        # падежное вхождение реестра, якорь заведомо слабее. До этапа T-ARB
        # такое пересечение вообще не могло возникнуть (адрес не строился на
        # территории ЛЮБОГО PER — см. ner_detector._address_barriers); теперь
        # строится, но ТОЛЬКО когда сам ADDRESS-кандидат уже прошёл проверку
        # силы, поэтому правило ниже не меняет ни одного прежнего исхода — им
        # раньше просто нечего было сравнивать. Многословный PER (само-якорь
        # ФИО) сюда не попадает и решается ниже как обычно.
        def _bare_person(e: Entity) -> bool:
            t = e.original_text
            return e.entity_type == "PERSON" and " " not in t and "." not in t

        if a.entity_type == "ADDRESS" and _bare_person(b):
            return a
        if b.entity_type == "ADDRESS" and _bare_person(a):
            return b

        # rule 4: более длинный span
        la, lb = a.end - a.start, b.end - b.start
        if la != lb:
            return a if la > lb else b
        # затем больший confidence
        if a.confidence != b.confidence:
            return a if a.confidence > b.confidence else b
        # неидентичные интервалы -> приоритет типа ADDRESS > ORG > PERSON
        if (a.start, a.end) != (b.start, b.end):
            ra, rb = _ner_rank(a.entity_type), _ner_rank(b.entity_type)
            if ra != rb:
                return a if ra < rb else b
            # равный тип -> меньший start
            if a.start != b.start:
                return a if a.start < b.start else b
        # иначе -> rule 5

    # rule 5: (entity_type, original_text) лексикографически, меньший побеждает
    ka = (a.entity_type, a.original_text)
    kb = (b.entity_type, b.original_text)
    if ka != kb:
        return a if ka < kb else b
    return a  # полностью идентичны — любой (токен всё равно один и тот же)


def _has_overlapping_pair(entities: list[Entity]) -> bool:
    """Есть ли среди сущностей хоть одна пересекающаяся пара.

    ЭТАП O2: было попарное сравнение O(E^2), и звалось оно ИЗ ЦИКЛА
    _resolve_overlaps (по одному витку на зафиксированного победителя) — то есть
    O(E^3) на сегменте с плотной разметкой. Теперь заметание по отсортированному
    началу, O(E log E), результат тот же БУЛЕВ.
    Эквивалентность: после сортировки по start у пары i<j имеем
    max(start_i, start_j) = start_j, поэтому _overlaps(i, j) ⟺
    start_j < end_i И start_j < end_j. Значит пересечение существует ⟺ найдётся j,
    у которого start_j меньше максимума end по всем предыдущим И меньше своего
    конца (вырожденный пустой интервал не пересекает ничего — как и в _overlaps).
    """
    max_end = None
    for e in sorted(entities, key=lambda x: x.start):
        if max_end is not None and e.start < max_end and e.start < e.end:
            return True
        if max_end is None or e.end > max_end:
            max_end = e.end
    return False


def _trim_to_free(loser: Entity, winner: Entity) -> list[Entity]:
    """Обрезает проигравшую сущность до части(ей), НЕ пересекающих победителя, вместо
    того чтобы выбросить её целиком (страховка W2-D1(б)). Пересечение победителя
    закрыто его токеном — утекать там нечему; но непересекающийся остаток проигравшего
    (напр. сам адрес слева от regex-ИНН) обязан сохранить свой токен, а не уйти в
    открытый текст. Возвращает 0/1/2 обрезка (2 — если победитель внутри проигравшего).
    Текст фрагмента режется из original_text проигравшего (равен срезу сегмента по
    [start:end]), поэтому seg_text не нужен. Крайние пробелы срезаются, чтобы токен не
    держал висячий разделитель; фрагмент из одних разделителей отбрасывается."""
    ws, we = winner.start, winner.end
    pieces: list[tuple[int, int]] = []
    if loser.start < ws:
        pieces.append((loser.start, min(loser.end, ws)))
    if loser.end > we:
        pieces.append((max(loser.start, we), loser.end))

    out: list[Entity] = []
    for s, e in pieces:
        # текст фрагмента через смещения внутри original_text проигравшего
        frag = loser.original_text[s - loser.start:e - loser.start]
        lstrip = len(frag) - len(frag.lstrip(" \t\r\n"))
        rstrip = len(frag) - len(frag.rstrip(" \t\r\n"))
        s += lstrip
        e -= rstrip
        if e <= s:
            continue
        frag = loser.original_text[s - loser.start:e - loser.start]
        if not frag.strip(" \t\r\n,;.:|«»\"'()"):
            continue  # остаток из одних разделителей — не сущность
        out.append(Entity(
            id=str(uuid.uuid4()),
            segment_id=loser.segment_id,
            start=s,
            end=e,
            original_text=frag,
            entity_type=loser.entity_type,
            detector=loser.detector,
            confidence=loser.confidence,
        ))
    return out


def _resolve_overlaps(
    entities: list[Entity],
    barrier_types: frozenset[str] = frozenset(),
    suppression_log: list | None = None,
) -> list[Entity]:
    """Разрешение пересечений внутри одного сегмента (полный алгоритм из ТЗ).

    Пока среди ещё не зафиксированных есть пересекающаяся пара: выбрать глобально
    сильнейшего, зафиксировать его, а всех, кто пересекается ИМЕННО с ним, ОБРЕЗАТЬ
    до непересекающейся части (не выбрасывать целиком — иначе адрес, поглотивший
    хвост regex-реквизита, утёк бы открытым текстом). Обрезки возвращаются в очередь
    и участвуют в разрешении дальше. Не пересекающиеся с победителем — не трогаем.

    ЭТАП T4 — ЗЕРКАЛО ПОДАВЛЕНИЯ. `barrier_types` — типы БЕЗ token_prefix
    (CLAUSE_REF/ROLE_TERM/COLLECTIVE): когда такой тип побеждает пересечение с
    типом, у которого token_prefix ЕСТЬ (реальный, маскируемый кандидат), это не
    обычный арбитраж T-ARB — это ПОДАВЛЕНИЕ (см. docs/archive/reports/T4_REPORT.md), и оно
    обязано быть НАБЛЮДАЕМЫМ: `suppression_log` (если передан) получает запись
    {suppressed_type, suppressor_type, segment_id, start, end} — start/end это
    ПЕРЕСЕЧЕНИЕ (та территория, что реально ушла из-под токена проигравшего).
    Без `suppression_log` (умолчание None) — поведение и цена побайтно те же,
    что до этапа T4 (используется только замером/гейтом)."""
    pending = list(entities)
    kept: list[Entity] = []

    while _has_overlapping_pair(pending):
        # глобально сильнейший среди всех ещё не зафиксированных
        winner = pending[0]
        for e in pending[1:]:
            winner = _winner(winner, e)
        kept.append(winner)
        # победитель зафиксирован; пересекающиеся с ним — обрезаются, остальные как есть
        new_pending: list[Entity] = []
        for e in pending:
            if e is winner:
                continue
            if _overlaps(e, winner):
                if (suppression_log is not None
                        and winner.entity_type in barrier_types
                        and e.entity_type not in barrier_types):
                    suppression_log.append({
                        "suppressed_type": e.entity_type,
                        "suppressor_type": winner.entity_type,
                        "segment_id": e.segment_id,
                        "start": max(e.start, winner.start),
                        "end": min(e.end, winner.end),
                    })
                new_pending.extend(_trim_to_free(e, winner))
            else:
                new_pending.append(e)
        pending = new_pending

    kept.extend(pending)  # сущности без единого пересечения проходят как есть
    return kept


def _load_token_prefixes(config_path: str) -> dict[str, str]:
    """entity_type -> token_prefix из entity_types.yaml. Файл обязан существовать."""
    config = load_yaml_cached(config_path)

    prefixes: dict[str, str] = {}
    for entity_type, spec in config["entity_types"].items():
        if isinstance(spec, dict) and "token_prefix" in spec:
            prefixes[entity_type] = spec["token_prefix"]
    return prefixes


def _render_segment(seg: TextSegment, seg_entities: list[Entity]) -> str:
    """Текст сегмента с подставленными токенами (подстановка от конца к началу)."""
    text = seg.text
    for e in sorted(seg_entities, key=lambda e: e.start, reverse=True):
        text = text[:e.start] + e.token + text[e.end:]
    return text


def _assemble(doc: SourceDocument, render) -> str:
    """Единый проход по segments; таблицы остаются на своём месте.
    render(seg) -> строка представления сегмента (с токенами или без)."""
    segs = doc.segments
    n = len(segs)
    units: list[str] = []
    i = 0
    while i < n:
        seg = segs[i]
        if seg.source_type == "docx_table_cell":
            table_index = seg.metadata["table_index"]
            cells: list[TextSegment] = []
            while (i < n
                   and segs[i].source_type == "docx_table_cell"
                   and segs[i].metadata["table_index"] == table_index):
                cells.append(segs[i])
                i += 1
            units.append(_render_table(cells, render))
        else:
            units.append(render(seg))
            i += 1
    return "\n".join(units)


def _render_table(cells: list[TextSegment], render) -> str:
    """Строки по возрастанию row_index, ячейки по col_index; ячейки — через ' | ',
    строки — через '\\n'. В тексте ячейки '\\n' заменяется на пробел (после подстановки)."""
    rows: dict[int, list[TextSegment]] = {}
    for c in cells:
        rows.setdefault(c.metadata["row_index"], []).append(c)

    row_strs: list[str] = []
    for r in sorted(rows):
        row_cells = sorted(rows[r], key=lambda c: c.metadata["col_index"])
        cell_strs = [render(c).replace("\n", " ") for c in row_cells]
        row_strs.append(" | ".join(cell_strs))
    return "\n".join(row_strs)


def build_plain_text(doc: SourceDocument) -> str:
    """Сборка исходного текста SourceDocument в один плоский текст без подстановки токенов.
    Разделители и порядок — те же, что в tokenize (эталон для приёмки блока 7)."""
    return _assemble(doc, lambda seg: seg.text)


def _boundary_sep(seg_a: TextSegment, seg_b: TextSegment) -> str:
    """Разделитель, который _assemble/build_plain_text вставил бы между двумя
    ИДУЩИМИ ПОДРЯД сегментами — но применительно к ПОСТРОЕНИЮ ГРАНИЧНОГО ОКНА.

    build_plain_text склеивает верхнеуровневые единицы через '\\n', а ячейки одной
    строки таблицы — через ' | ' (для читаемости итогового текста). Для окна
    детекции ' | ' НЕ подходит: этот литерал физически рвёт паттерны (телефон/ИНН
    через ' | ' не матчатся), а сущность, порванная границей соседних ячеек, обязана
    реконструироваться. Поэтому две соседние ячейки одной строки одной таблицы
    склеиваются в окне БЕЗ разделителя (''); во всех остальных случаях граница
    соответствует '\\n' финальной сборки. См. docs/archive/SHIFRATOR_SPEC_AI.md (блок 4,
    «Разделитель граничного окна») / docs/archive/handoffs/HANDOFF_4.md.
    """
    if (
        seg_a.source_type == "docx_table_cell"
        and seg_b.source_type == "docx_table_cell"
        and seg_a.metadata["table_index"] == seg_b.metadata["table_index"]
        and seg_a.metadata["row_index"] == seg_b.metadata["row_index"]
    ):
        return ""
    return "\n"


# B3-fix + этап C волны 2: разделитель между граничными окнами в СБАТЧЕННОМ блобе.
# Все окна склеиваются в один текст и детекторы гоняются ОДИН раз (а не 6000). Разделитель
# обязан быть таким, через который НИЧТО не «сшивается»: перенос строки рвёт [ ]-классовые
# реквизиты/SUM и email (в них нет \n), а невидимый U+2063 добивает границы слов и не
# встречается в нормальном тексте. Natasha по \n режет предложения — NER-спаны через стык
# окон не тянутся. Проверено в разведке (recon_h3e): 0 сущностей пересекает стык.
# ВНИМАНИЕ: этот разделитель — про БЛОБ детекции, он НЕ имеет отношения к разделителю
# _boundary_sep внутри самого окна (тот — осознанная семантика соседних ячеек, не трогать).
_BATCH_SEP = "\n⁣⁣⁣\n"


# --------------------------------------------------------------------------- #
#   ЭТАП S3 — cross-segment ORG: ремонт разорванного имени, а не расползание   #
# --------------------------------------------------------------------------- #
# Регрессия M-1 (вскрыта пересборкой baseline после мержа E′): проход ORG по
# граничным окнам эмитил ОБЕ половины всякого якоря, пересёкшего стык, — включая
# те, где никакого разрыва не было. anchor_search_view вырезает \n НАПРОЧЬ,
# поэтому «ПАО Сбербанк\nк/с 30101810…» в виде окна становится «…Сбербанкк/с…»,
# name-run переползает стык, и в соседний сегмент уходит маска из ОДНОЙ буквы
# («к» ×8, «Юридический» ×2, «с»»). Одиночная буква под маской не прячет ничего
# — это порча документа.
#
# Два структурных условия (списков слов не заводим — форма юрлица берётся из
# того же _ABBR_FORM_RE, на котором стоит якорь ORG этапов A/C′):
#   1. ПАРА ЭМИТИТСЯ, ТОЛЬКО ЕСЛИ СТЫК ДЕЙСТВИТЕЛЬНО РАЗОРВАЛ ИМЯ. Признак
#      «разорвал» — ни одна половина не накрыта посегментным ORG: ровно ради
#      этого класса E′ и делал проход («ПАО «Альтаи|р»» не находит никто). Если
#      организация уже найдена посегментно, через стык ползёт хвост якоря, а не
#      половина названия.
#   2. ПОЛОВИНА ЭМИТИТСЯ, ТОЛЬКО ЕСЛИ НЕСЁТ КУСОК ИМЕНИ: не короче 3 значащих
#      символов и не голая орг-форма («ПАО»/«ООО» без имени — не сущность).
_ORG_MIN_HALF = 3


def _org_half_carries_name(text: str) -> bool:
    """Несёт ли половина разорванного ORG кусок ИМЕНИ (условие 2 выше)."""
    import anchor_registry as _AR
    core = "".join(ch for ch in text if ch.isalnum())
    if len(core) < _ORG_MIN_HALF:
        return False
    return _AR._ABBR_FORM_RE.fullmatch(core.lower()) is None


def _org_pair_is_a_repair(win, ls: int, le: int, org_seg_spans) -> bool:
    """Условие 1 выше: разорвал ли стык имя, или якорь просто переполз в соседа."""
    start_a = win["tail_off"] + ls
    end_a = len(win["text_a"])
    end_b = le - win["head_start"]
    for seg, s, e in ((win["seg_a"], start_a, end_a), (win["seg_b"], 0, end_b)):
        for os_, oe_ in org_seg_spans.get(seg.id, ()):
            if max(s, os_) < min(e, oe_):
                return False
    return True


def _detect_boundary_entities(
    doc: SourceDocument,
    config_path: str,
    segment_entities: list[Entity] = (),
) -> list[Entity]:
    """B3-fix: находит сущности, РАЗОРВАННЫЕ границей соседних сегментов.

    detect_regex/detect_ner работают посегментно, поэтому значение, чей текст
    разложен на хвост сегмента A и голову сегмента B (перенос абзаца/строки или
    соседние ячейки таблицы), не находит ни один из них и утекает в открытом виде.

    Для каждой пары соседних сегментов строится окно `хвост A + разделитель + голова B`.
    Оставляются ТОЛЬКО сущности, чей интервал реально пересекает точку стыка (иначе это
    дубликат обычной посегментной детекции). Пересекающая сущность делится на пару
    Entity_A/Entity_B с реальными оффсетами внутри своих сегментов; половины идут дальше
    как две ОБЫЧНЫЕ независимые сущности (свой original_text — свой токен).

    ЭТАП C (батчинг): раньше по каждому из ~6000 окон вызывались detect_regex+detect_ner
    ОТДЕЛЬНО (6000×2 чтений конфига, 6000 запусков NER-тэггера — доминанта стоимости после
    снятия yargy со сканирования на этапе A). Теперь все окна склеиваются в один блоб через
    безопасный _BATCH_SEP, regex и NER-тэггер гоняются ОДИН раз, а найденные спаны
    раскладываются обратно по окнам через карту оффсетов. yargy остаётся ГЕЙТИРОВАННЫМ
    ПОСЕГМЕНТНО (как на этапе A): запускается только на коротком тексте окна с адресным
    анкором — блоб-скан yargy (256 с) НЕ воскрешается. Результат идентичен посегментному
    (батчинг меняет скорость, не состав сущностей); лишние чтения конфига исчезают сами
    (кэш конфига отдельно не нужен, см. RECON_REPORT).

    ЭТАП S3: `segment_entities` — сущности ПОСЕГМЕНТНОЙ детекции (результат
    `pipeline.run_detection`). Нужны cross-segment ORG этапа E′ как проверка
    «а разорвано ли вообще»: см. _org_pair_is_a_repair.
    """
    import bisect

    from regex_detector import (detect_regex, _load_regex_types,
                                _fold_anchor_context)
    from ner_detector import (
        _segmenter,
        _ner_tagger,
        _load_ner_config,
        _expand_person_span,
        _glue_address_matches,
        _addr_extractor,
        _build_address_spans,
        _finalize_address_spans,
        _filter_suspect_yargy,
        _address_barriers,
        _addr_has_seed,
    )
    from natasha import Doc

    segs = doc.segments

    # 1. Построение окон (та же геометрия, что и раньше).
    wins = []
    for i in range(len(segs) - 1):
        seg_a, seg_b = segs[i], segs[i + 1]
        if not seg_a.text or not seg_b.text:
            continue  # пустой сегмент — стыка контента нет
        text_a, text_b = seg_a.text, seg_b.text
        tail_off = max(0, len(text_a) - _BOUNDARY_WINDOW)
        tail = text_a[tail_off:]
        head = text_b[:_BOUNDARY_WINDOW]
        sep = _boundary_sep(seg_a, seg_b)
        window = tail + sep + head
        wins.append({
            "seg_a": seg_a, "seg_b": seg_b,
            "text_a": text_a, "text_b": text_b,
            "tail_off": tail_off,
            "tail_end": len(tail),               # конец хвоста A в окне
            "head_start": len(tail) + len(sep),  # начало головы B в окне
            "window": window,
        })

    if not wins:
        return []

    # 2. Блоб всех окон + карта стартовых оффсетов каждого окна в блобе.
    win_texts = [w["window"] for w in wins]
    starts: list[int] = []
    pos = 0
    for wt in win_texts:
        starts.append(pos)
        pos += len(wt) + len(_BATCH_SEP)
    blob = _BATCH_SEP.join(win_texts)

    def _locate(s: int, e: int):
        """Блоб-оффсеты [s,e) -> (индекс окна, локальные s,e) или None, если спан
        пересёк разделитель (сшивание — не должно происходить при безопасном _BATCH_SEP)."""
        k = bisect.bisect_right(starts, s) - 1
        ls, le = s - starts[k], e - starts[k]
        if le > len(win_texts[k]):
            return None
        return k, ls, le

    # локальные спаны на окно: (start, end, entity_type, detector, confidence)
    per_win: list[list[tuple]] = [[] for _ in wins]
    #: ЭТАП FIX-2 — сущности, найденные ЯКОРЕМ ИЗ СОСЕДНЕЙ ЯЧЕЙКИ (шаг 3c).
    #: Они не пересекают стык и потому мимо шага 4 — уходят в результат прямо.
    anchor_across: list[Entity] = []

    blob_doc = SourceDocument(
        segments=[TextSegment(id="__batch__", text=blob, source_type="txt_line", metadata={})],
        source_format=doc.source_format, source_path=doc.source_path,
    )

    # 3a. Regex — один проход по блобу (одно чтение конфига вместо 6000).
    for e in detect_regex(blob_doc, config_path):
        loc = _locate(e.start, e.end)
        if loc is not None:
            k, ls, le = loc
            per_win[k].append((ls, le, e.entity_type, e.detector, e.confidence))

    # 3a'. ЭТАП A6 — сборка реквизита, разорванного стыком '\n'. Regex-проход
    # выше через '\n' не матчится осознанно (A2-NEWLINE-CROSS), поэтому
    # значение, рваное границей сегментов-строк/абзацев, не находил никто
    # (остаток A5: ACCOUNT/OGRN). Сборка — не ослабление паттернов, а цепь со
    # строгими стражами (multispan._match_seam_type: якорь + точная длина
    # ОДНОГО значения + КС + «ни одна сторона разрыва не целое значение сама
    # по себе»). Стыки ячеек одной строки (sep '' -> tail_end == head_start)
    # пропускаются: их уже склеил сам текст окна для штатного regex-прохода.
    from multispan import collect_seam_entities
    for k, w in enumerate(wins):
        if w["tail_end"] == w["head_start"]:
            continue
        for (ls, le, etype) in collect_seam_entities(
                w["window"], w["tail_end"], w["head_start"], config_path):
            per_win[k].append((ls, le, etype, "regex", 1.0))

    # 3a''. ЭТАП LAYOUT — РЕМОНТ СТЫКА: значение, разорванное границей
    # сегментов НЕ по границе цифровых групп (это зона A6 выше), а произвольно:
    # «makarov@gma\nil.com», «Кузнецо\nва А. М.», «+7 (49\n9) …». Шовный '\n'
    # ВЫБРАСЫВАЕТСЯ из текста окна, по залеченному тексту гоняются штатные
    # детекторы; принимается ТОЛЬКО спан, пересекающий точку стыка, и только
    # через стражи layout_repair (поглощение/«ремонт, а не расползание»):
    #   * regex-типы — на любом стыке: различение «мягкий разрыв vs граница»
    #     несут их собственный якорь/КС/фиксированная форма, а стопку двух
    #     целых значений отсекает страж (обе половины целы порознь = дубль);
    #   * PERSON (структурный движок) — только если стык рвёт СЛОВО со
    #     строчным продолжением (заглавная справа = новый структурный блок).
    # Эмит парой Entity_A/Entity_B делает общий шаг 4 (контракт B3,
    # обратимость доказана round-trip-тестами).
    import layout_repair as _LR
    if _LR.ENABLED:
        seg_spans_all: dict[str, list[tuple[int, int, str]]] = {}
        for e in (segment_entities or ()):
            seg_spans_all.setdefault(e.segment_id, []).append(
                (e.start, e.end, e.entity_type))

        def _lr_guard_ok(w, ls: int, le_w: int, etype: str) -> bool:
            """Стражи приёма залеченного спана окна (координаты ОКНА [ls, le_w)).

            Пересечение половины с посегментной сущностью ЧУЖОГО типа или
            частичное пересечение со своей — отказ (направление ошибки). Обе
            половины, целиком накрытые своими же посегментными сущностями, —
            стопка двух целых значений либо дубль, не ремонт (урок A2/S3)."""
            halves = [
                (w["seg_a"].id, w["tail_off"] + ls, len(w["text_a"])),
                (w["seg_b"].id, 0, le_w - w["head_start"]),
            ]
            covered = 0
            for seg_id, hs, he in halves:
                for ps, pe, ptype in seg_spans_all.get(seg_id, ()):
                    if max(hs, ps) >= min(he, pe):
                        continue
                    if ptype != etype:
                        return False
                    if ps <= hs and he <= pe:
                        covered += 1
                        break
                    if not (hs <= ps and pe <= he):
                        return False
            return covered < 2

        rep_idx: list[int] = []
        rep_texts: list[str] = []
        person_rows: list[int] = []
        for k, w in enumerate(wins):
            if w["tail_end"] == w["head_start"]:
                continue   # соседние ячейки одной строки: шва в тексте окна нет
            _rx_ok, person_ok = _LR.seam_eligibility(
                w["window"], w["tail_end"], w["head_start"])
            rep_idx.append(k)
            rep_texts.append(w["window"][:w["tail_end"]]
                             + w["window"][w["head_start"]:])
            if person_ok:
                person_rows.append(len(rep_texts) - 1)

        if rep_texts:
            starts2: list[int] = []
            pos2 = 0
            for rt in rep_texts:
                starts2.append(pos2)
                pos2 += len(rt) + len(_BATCH_SEP)
            blob2_doc = SourceDocument(
                segments=[TextSegment(id="__lrbatch__",
                                      text=_BATCH_SEP.join(rep_texts),
                                      source_type="txt_line", metadata={})],
                source_format=doc.source_format, source_path=doc.source_path,
            )
            for e in detect_regex(blob2_doc, config_path):
                m = bisect.bisect_right(starts2, e.start) - 1
                ls, le = e.start - starts2[m], e.end - starts2[m]
                if le > len(rep_texts[m]):
                    continue   # спан пересёк разделитель блоба — не бывает
                k = rep_idx[m]
                w = wins[k]
                j = w["tail_end"]
                if not (ls < j and le > j):
                    continue   # стык не пересечён — дубль посегментной детекции
                le_w = le + (w["head_start"] - w["tail_end"])
                if _lr_guard_ok(w, ls, le_w, e.entity_type):
                    per_win[k].append((ls, le_w, e.entity_type, "regex", 1.0))

        if person_rows:
            import anchor_registry as _ARL
            row_by_id = {f"__lrw{m}__": m for m in person_rows}
            p_doc = SourceDocument(
                segments=[TextSegment(id=f"__lrw{m}__", text=rep_texts[m],
                                      source_type="txt_line", metadata={})
                          for m in person_rows],
                source_format=doc.source_format, source_path=doc.source_path,
            )
            try:
                p_entities = _ARL.detect_structural(p_doc, regex_entities=[])
            except Exception:
                p_entities = []
            for e in p_entities:
                if e.entity_type != "PERSON":
                    continue
                m = row_by_id[e.segment_id]
                k = rep_idx[m]
                w = wins[k]
                j = w["tail_end"]
                if not (e.start < j and e.end > j):
                    continue
                le_w = e.end + (w["head_start"] - w["tail_end"])
                if _lr_guard_ok(w, e.start, le_w, "PERSON"):
                    per_win[k].append((e.start, le_w, "PERSON", "ner", 1.0))

    # 3c. ЭТАП FIX-2 — ЯКОРЬ ЧЕРЕЗ ГРАНИЦУ ЯЧЕЙКИ (долг DOG-ANCHOR-SPLIT).
    #
    # ЧТО ЭТО ЗА КЛАСС. A6 закрывал разрыв ЗНАЧЕНИЯ границей сегментов; здесь
    # значение целое, а границей оторван ЯКОРЬ: «БИК» в одной ячейке строки,
    # число — в соседней. Посегментная детекция якоря не видит (он в чужом
    # сегменте), а шаг 4 ниже отдаёт только спаны, ПЕРЕСЁКШИЕ стык, — этот не
    # пересекает ничего. Значение проваливалось между двумя проходами. Ровно так
    # этап NEG, поставив BIK якорь-шлагбаум, потерял единственный настоящий БИК
    # реального договора.
    #
    # ГРАНИЦА РАЗЛИЧАЕТСЯ ПО РАЗМЕТКЕ, НЕ ПО СИМВОЛУ. Берутся ТОЛЬКО окна с
    # `tail_end == head_start`, то есть с пустым `_boundary_sep`, а он пуст
    # исключительно у двух ячеек ОДНОЙ СТРОКИ ОДНОЙ ТАБЛИЦЫ (проверка по
    # metadata: table_index/row_index). Мягкий перенос и граница абзаца сюда не
    # попадают вовсе — там `_has_anchor` и так обязан остановиться на
    # переводе строки.
    #
    # НАПРАВЛЕНИЕ ОШИБКИ — «лучше не найти, чем захватить лишнее»: приклеенный
    # хвост виден ТОЛЬКО якорю (`detect_regex(anchor_prefix=…)`), спан ищется и
    # вырезается по-прежнему из своего сегмента, анти-якорь и ограничитель
    # соседних цифр смотрят на ту же склейку и потому только ужесточают. Ничего
    # нового не эмитится там, где посегментный проход уже нашёл то же самое.
    anchored_res = [spec[4] for spec in _load_regex_types(config_path)
                    if spec[4] is not None]
    if anchored_res:
        seen_seg = {(e.segment_id, e.start, e.end, e.entity_type)
                    for e in (segment_entities or ())}
        for w in wins:
            if w["tail_end"] != w["head_start"]:
                continue                      # не соседние ячейки одной строки
            tail = w["window"][:w["tail_end"]]
            # Дешёвый отсев: без якорного слова в хвосте склейка ничего не даёт,
            # а detect_regex по каждой паре ячеек стоил бы заметно.
            if not any(a.search(_fold_anchor_context(tail)) for a in anchored_res):
                continue
            seg_b = w["seg_b"]
            tmp_doc = SourceDocument(
                segments=[seg_b], source_format=doc.source_format,
                source_path=doc.source_path,
            )
            base = {(e.start, e.end, e.entity_type)
                    for e in detect_regex(tmp_doc, config_path)}
            for e in detect_regex(tmp_doc, config_path, anchor_prefix=tail):
                if (e.start, e.end, e.entity_type) in base:
                    continue              # нашлось бы и без соседней ячейки
                if (seg_b.id, e.start, e.end, e.entity_type) in seen_seg:
                    continue              # уже есть у посегментного прохода
                seen_seg.add((seg_b.id, e.start, e.end, e.entity_type))
                anchor_across.append(e)

    # 3b. NER-тэггер — один проход по блобу; PER/ORG раскладываются по окнам, LOC копятся
    # как адресные анкоры. yargy — ПОСЕГМЕНТНО, только на окнах с анкором (короткий текст).
    ner_label_map, addr_types = _load_ner_config(config_path)
    if ner_label_map or addr_types:
        loc_by_win: list[list[tuple[int, int]]] = [[] for _ in wins]
        ner_by_win: list[list[tuple[int, int]]] = [[] for _ in wins]  # PER/ORG для отсева yargy-ложняков
        nd = Doc(blob)
        nd.segment(_segmenter)
        nd.tag_ner(_ner_tagger)
        for span in nd.spans:
            loc = _locate(span.start, span.stop)
            if loc is None:
                continue
            k, ls, le = loc
            if span.type == "LOC":
                loc_by_win[k].append((ls, le))
                continue
            entity_type = ner_label_map.get(span.type)
            if entity_type is None:
                continue
            if span.type == "PER":
                ls, le = _expand_person_span(win_texts[k], ls, le)
            ner_by_win[k].append((ls, le))
            per_win[k].append((ls, le, entity_type, "ner", 1.0))

        if addr_types:
            for k, wt in enumerate(win_texts):
                if loc_by_win[k] or _addr_has_seed(wt):
                    yargy_spans = _glue_address_matches(wt, list(_addr_extractor(wt)))
                    yargy_spans = _filter_suspect_yargy(wt, yargy_spans, ner_by_win[k], loc_by_win[k])
                    # Барьеры в окне: regex-реквизиты + настоящие PER/ORG (симметрично
                    # основному проходу — адрес не поглощает соседнее поле на стыке).
                    regex_spans = [
                        (ls, le) for (ls, le, _et, det, _cf) in per_win[k] if det == "regex"
                    ]
                    perorg_spans = [
                        (ls, le) for (ls, le, et, det, _cf) in per_win[k]
                        if det != "regex" and et in ("PERSON", "ORG")
                    ]
                    occupied = _address_barriers(wt, perorg_spans, regex_spans)
                    raw_addr = _build_address_spans(wt, loc_by_win[k] + yargy_spans, occupied)
                    # ЭТАП C: тот же строгий якорь+обрезка, что в основном проходе —
                    # иначе жадный адрес возвращается через кросс-сегментный B3-путь.
                    for s, e in _finalize_address_spans(wt, raw_addr, occupied):
                        for addr_type in addr_types:
                            per_win[k].append((s, e, addr_type, "ner", 1.0))

            # --- ЭТАП E′: РАСПЛЕТЁННЫЙ вид окна — адрес, рваный переносом ЧЕРЕЗ
            # ГРАНИЦУ СЕГМЕНТОВ (в .txt строка = сегмент: «Ленинградск\nая 36» —
            # это два сегмента). Тот же вид/конвейер/строгий якорь, что во втором
            # проходе detect_ner; анти-склейка двух адресов по позициям \n (обе
            # половины строгие -> деление; после деления спан не пересекает стык
            # и отбрасывается фильтром шага 4 как дубль посегментной детекции). ---
            from ner_detector import _addr_deweave, _addr_split_at_newlines
            from normalizer import normalize_for_detection, src_to_norm
            for k, wt in enumerate(win_texts):
                woven, w_idx, changed = _addr_deweave(wt)
                # ТОЛЬКО окна с реальной СКЛЕЙКОЙ (рваное слово/индекс через стык).
                # Гонять вид по каждому окну (стык всегда '\n') нельзя: пропадает
                # граница предложения, расширение краёв ползёт через стык в чужие
                # метки/номера пунктов («ИНН», «3.2») — 182 мусорных маски на
                # ЧИСТЫХ доках в замере. Не-склеенные стыки полностью покрывает
                # штатный посегментный проход.
                if not changed:
                    continue
                vnorm, n_idx = normalize_for_detection(woven)
                vmap = [w_idx[i] for i in n_idx]          # view -> окно
                loc_v = [src_to_norm(vmap, s, e) for s, e in loc_by_win[k]]
                # склейка (changed) — сама по себе сид: LOC/маркер на рваном
                # тексте молчат, а строгий якорь всё равно решает после сборки.
                if not (loc_v or _addr_has_seed(vnorm) or changed):
                    continue
                yargy_v = _glue_address_matches(vnorm, list(_addr_extractor(vnorm)))
                ner_v = [src_to_norm(vmap, s, e) for s, e in ner_by_win[k]]
                yargy_v = _filter_suspect_yargy(vnorm, yargy_v, ner_v, loc_v)
                regex_v = [src_to_norm(vmap, ls, le)
                           for (ls, le, _et, det, _cf) in per_win[k] if det == "regex"]
                perorg_v = [src_to_norm(vmap, ls, le)
                            for (ls, le, et, det, _cf) in per_win[k]
                            if det != "regex" and et in ("PERSON", "ORG")]
                occ_v = _address_barriers(vnorm, perorg_v, regex_v)
                raw_v = _build_address_spans(vnorm, loc_v + yargy_v, occ_v)
                final_v = _finalize_address_spans(vnorm, raw_v, occ_v)
                nl_pos = [i for i, si in enumerate(vmap) if wt[si] in "\n\r"]
                final_v = _addr_split_at_newlines(vnorm, final_v, nl_pos)
                from ner_detector import _addr_trim_tail_lines
                final_v = _addr_trim_tail_lines(vnorm, final_v, nl_pos)
                from ner_detector import _deweave_glue_positions, _span_has_glue
                glue_w = _deweave_glue_positions(w_idx)
                for s, e in final_v:
                    ws_, we_ = vmap[s], vmap[e - 1] + 1
                    # эмитим ТОЛЬКО спан, содержащий точку СКЛЕЙКИ deweave: окно
                    # могло стать changed из-за легитимного NBSP-разделителя цифр
                    # в другом месте — тогда этот адрес полностью покрыт штатным
                    # посегментным проходом, а видовой спан без склейки лишь
                    # тащит края через стык. Скачки normalize (дефисы) — не склейка.
                    if not _span_has_glue(ws_, we_, glue_w):
                        continue
                    for addr_type in addr_types:
                        per_win[k].append((ws_, we_, addr_type, "ner", 1.0))

    # --- ЭТАП E′: ORG, РВАНЫЙ ГРАНИЦЕЙ СЕГМЕНТОВ (cross-segment, контракт
    # SplitEntities / находка A-1). OrgAnchorDetector на тексте окна: его
    # anchor_search_view вырезает \n, «ПАО «Альтаи\nр»» склеивается в
    # «ПАО «Альтаир»» — якорные правила C′ НЕ трогаются, детектору просто дают
    # целый текст. Берём ТОЛЬКО якоря, пересекающие стык (прочие — дубли
    # посегментного прохода detect_structural); шаг 4 разрежет их на пару
    # половин B3-стилем (каждая — свой токен, восстановление посимвольно). ---
    import anchor_registry as _AR
    _org_det = _AR.OrgAnchorDetector()
    # ЭТАП S3: посегментные ORG-спаны — по ним видно, БЫЛА ЛИ сущность разорвана
    # стыком (см. _org_pair_is_a_repair).
    org_seg_spans: dict[str, list[tuple[int, int]]] = {}
    for e in segment_entities or ():
        if e.entity_type == "ORG":
            org_seg_spans.setdefault(e.segment_id, []).append((e.start, e.end))
    for k, w in enumerate(wins):
        wt = w["window"]
        seg_tmp = TextSegment(id="__win__", text=wt, source_type="txt_line", metadata={})
        norm_w, omap_w = _AR.anchor_search_view(seg_tmp)
        low_w = norm_w.lower()
        try:
            org_anchors = _org_det.detect(seg_tmp, norm_w, low_w, omap_w, [])
        except Exception:
            org_anchors = []
        for a in org_anchors:
            if a.span_end <= a.span_start:
                continue
            ws_ = omap_w[a.span_start]
            we_ = omap_w[a.span_end - 1] + 1
            if ws_ < w["tail_end"] and we_ > w["head_start"]:
                if not _org_pair_is_a_repair(w, ws_, we_, org_seg_spans):
                    continue
                per_win[k].append((ws_, we_, "ORG", "ner", 1.0))

    # 4. Пересекающие стык спаны -> пары Entity_A/Entity_B (та же логика, что и раньше).
    extra: list[Entity] = []
    for k, w in enumerate(wins):
        tail_end, head_start = w["tail_end"], w["head_start"]
        text_a, text_b, tail_off = w["text_a"], w["text_b"], w["tail_off"]
        for (ls, le, entity_type, detector, confidence) in per_win[k]:
            if not (ls < tail_end and le > head_start):
                continue
            start_a = tail_off + ls
            end_a = len(text_a)
            end_b = le - head_start
            halves = [
                (w["seg_a"].id, start_a, end_a, text_a[start_a:end_a]),
                (w["seg_b"].id, 0, end_b, text_b[0:end_b]),
            ]
            # ЭТАП S3, условие 2 (только ORG — у реквизитов короткая половина
            # это кусок ЗНАЧЕНИЯ и обязана быть закрыта).
            if entity_type == "ORG":
                halves = [h for h in halves if _org_half_carries_name(h[3])]
            # ЭТАП LAYOUT: PERSON в окне рождает только ремонт стыка (Natasha-PER
            # выключена этапом B) — половина из одной буквы не прячет ничего,
            # а документ портит (урок S3 про однобуквенные маски).
            elif entity_type == "PERSON":
                halves = [h for h in halves
                          if sum(ch.isalnum() for ch in h[3]) >= 2]
            for seg_id, hs, he, htext in halves:
                extra.append(Entity(
                    id=str(uuid.uuid4()), segment_id=seg_id,
                    start=hs, end=he, original_text=htext,
                    entity_type=entity_type, detector=detector, confidence=confidence,
                ))

    return extra + anchor_across


def _barrier_types(config_path: str) -> frozenset[str]:
    """ЭТАП T4: типы БЕЗ `token_prefix` в entity_types.yaml (CLAUSE_REF/ROLE_TERM/
    COLLECTIVE — отрицательные классы) — те, что никогда не маскируются, но
    участвуют в разрешении пересечений барьером. Тип без записи в конфиге вовсе
    сюда попасть не может (перечисление только объявленных типов)."""
    config = load_yaml_cached(config_path)
    return frozenset(
        t for t, spec in config.get("entity_types", {}).items()
        if isinstance(spec, dict) and "token_prefix" not in spec
    )


def resolve_for_masking(
    doc: SourceDocument,
    entities: list[Entity],
    config_path: str,
    suppression_log: list | None = None,
) -> list[Entity]:
    """Всё, что предшествует простановке масок: B3-сущности границ + разрешение
    пересечений + порядок появления. Результат — сущности ВСЕХ типов, включая
    выключенные пользователем (этап T1: выключенный тип обязан доучаствовать в
    разрешении пересечений барьером, иначе поедут границы соседей) И
    отрицательные классы этапа T4 (CLAUSE_REF/ROLE_TERM/COLLECTIVE — без
    token_prefix, отфильтровываются позже, в apply_masking).

    Вынесено из `tokenize` отдельной функцией, потому что это дорогая часть
    (внутри — второй проход детекции по граничным окнам), а простановка масок
    поверх готового результата дешёвая: приёмка этапа T1 (14 наборов «все типы
    кроме одного») считает эту функцию ОДИН раз и рендерит 15 вариантов.

    `suppression_log` (ЭТАП T4, зеркало подавления) — необязательный список;
    если передан, получает по записи на каждый случай, когда отрицательный
    класс победил пересечение с маскируемым типом (см. `_resolve_overlaps`).
    Умолчание `None` — поведение и цена побайтно те же, что до этапа T4.
    """
    # ЭТАП T-ARB: страж контракта арбитража — ДО первого пересечения (см.
    # assert_priority_contract). Дешёвое чтение yaml на фоне остальной работы
    # этой функции (второй проход детекции по граничным окнам).
    assert_priority_contract(config_path)
    barrier_types = _barrier_types(config_path)

    boundary_entities = _detect_boundary_entities(doc, config_path, entities)
    entities = list(entities) + boundary_entities

    by_segment: dict[str, list[Entity]] = {}
    for e in entities:
        by_segment.setdefault(e.segment_id, []).append(e)

    kept: list[Entity] = []
    for seg in doc.segments:
        group = by_segment.get(seg.id)
        if group:
            kept.extend(_resolve_overlaps(group, barrier_types, suppression_log))

    seg_index = {seg.id: idx for idx, seg in enumerate(doc.segments)}
    kept.sort(key=lambda e: (seg_index[e.segment_id], e.start))
    return kept


def apply_masking(
    doc: SourceDocument,
    kept: list[Entity],
    config_path: str,
    enabled_types=None,
) -> tuple[str, list[Entity]]:
    """Простановка масок поверх РАЗРЕШЁННОГО списка сущностей.

    ЭТАП T1 — ЕДИНСТВЕННАЯ точка фильтра типов. `enabled_types` — множество
    включённых `entity_type` (или None = набор «Максимум», фильтр не
    применяется). Сущность выключенного типа уже отработала барьером в
    `resolve_for_masking` и здесь просто не получает токена: границы масок
    остальных типов от этого не меняются НИ НА СИМВОЛ, потому что решались до
    фильтра. Нумерация токенов ведётся отдельным счётчиком на префикс
    (`counters`), поэтому выпадение целого типа не сдвигает номера чужих масок.

    ЭТАП T4 — отрицательные классы (CLAUSE_REF/ROLE_TERM/COLLECTIVE, БЕЗ
    token_prefix в конфиге) отфильтровываются здесь ЖЕ, тем же способом, что и
    выключенный тип: они уже отработали барьером в `resolve_for_masking`
    (см. `_barrier_types`/`_resolve_overlaps`) и токена не получают физически.
    Не через `enabled_types`, потому что они не участвуют в пользовательских
    наборах T1 (`type_policy.known_types` их тоже не видит) — это отдельный,
    не пользовательский выключатель.

    ФИЛЬТР ТОЧЕЧНЫЙ — ровно объявленные `_barrier_types(config_path)`, НЕ
    «любой тип без token_prefix»: `Entity` с ПОДЛИННО неизвестным `entity_type`
    (опечатка/дефект где-то в конвейере) обязана падать `KeyError` в
    `_new_token` ниже, как и до этапа T4 (см. `tests/test_tokenizer.py::
    TestTokenizeErrors`) — тихий пропуск НЕОБЪЯВЛЕННОГО типа был бы ровно тем
    молчаливым исчезновением маски, от которого этот этап должен защищать, а
    не которое создавать.
    """
    token_prefixes = _load_token_prefixes(config_path)
    barrier_types = _barrier_types(config_path)
    kept = [e for e in kept if e.entity_type not in barrier_types]

    if enabled_types is not None:
        kept = [e for e in kept if e.entity_type in enabled_types]

    # --- Присвоение токенов ---
    # Правило переиспользования (этап E, часть 2 — ЕДИНЫЙ ID НА СУЩНОСТЬ):
    #   * у сущности есть group_key (реестр кореференции ORG/PER) — токен один на
    #     ГРУППУ: «Меркурий»/«Меркурия»/«Меркурием» -> один [ORG_N]; номер берётся
    #     из записи реестра (первое появление группы в документе), не из формы;
    #   * иначе — по (entity_type, original_text), как раньше (ADDRESS, regex-типы).
    # Пространства ключей разделены префиксами "G:"/"T:" — group_key не может
    # случайно совпасть с original_text другой сущности. Половины разорванной
    # границей B3-сущности group_key не имеют — каждая берёт свой токен со своим
    # значением (восстановление посимвольно точно, см. Вариант А).
    token_map: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}

    def _new_token(entity_type: str) -> str:
        prefix = token_prefixes[entity_type]
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"[{prefix}_{counters[prefix]}]"

    for e in kept:
        if e.group_key is not None:
            key = (e.entity_type, "G:" + e.group_key)
        else:
            key = (e.entity_type, "T:" + e.original_text)
        token = token_map.get(key)
        if token is None:
            token = _new_token(e.entity_type)
            token_map[key] = token
        e.token = token

    # --- Сборка анонимизированного текста ---
    kept_by_segment: dict[str, list[Entity]] = {}
    for e in kept:
        kept_by_segment.setdefault(e.segment_id, []).append(e)

    anonymized = _assemble(
        doc,
        lambda seg: _render_segment(seg, kept_by_segment.get(seg.id, [])),
    )

    return anonymized, kept


def tokenize(
    doc: SourceDocument,
    entities: list[Entity],
    config_path: str,
    enabled_types=None,
    suppression_log: list | None = None,
) -> tuple[str, list[Entity]]:
    """Разрешение пересечений + простановка масок (публичный контракт блока 4).

    `enabled_types=None` (умолчание) — набор «Максимум»: поведение побайтно то
    же, что до этапа T1. Замер и гейт зовут ИМЕННО так, всегда: иначе они мерили
    бы настройку пользователя, а не работу программы, и падение полноты стало бы
    неотличимо от выключенного типа.

    `suppression_log` (ЭТАП T4) прокидывается в `resolve_for_masking` — см. там.
    """
    kept = resolve_for_masking(doc, entities, config_path, suppression_log=suppression_log)
    return apply_masking(doc, kept, config_path, enabled_types)
