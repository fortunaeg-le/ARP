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
import uuid

import yaml

from models import Entity, SourceDocument, TextSegment

# B3-fix: сколько символов хвоста A / головы B берём в граничное окно детекции.
# 60 с запасом перекрывает самую длинную сущность (счёт из 20 цифр с пробелами-
# разделителями, адрес) — торчащий за окно фрагмент реального смысла не несёт.
_BOUNDARY_WINDOW = 60

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


def _boundary_sep(seg_a: TextSegment, seg_b: TextSegment) -> str:
    """Разделитель, который _assemble/build_plain_text вставил бы между двумя
    ИДУЩИМИ ПОДРЯД сегментами — но применительно к ПОСТРОЕНИЮ ГРАНИЧНОГО ОКНА.

    build_plain_text склеивает верхнеуровневые единицы через '\\n', а ячейки одной
    строки таблицы — через ' | ' (для читаемости итогового текста). Для окна
    детекции ' | ' НЕ подходит: этот литерал физически рвёт паттерны (телефон/ИНН
    через ' | ' не матчатся), а сущность, порванная границей соседних ячеек, обязана
    реконструироваться. Поэтому две соседние ячейки одной строки одной таблицы
    склеиваются в окне БЕЗ разделителя (''); во всех остальных случаях граница
    соответствует '\\n' финальной сборки. См. docs/SHIFRATOR_SPEC_AI.md (блок 4,
    «Разделитель граничного окна») / docs/handoffs/HANDOFF_4.md.
    """
    if (
        seg_a.source_type == "docx_table_cell"
        and seg_b.source_type == "docx_table_cell"
        and seg_a.metadata["table_index"] == seg_b.metadata["table_index"]
        and seg_a.metadata["row_index"] == seg_b.metadata["row_index"]
    ):
        return ""
    return "\n"


def _detect_boundary_entities(
    doc: SourceDocument,
    config_path: str,
) -> list[Entity]:
    """B3-fix: находит сущности, РАЗОРВАННЫЕ границей соседних сегментов.

    detect_regex/detect_ner работают посегментно, поэтому значение, чей текст
    разложен на хвост сегмента A и голову сегмента B (перенос абзаца/строки или
    соседние ячейки таблицы), не находит ни один из них и утекает в открытом виде.

    Здесь для каждой пары соседних (по индексу в doc.segments) сегментов строится
    окно `хвост A + разделитель + голова B` и по нему повторно гоняются те же
    публичные детекторы блоков 2/3. Оставляются ТОЛЬКО найденные в окне сущности,
    чей интервал реально пересекает точку стыка (иначе это дубликат обычной
    посегментной детекции). Пересекающая сущность делится на пару Entity_A/Entity_B
    с реальными оффсетами внутри своих сегментов; половины идут дальше как две
    ОБЫЧНЫЕ независимые сущности (у каждой свой original_text — свой токен), чтобы
    восстановление в каждом сегменте было посимвольно точным.

    Возвращает: список добавочных Entity.
    detect_regex/detect_ner импортируются ЛОКАЛЬНО, чтобы прямой импорт tokenizer
    (напр. в юнит-тестах блока 4) не тянул natasha, пока окна не строятся реально.
    """
    from regex_detector import detect_regex
    from ner_detector import detect_ner

    extra: list[Entity] = []

    segs = doc.segments
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
        tail_end = len(tail)                 # позиция конца хвоста A в окне
        head_start = tail_end + len(sep)     # позиция начала головы B в окне

        win_seg = TextSegment(
            id=f"boundary_{seg_a.id}_{seg_b.id}",
            text=window,
            source_type="txt_line",  # детекторы на source_type не смотрят
            metadata={},
        )
        win_doc = SourceDocument(
            segments=[win_seg],
            source_format=doc.source_format,
            source_path=doc.source_path,
        )

        win_entities = detect_regex(win_doc, config_path) + detect_ner(win_doc, config_path)
        for win_e in win_entities:
            # оставляем только то, что реально перекрывает стык хвоста A и головы B;
            # всё, что целиком в хвосте A либо целиком в голове B — дубликат обычной
            # посегментной детекции, не добавляем.
            if not (win_e.start < tail_end and win_e.end > head_start):
                continue

            # оффсеты половин пересчитываем из позиции в окне обратно в оригинальные
            # сегменты: A-часть тянется до конца text_a, B-часть — от начала text_b.
            start_a = tail_off + win_e.start
            end_a = len(text_a)
            end_b = win_e.end - head_start

            ent_a = Entity(
                id=str(uuid.uuid4()),
                segment_id=seg_a.id,
                start=start_a,
                end=end_a,
                original_text=text_a[start_a:end_a],
                entity_type=win_e.entity_type,
                detector=win_e.detector,
                confidence=win_e.confidence,
            )
            ent_b = Entity(
                id=str(uuid.uuid4()),
                segment_id=seg_b.id,
                start=0,
                end=end_b,
                original_text=text_b[0:end_b],
                entity_type=win_e.entity_type,
                detector=win_e.detector,
                confidence=win_e.confidence,
            )
            extra.append(ent_a)
            extra.append(ent_b)

    return extra


def tokenize(
    doc: SourceDocument,
    entities: list[Entity],
    config_path: str,
) -> tuple[str, list[Entity]]:
    token_prefixes = _load_token_prefixes(config_path)
    seg_index = {seg.id: idx for idx, seg in enumerate(doc.segments)}

    # --- B3-fix: сущности, разорванные границей соседних сегментов ---
    # Добавляются к обычным ДО разрешения пересечений и проходят через тот же
    # алгоритм. Каждая половина пары — обычная независимая сущность со своим
    # original_text (и, значит, своим токеном): так восстановление в каждом
    # сегменте посимвольно точно (см. Вариант А в отчёте о фиксе порчи B3).
    boundary_entities = _detect_boundary_entities(doc, config_path)
    entities = list(entities) + boundary_entities

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

    # --- Присвоение токенов ---
    # Единое правило: переиспользование по (entity_type, original_text). Половины
    # разорванной границей сущности НЕ получают общий токен — у них разный
    # original_text, поэтому каждая берёт свой токен со своим значением. Это делает
    # восстановление посимвольно точным в обоих сегментах и превращает ложное
    # срабатывание B3 из разрушительного (порча данных) в терпимо-шумное.
    token_map: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}

    def _new_token(entity_type: str) -> str:
        prefix = token_prefixes[entity_type]
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"[{prefix}_{counters[prefix]}]"

    for e in kept:
        key = (e.entity_type, e.original_text)
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
