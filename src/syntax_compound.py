"""Волна 2, этап B — синтаксический проход для составных сущностей.

Отдельный, ЯВНО ПОИМЕНОВАННЫЙ проход ПОСЛЕ основной детекции. Чинит разрушение
СМЫСЛОВОЙ ЦЕЛОСТНОСТИ: NER-тэггер расщепляет «ИП Пирогова А.С.» на два НЕСВЯЗАННЫХ
токена — ORG='ИП' и PERSON='Пирогова А.С.'. LLM, увидев только [ORG], после расшифровки
выдаёт бессмысленное «Компания ИП». Здесь такие пары объединяются в ОДНУ составную
сущность (один ORG-токен, один original_text — конкатенация с сохранением исходного
текста между частями).

Механизм — синтаксическая связь appos (приложение) из NewsSyntaxParser: если токен
внутри PER-спана связан ребром appos с токеном внутри соседнего ORG-спана, это одна
сущность-сторона. Ребро conj/parataxis (сочинение/разные стороны) склейку НЕ даёт —
поэтому «Стороны: ИП Иванов И.И. и ООО «Ромашка»» не сливается в один блоб (см.
негативные тесты). Типизация итога: для «ИП + ФИО» — единый ORG (роль в договоре —
сторона/контрагент). Для «роль + ФИО» (директор/глава + ФИО) склейка в ORG НЕ делается:
человек — представитель, а не организация; см. раздел типизации в
docs/reports/DETECTION_REBUILD.md.

ОТЛИЧИЕ ОТ B3 (важно, не путать): B3 сшивает ОДНУ И ТУ ЖЕ сущность, разорванную
ГРАНИЦЕЙ СЕГМЕНТА (форматированием). Здесь — ДВЕ РАЗНОТИПНЫЕ сущности ВНУТРИ ОДНОГО
сегмента, семантически составляющие одно целое. Это НЕ частный случай B3.

ВЫБОРОЧНОСТЬ (синтаксис стоит +17 мс/строка, см. RECON_REPORT): парсер запускается
ТОЛЬКО на сегментах, где основная детекция уже дала ORG рядом с PER (или regex нашёл
ИП-форму прописью рядом с PER). Это не «список что искать», а «где детекторы уже что-то
нашли».

Публичная функция — merge_compound_entities(doc, entities).
Модели Natasha (морфология+синтаксис) инициализируются один раз при импорте.
"""

import re
import uuid

from models import Entity, SourceDocument
from natasha import (
    Segmenter,
    NewsEmbedding,
    NewsMorphTagger,
    NewsSyntaxParser,
    Doc,
)

_segmenter = Segmenter()
_emb = NewsEmbedding()
_morph_tagger = NewsMorphTagger(_emb)
_syntax_parser = NewsSyntaxParser(_emb)

# Максимальный разрыв (в символах) между ORG и PER, при котором пара считается
# СОСЕДНЕЙ и потому кандидатом на составную сущность. «ИП Пирогова» — разрыв 1 (пробел),
# «ИП, Пирогова» — 2. Больше — это уже не «ИП <ФИО>», а разные члены предложения.
_COMPOUND_MAX_GAP = 3

# ИП-форма ПРОПИСЬЮ («Индивидуальный предприниматель Сидоров И.И.») не даёт ORG-токена
# (голова аппозиции — обычное существительное «предприниматель»), поэтому appos-склейке
# не за что зацепиться. Regex создаёт ORG-КАНДИДАТА на саму форму, после чего синтаксис
# доделывает склейку с ФИО. Кандидат живёт ТОЛЬКО ради склейки: если рядом нет ФИО и
# склейка не состоялась — он отбрасывается (роль-фраза сама по себе не ПДн, её не токенизируем).
# Только ИП-эквиваленты (не «директор»/«глава» — те роли, а не организации).
_SPELLED_ORG_RE = re.compile(r"(?i)\bиндивидуальн\w*\s+предпринимател\w*")

# Рёбра синтаксиса, означающие «одна сущность-сторона» (склеиваем).
_APPOS_RELS = frozenset({"appos"})


def _token_overlaps(tok, start: int, end: int) -> bool:
    return tok.start < end and tok.stop > start


