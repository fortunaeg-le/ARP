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
