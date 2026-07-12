"""Блок 3 — NER-детектор.

Находит PERSON/ORG через стандартный NER-пайплайн Natasha и почтовые адреса
(ADDRESS) через natasha.AddrExtractor. Публичная функция — detect_ner.

Модели Natasha инициализируются один раз при импорте модуля (это секунды и
сотни МБ памяти — см. HANDOFF_3, раздел "Побочные эффекты импорта").
"""

import re
import sys
import uuid

import yaml

from models import Entity, SourceDocument
from natasha import (
    Segmenter,
    MorphVocab,
    NewsEmbedding,
    NewsNERTagger,
    AddrExtractor,
    Doc,
)

# --- Инициализация моделей один раз при старте модуля ---
_segmenter = Segmenter()
_morph_vocab = MorphVocab()
_emb = NewsEmbedding()
_ner_tagger = NewsNERTagger(_emb)
_addr_extractor = AddrExtractor(_morph_vocab)

# Расширение PERSON-спана инициалами вида "И.И." справа/слева от спана.
# Паттерны из ТЗ применяются к срезам text[end:] / text[:start], поэтому якоря
# ^ и $ работают на границе среза (а не на реальном начале/конце сегмента).
_INITIALS_RIGHT = re.compile(r"^[  ]?[А-ЯЁ]\.[  ]?[А-ЯЁ]\.")
_INITIALS_LEFT = re.compile(r"[А-ЯЁ]\.[  ]?[А-ЯЁ]\.[  ]?$")

# Символы, из которых (и только из которых) может состоять разрыв между
# двумя соседними Match'ами AddrExtractor, чтобы их склеить в один Entity.
_ADDR_GAP_CHARS = frozenset(" ,. ")
_ADDR_GAP_MAXLEN = 6


def _load_ner_config(config_path: str) -> tuple[dict, list[str]]:
    """Читает entity_types.yaml и возвращает:
      - ner_label_map: {метка Natasha -> entity_type} для записей method: ner с ner_label
        (напр. {"PER": "PERSON", "ORG": "ORG"});
      - addr_types: список entity_type для записей method: ner с ner_extractor: addr
        (обычно ["ADDRESS"]).
    Записи с enabled: false пропускаются. Файл обязан существовать — иначе FileNotFoundError.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    ner_label_map: dict[str, str] = {}
    addr_types: list[str] = []

    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or spec.get("method") != "ner":
            continue
        if spec.get("enabled", True) is False:
            continue

        ner_label = spec.get("ner_label")
        ner_extractor = spec.get("ner_extractor")

        if ner_extractor == "addr":
            addr_types.append(entity_type)
        elif ner_label is not None:
            ner_label_map[ner_label] = entity_type
        else:
            print(
                f"ПРЕДУПРЕЖДЕНИЕ: тип {entity_type} (method: ner) без ner_label и без "
                f"ner_extractor: addr — пропущен",
                file=sys.stderr,
            )

    return ner_label_map, addr_types


def _expand_person_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Расширяет спан PERSON инициалами "И.И." в обе стороны независимо.
    Возвращает новые (start, end)."""
    right = _INITIALS_RIGHT.match(text[end:])
    if right is not None:
        end = end + right.end()

    left = _INITIALS_LEFT.search(text[:start])
    if left is not None:
        start = left.start()

    return start, end


def _glue_address_matches(text: str, matches: list) -> list[tuple[int, int]]:
    """Склеивает соседние Match'и AddrExtractor в цепочки-адреса.
    Соседние склеиваются, если разрыв между match_i.stop и match_{i+1}.start
    не длиннее 6 символов и целиком состоит из [ ,.\\u00A0] (без букв).
    Возвращает список (start, end) итоговых спанов."""
    if not matches:
        return []

    ordered = sorted(matches, key=lambda m: (m.start, m.stop))

    spans: list[tuple[int, int]] = []
    cur_start = ordered[0].start
    cur_end = ordered[0].stop

    for m in ordered[1:]:
        gap = text[cur_end:m.start]
        glue = (
            0 <= len(gap) <= _ADDR_GAP_MAXLEN
            and all(ch in _ADDR_GAP_CHARS for ch in gap)
        )
        if glue:
            cur_end = m.stop
        else:
            spans.append((cur_start, cur_end))
            cur_start = m.start
            cur_end = m.stop

    spans.append((cur_start, cur_end))
    return spans


def detect_ner(doc: SourceDocument, config_path: str) -> list[Entity]:
    ner_label_map, addr_types = _load_ner_config(config_path)

    entities: list[Entity] = []

    for segment in doc.segments:
        text = segment.text
        if not text:
            continue

        # --- PERSON / ORG через NER-тэггер Natasha ---
        if ner_label_map:
            nlp_doc = Doc(text)
            nlp_doc.segment(_segmenter)
            nlp_doc.tag_ner(_ner_tagger)
            for span in nlp_doc.spans:
                entity_type = ner_label_map.get(span.type)
                if entity_type is None:
                    continue  # метка Natasha не сопоставлена ни одному типу конфига (напр. LOC)

                start, end = span.start, span.stop
                if span.type == "PER":
                    start, end = _expand_person_span(text, start, end)

                entities.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=start,
                    end=end,
                    original_text=text[start:end],
                    entity_type=entity_type,
                    detector="ner",
                    confidence=1.0,
                ))

        # --- ADDRESS через AddrExtractor ---
        for addr_type in addr_types:
            matches = list(_addr_extractor(text))
            for start, end in _glue_address_matches(text, matches):
                entities.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=start,
                    end=end,
                    original_text=text[start:end],
                    entity_type=addr_type,
                    detector="ner",
                    confidence=1.0,
                ))

    # Инвариант блока 3: original_text строго равен срезу сегмента по [start:end].
    for e in entities:
        seg = next(s for s in doc.segments if s.id == e.segment_id)
        assert e.original_text == seg.text[e.start:e.end], (
            f"Нарушен инвариант original_text для {e.entity_type} в {e.segment_id}: "
            f"{e.original_text!r} != {seg.text[e.start:e.end]!r}"
        )

    return entities
