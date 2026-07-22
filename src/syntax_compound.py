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

# ИП-форма — прописью («Индивидуальный предприниматель Сидоров И.И.») ИЛИ
# аббревиатурой («ИП Сидоров И.И.») — не даёт ORG-токена сама по себе (голова
# аппозиции — обычное существительное «предприниматель», а «ИП» до этапа A ловила
# ORG-детекция Natasha). Regex создаёт ORG-КАНДИДАТА на саму форму, после чего
# синтаксис доделывает склейку с ФИО. Кандидат живёт ТОЛЬКО ради склейки: если рядом
# нет ФИО и склейка не состоялась — он отбрасывается (роль-фраза сама по себе не ПДн).
# Только ИП-эквиваленты (не «директор»/«глава» — те роли, а не организации).
#
# ЭТАП A: аббревиатура «ИП» ДОБАВЛЕНА в этот regex. Раньше кандидата на «ИП» давала
# ORG-детекция Natasha (span.type == "ORG"), и склейка «ИП + ФИО» опиралась на неё.
# После отключения Natasha-ORG (structure-first ORG, см. anchor_registry) этот путь
# исчез бы, и «ИП Сидоров И.И.» перестал бы склеиваться. Делаем компонент самодоста-
# точным по абброс-форме — так существующее поведение склейки сохранено без Natasha-ORG.
_SPELLED_ORG_RE = re.compile(
    r"(?i)(?:\bиндивидуальн\w*\s+предпринимател\w*|(?<![а-яёa-z])ип(?![а-яёa-z]))"
)

# Рёбра синтаксиса, означающие «одна сущность-сторона» (склеиваем).
_APPOS_RELS = frozenset({"appos"})


def _token_overlaps(tok, start: int, end: int) -> bool:
    return tok.start < end and tok.stop > start


def _appos_links(tokens, org_span: tuple[int, int], per_span: tuple[int, int]) -> bool:
    """True, если PER — аппозитивное ПРИЛОЖЕНИЕ к ORG: есть ребро appos, чей ЗАВИСИМЫЙ
    токен лежит в PER-спане, а ГОЛОВА — в ORG-спане. НАПРАВЛЕНИЕ существенно.

    Эмпирика (docs/reports/WAVE2_VERIFICATION.md, разбор в голой строке ячейки):
      • «ИП Пирогова А.С.»  → root=«ИП»(ORG), «Пирогова»(PER) --appos--> «ИП».
        Зависимый=PER, голова=ORG. Организационная форма — вершина, ФИО уточняет,
        КАКОЙ именно ИП. ОДНО лицо (ИП и есть Пирогова) → склеиваем.
      • «Свидетель Иванов И.И. ООО «Ромашка»» → «Иванов»(PER) --appos--> «Свидетель»,
        «ООО»(ORG) --appos--> «Иванов»(PER). Зависимый=ORG, голова=PER.
        Организация приложена К человеку — это РАЗНЫЕ стороны → НЕ склеиваем.
    Прежняя версия принимала appos в ЛЮБУЮ сторону и потому ложно сливала второй случай
    (W2-D3). Критерий синтаксический (направление ребра), НЕ список ролевых слов.
    id токенов Natasha — строки вида '1_2'."""
    by_id = {t.id: t for t in tokens}
    o0, o1 = org_span
    p0, p1 = per_span
    for t in tokens:
        if t.rel not in _APPOS_RELS:
            continue
        head = by_id.get(t.head_id)
        if head is None:
            continue
        # зависимый (t) — в PER-спане, его голова — в ORG-спане: ORG есть вершина.
        if _token_overlaps(t, p0, p1) and _token_overlaps(head, o0, o1):
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
            if not any(o.start <= m.start() and m.end() <= o.end for o in orgs):
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
            linked = _appos_links(tokens, (os_, oe), (p.start, p.end))
            if not linked and oent is None and _gap((os_, oe), (p.start, p.end)) <= 1:
                # A-2: синтетическая ИП-форма (_SPELLED_ORG_RE) ВПЛОТНУЮ перед ФИО —
                # структурная единица «ИП + ФИО». В сочинительной конструкции
                # («Стороны: ИП Иванов и ООО «Ромашка»») парсер цепляет ФИО appos'ом к
                # КОРНЮ предложения, не к «ИП», поэтому appos-проверка не срабатывает.
                # Непосредственное примыкание аббревиатуры-формы к имени однозначно
                # (это НЕ ролевое «Свидетель Иванов …»: там ORG — реальная сущность,
                # oent is not None, и для неё по-прежнему требуется appos-направление).
                linked = True
            if not linked:
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
