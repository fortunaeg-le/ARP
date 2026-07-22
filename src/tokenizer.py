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
# SNILS выше KPP: 9 схлопнутых цифр СНИЛС — ровно форма КПП, и при РАВНОЙ длине спана
# (вырожденный случай) маска обязана быть своего типа, а не чужого (C-корректность,
# нарушение «SNILS под KPP»). В обычном случае СНИЛС и так длиннее (9+2 цифры).
# BIRTHDATE выше DATE по той же причине (DATE сейчас enabled:false, но порядок фиксируем).
_REGEX_PRIORITY = [
    "BANK_ACCOUNT", "OGRN", "INN", "BIK", "SNILS", "KPP",
    "PASSPORT", "PHONE", "SUM", "EMAIL", "BIRTHDATE", "DATE",
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


def _resolve_overlaps(entities: list[Entity]) -> list[Entity]:
    """Разрешение пересечений внутри одного сегмента (полный алгоритм из ТЗ).

    Пока среди ещё не зафиксированных есть пересекающаяся пара: выбрать глобально
    сильнейшего, зафиксировать его, а всех, кто пересекается ИМЕННО с ним, ОБРЕЗАТЬ
    до непересекающейся части (не выбрасывать целиком — иначе адрес, поглотивший
    хвост regex-реквизита, утёк бы открытым текстом). Обрезки возвращаются в очередь
    и участвуют в разрешении дальше. Не пересекающиеся с победителем — не трогаем.
    """
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
                new_pending.extend(_trim_to_free(e, winner))
            else:
                new_pending.append(e)
        pending = new_pending

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


# B3-fix + этап C волны 2: разделитель между граничными окнами в СБАТЧЕННОМ блобе.
# Все окна склеиваются в один текст и детекторы гоняются ОДИН раз (а не 6000). Разделитель
# обязан быть таким, через который НИЧТО не «сшивается»: перенос строки рвёт [ ]-классовые
# реквизиты/SUM и email (в них нет \n), а невидимый U+2063 добивает границы слов и не
# встречается в нормальном тексте. Natasha по \n режет предложения — NER-спаны через стык
# окон не тянутся. Проверено в разведке (recon_h3e): 0 сущностей пересекает стык.
# ВНИМАНИЕ: этот разделитель — про БЛОБ детекции, он НЕ имеет отношения к разделителю
# _boundary_sep внутри самого окна (тот — осознанная семантика соседних ячеек, не трогать).
_BATCH_SEP = "\n⁣⁣⁣\n"


def _detect_boundary_entities(
    doc: SourceDocument,
    config_path: str,
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
    """
    import bisect

    from regex_detector import detect_regex
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
            extra.append(Entity(
                id=str(uuid.uuid4()), segment_id=w["seg_a"].id,
                start=start_a, end=end_a, original_text=text_a[start_a:end_a],
                entity_type=entity_type, detector=detector, confidence=confidence,
            ))
            extra.append(Entity(
                id=str(uuid.uuid4()), segment_id=w["seg_b"].id,
                start=0, end=end_b, original_text=text_b[0:end_b],
                entity_type=entity_type, detector=detector, confidence=confidence,
            ))

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