def _appos_links(tokens, a_span: tuple[int, int], b_span: tuple[int, int]) -> bool:
    """True, если есть ребро appos, напрямую связывающее токен из a_span с токеном
    из b_span (в любую сторону). id токенов Natasha — строки вида '1_2'."""
    by_id = {t.id: t for t in tokens}
    a0, a1 = a_span
    b0, b1 = b_span
    for t in tokens:
        if t.rel not in _APPOS_RELS:
            continue
        head = by_id.get(t.head_id)
        if head is None:
            continue
        # t в одном спане, его голова — в другом (в любом порядке)
        if (_token_overlaps(t, b0, b1) and _token_overlaps(head, a0, a1)) or (
            _token_overlaps(t, a0, a1) and _token_overlaps(head, b0, b1)
        ):
            return True
    return False


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Разрыв между двумя непересекающимися интервалами (0 при касании/пересечении)."""
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))


def merge_compound_entities(doc: SourceDocument, entities: list[Entity]) -> list[Entity]:
    """Склеивает соседние разнотипные сущности «ORG + ФИО», связанные appos, в одну
    составную ORG-сущность. Возвращает НОВЫЙ список entities (несоставные — как есть).

    Выборочно: синтаксический парсер запускается только на сегментах, где есть ORG
    рядом с PER (или ИП-форма прописью рядом с PER)."""
    by_seg: dict[str, list[Entity]] = {}
    for e in entities:
        by_seg.setdefault(e.segment_id, []).append(e)

    seg_by_id = {s.id: s for s in doc.segments}
    result: list[Entity] = []

    for seg_id, ents in by_seg.items():
        seg = seg_by_id.get(seg_id)
        orgs = [e for e in ents if e.entity_type == "ORG"]
        pers = [e for e in ents if e.entity_type == "PERSON"]

        # Дешёвый гейт: без пары ORG↔PER синтаксис не нужен вовсе.
        if seg is None or not pers or (not orgs and not _SPELLED_ORG_RE.search(
            seg.metadata.get("detection_text", seg.text)
        )):
            result.extend(ents)
            continue

        text = seg.metadata.get("detection_text", seg.text)

        # ORG-кандидаты = реальные ORG-сущности + синтетические ИП-формы прописью
        # (последние — только если не покрыты уже реальной ORG).
        org_spans: list[tuple[int, int, Entity | None]] = [
            (o.start, o.end, o) for o in orgs
        ]
        for m in _SPELLED_ORG_RE.finditer(text):
            if not any(o.start <= m.start() and m.end() <= o.end() for o in orgs):
                org_spans.append((m.start(), m.end(), None))

        # Пары ORG↔PER в пределах разрыва _COMPOUND_MAX_GAP.
        candidate_pairs = []
        for (os_, oe, oent) in org_spans:
            for p in pers:
                if _gap((os_, oe), (p.start, p.end)) <= _COMPOUND_MAX_GAP:
                    candidate_pairs.append((os_, oe, oent, p))

        if not candidate_pairs:
            result.extend(ents)
            continue

        # Выборочный запуск синтаксиса — один раз на сегмент.
        nlp = Doc(text)
        nlp.segment(_segmenter)
        nlp.tag_morph(_morph_tagger)
        nlp.parse_syntax(_syntax_parser)
        tokens = nlp.tokens

        consumed: set[int] = set()   # id() поглощённых реальных сущностей
        merged: list[Entity] = []
        for (os_, oe, oent, p) in candidate_pairs:
            if oent is not None and id(oent) in consumed:
                continue
            if id(p) in consumed:
                continue
            if not _appos_links(tokens, (os_, oe), (p.start, p.end)):
                continue

            start = min(os_, p.start)
            end = max(oe, p.end)
            merged.append(Entity(
                id=str(uuid.uuid4()),
                segment_id=seg_id,
                start=start,
                end=end,
                original_text=seg.text[start:end],
                entity_type="ORG",          # ИП + ФИО -> единый ORG (сторона договора)
                detector="ner",
                confidence=1.0,
            ))
            if oent is not None:
                consumed.add(id(oent))
            consumed.add(id(p))

        # Оставляем непоглощённые сущности + добавляем составные.
        for e in ents:
            if id(e) not in consumed:
                result.append(e)
        result.extend(merged)

    return result
