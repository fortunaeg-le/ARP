from dataclasses import dataclass


@dataclass
class TextSegment:
    id: str                 # уникальный id, напр. "p12" (параграф 12), "t0_r1_c2" (таблица 0, строка 1, ячейка 2), "l5" (строка 5 txt)
    text: str
    source_type: str        # "docx_paragraph" | "docx_table_cell" | "txt_line"
    metadata: dict           # для параграфа: {"paragraph_index": 12, "style": "Heading1"}
                              # для ячейки таблицы (обязательно все три ключа): {"table_index": 0, "row_index": 1, "col_index": 2}


@dataclass
class SourceDocument:
    segments: list[TextSegment]
    source_format: str       # "docx" | "txt"  ("pdf" зарезервировано для фазы 2, блок 1 его не выдаёт)
    source_path: str


@dataclass
class Entity:
    id: str                  # uuid4
    segment_id: str          # ссылка на TextSegment.id
    start: int                # смещение символа начала внутри segment.text
    end: int                  # смещение символа конца (не включительно)
    original_text: str
    entity_type: str          # ключ из entity_types.yaml, напр. "PERSON", "INN", "ORG"
    detector: str              # "regex" | "ner"
    confidence: float          # 0.0-1.0; для regex всегда 1.0. Natasha не предоставляет confidence для NER-спанов — для всех entity из блока 3 записывать 1.0. Поле оставлено в формате на будущее (другие NER-бэкенды).
    token: str | None = None   # заполняется в блоке 4, до этого None; дефолт обязателен — блоки 2 и 3 создают Entity без этого поля

    # --- ЭТАП E (мультиспан + единый id + канон). Все три поля опциональны: ---
    # spans — список диапазонов ЗНАЧЕНИЯ в исходных координатах segment.text,
    #   отсортированных и непересекающихся; [start, end) — ОБОЛОЧКА (hull) от начала
    #   первого до конца последнего куска, разделители между кусками входят в hull и
    #   в original_text (= segment.text[start:end]), но НЕ в spans. None — сущность
    #   однодиапазонная (частный случай: spans == [(start, end)]); детекторы, не
    #   знающие о мультиспане, ничего не заполняют и ничего не ломают.
    #   Маскируется ВСЕГДА hull одним токеном (структурная целостность восстановления
    #   важнее гранулярности: original_text хранит разделители байт-в-байт).
    spans: list[tuple[int, int]] | None = None
    # group_key — ключ кореференции из реестра документа (этап E, часть 2): все
    #   вхождения ОДНОЙ сущности (падежи «Меркурий»/«Меркурия»/«Меркурием», формы
    #   «Новикова О. Е.»/«Новиковой Ольги Егоровны») несут один group_key и получают
    #   ОДИН токен. None — нумерация по (entity_type, original_text), как раньше
    #   (ADDRESS и regex-типы: реестра кореференции нет).
    group_key: str | None = None
    # canonical — каноническая форма сущности из реестра (ORG — юридическая форма
    #   «ООО «Меркурий»», PER — ФИО в именительном). Используется восстановлением
    #   (детокенизация подставляет канон, см. detokenizer). None — канон не известен,
    #   восстановление подставляет original_text (regex-типы, ADDRESS).
    canonical: str | None = None
    # --- ЭТАП DEFAULT-GATE (решение владельца по MFIX-PROFILE-IP-PER) ---
    # shadowed — кандидаты, которых ЭТА сущность вытеснила при разрешении
    #   пересечений (см. tokenizer._resolve_overlaps). Нужны ровно для одного
    #   случая: победитель принадлежит типу, ВЫКЛЮЧЕННОМУ набором пользователя,
    #   и потому маски не получает — тогда территория остаётся открытой, а
    #   вытесненный кандидат ВКЛЮЧЁННОГО типа обязан её закрыть. Имя человека
    #   внутри названия ИП — персональные данные независимо от того, оформлено
    #   ли юрлицо (решение владельца). Поле служебное, живёт только внутри
    #   маскирования: в сессию оно не пишется (session_store сериализует
    #   перечисленные поля явно), в набор «Максимум» не вмешивается вовсе —
    #   там выключенных типов нет и восстанавливать нечего.
    shadowed: list["Entity"] | None = None


def entity_spans(e: Entity) -> list[tuple[int, int]]:
    """Диапазоны значения сущности: spans, если заданы, иначе [(start, end)].
    Единая точка чтения — потребители не обязаны знать про None-конвенцию."""
    return list(e.spans) if e.spans else [(e.start, e.end)]
