"""Блок 4 — Токенизатор.

Принимает объединённый список Entity из блоков 2 (regex) и 3 (ner), разрешает
пересечения внутри каждого сегмента, присваивает переиспользуемые токены вида
[TYPE_N] (TYPE = token_prefix из entity_types.yaml, нумерация сквозная по префиксу)
и собирает анонимизированный текст всего документа.

Публичные функции:
  - tokenize(doc, entities, config_path) -> (анонимизированный текст, list[Entity] с token)
  - build_plain_text(doc) -> str  — та же сборка текста, но без подстановки токенов
    (эталон для сквозной приёмки блока 7).

dataclass'ы импортируются из models.py, не переопределены.
"""

import sys

import yaml

from models import Entity, SourceDocument, TextSegment

# Приоритет типов среди двух regex одинаковой длины (раньше в списке = сильнее).
# Типы вне списка слабее всех перечисленных; между собой — по алфавиту entity_type.
_REGEX_PRIORITY = [
    "BANK_ACCOUNT", "OGRN", "INN", "BIK", "KPP",
    "PASSPORT", "PHONE", "SUM", "EMAIL", "DATE",
]
# Приоритет типов среди двух ner при равной длине/confidence и неидентичных интервалах.
_NER_PRIORITY = ["ADDRESS", "ORG", "PERSON"]


def _regex_rank(entity_type: str) -> tuple[int, str]:
    """Меньший кортеж = сильнее. Перечисленные типы: (0, индекс); прочие: (1, entity_type)."""
    if entity_type in _REGEX_PRIORITY:
        return (0, str(_REGEX_PRIORITY.index(entity_type)))
    return (1, entity_type)


def _ner_rank(entity_type: str) -> tuple[int, str]:
    """Меньший кортеж = сильнее (для ner-tie-break по типу)."""
    if entity_type in _NER_PRIORITY:
        return (0, str(_NER_PRIORITY.index(entity_type)))
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
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            if _overlaps(entities[i], entities[j]):
                return True
    return False


def _resolve_overlaps(entities: list[Entity]) -> list[Entity]:
    """Разрешение пересечений внутри одного сегмента (полный алгоритм из ТЗ).

    Пока среди ещё не зафиксированных есть пересекающаяся пара: выбрать глобально
    сильнейшего, зафиксировать его, удалить всех, кто пересекается ИМЕННО с ним
    (не трогая тех, кто с ним не пересекается). Оставшиеся без пересечений проходят
    без изменений.
    """
    pending = list(entities)
    kept: list[Entity] = []

    while _has_overlapping_pair(pending):
        # глобально сильнейший среди всех ещё не зафиксированных
        winner = pending[0]
        for e in pending[1:]:
            winner = _winner(winner, e)
        kept.append(winner)
        # удалить всех, кто пересекается с победителем, и самого победителя (зафиксирован)
        pending = [e for e in pending if e is not winner and not _overlaps(e, winner)]

    kept.extend(pending)  # сущности без единого пересечения проходят как есть
    return kept


def _load_token_prefixes(config_path: str) -> dict[str, str]:
    """entity_type -> token_prefix из entity_types.yaml. Файл обязан существовать."""
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

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


def tokenize(
    doc: SourceDocument,
    entities: list[Entity],
    config_path: str,
) -> tuple[str, list[Entity]]:
    token_prefixes = _load_token_prefixes(config_path)
    seg_index = {seg.id: idx for idx, seg in enumerate(doc.segments)}

    # --- Разрешение пересечений внутри каждого сегмента ---
    by_segment: dict[str, list[Entity]] = {}
    for e in entities:
        by_segment.setdefault(e.segment_id, []).append(e)

    kept: list[Entity] = []
    for seg in doc.segments:
        group = by_segment.get(seg.id)
        if group:
            kept.extend(_resolve_overlaps(group))

    # --- Порядок появления: (индекс сегмента, start) ---
    kept.sort(key=lambda e: (seg_index[e.segment_id], e.start))

    # --- Присвоение токенов (переиспользование по (entity_type, original_text)) ---
    token_map: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    for e in kept:
        key = (e.entity_type, e.original_text)
        token = token_map.get(key)
        if token is None:
            prefix = token_prefixes[e.entity_type]
            counters[prefix] = counters.get(prefix, 0) + 1
            token = f"[{prefix}_{counters[prefix]}]"
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
