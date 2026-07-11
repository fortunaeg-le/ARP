import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

from models import SourceDocument, TextSegment


def _extract_docx(path: str) -> SourceDocument:
    document = Document(path)
    segments: list[TextSegment] = []

    paragraph_counter = 0
    table_counter = 0
    seen_tcs = set()

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            style_name = paragraph.style.name if paragraph.style is not None else None
            segments.append(
                TextSegment(
                    id=f"p{paragraph_counter}",
                    text=paragraph.text,
                    source_type="docx_paragraph",
                    metadata={"paragraph_index": paragraph_counter, "style": style_name},
                )
            )
            paragraph_counter += 1
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            table_idx = table_counter
            for row_idx, row in enumerate(table.rows):
                for col_idx, cell in enumerate(row.cells):
                    if len(cell.tables) > 0:
                        print(
                            f"Предупреждение: вложенная таблица в ячейке "
                            f"t{table_idx}_r{row_idx}_c{col_idx} не извлечена (MVP)",
                            file=sys.stderr,
                        )
                    tc = cell._tc
                    if tc in seen_tcs:
                        cell_text = ""
                    else:
                        seen_tcs.add(tc)
                        cell_text = cell.text
                    segments.append(
                        TextSegment(
                            id=f"t{table_idx}_r{row_idx}_c{col_idx}",
                            text=cell_text,
                            source_type="docx_table_cell",
                            metadata={
                                "table_index": table_idx,
                                "row_index": row_idx,
                                "col_index": col_idx,
                            },
                        )
                    )
            table_counter += 1

    return SourceDocument(segments=segments, source_format="docx", source_path=path)


def _extract_txt(path: str) -> SourceDocument:
    encoding = "utf-8-sig"
    try:
        with open(path, "r", encoding=encoding) as f:
            raw = f.read()
    except UnicodeDecodeError:
        encoding = "cp1251"
        with open(path, "r", encoding=encoding) as f:
            raw = f.read()

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    segments = [
        TextSegment(
            id=f"l{index}",
            text=line,
            source_type="txt_line",
            metadata={"line_index": index, "encoding": encoding},
        )
        for index, line in enumerate(lines)
    ]

    return SourceDocument(segments=segments, source_format="txt", source_path=path)


def extract(path: str) -> SourceDocument:
    if not Path(path).is_file():
        raise FileNotFoundError(path)

    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return _extract_docx(path)
    elif ext == ".txt":
        return _extract_txt(path)
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}. Поддерживаются: .docx, .txt")
