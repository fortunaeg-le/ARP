"""Этап 2 — охота за критическими ошибками использования блока 1 (extractor.py)
на реальных "грязных" входных документах. НЕ дублирует test_extractor.py этапа 1
(корректные .docx/.txt, FileNotFoundError, неподдерживаемый формат).

Фокус: файлы, которые пользователь реально приносит — переименованный .txt→.docx,
обрезанный/повреждённый .docx, пустой файл, .txt в UTF-16 (сохранён блокнотом как
"Юникод"). Именно на них проявляются падения и — что опаснее — тихая порча.
"""
import tempfile
from pathlib import Path

import pytest

from extractor import extract


# --------------------------------------------------------------------------- #
# F3 — КРИТИЧНО: .docx, который на самом деле не zip-контейнер, роняет extract
#      исключением из недр python-docx, а не чистой ошибкой формата
# --------------------------------------------------------------------------- #
class TestFakeOrCorruptDocx:
    """Реальные сценарии: (1) пользователь переименовал report.txt в report.docx;
    (2) файл скачался наполовину/битый; (3) пустой файл с расширением .docx.
    Во всех случаях `Document(path)` кидает docx.opc.exceptions.PackageNotFoundError.
    extract его не оборачивает, а CLI `encrypt` ловит только FileNotFoundError/ValueError
    => команда падает с сырым трейсбеком python-docx (см. test_adversarial_cli.py, F3b).
    Ожидаемо: extract должен отдавать ValueError с понятным сообщением о битом формате.
    """

    def _write(self, tmp_path, name, data: bytes):
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    def test_text_file_renamed_to_docx_raises_value_error(self, tmp_path):
        path = self._write(tmp_path, "report.docx", "Это обычный текст, а не docx".encode("utf-8"))
        with pytest.raises(ValueError):
            extract(path)

    def test_truncated_docx_raises_value_error(self, tmp_path):
        # Валидный zip-заголовок, но обрезанный контейнер.
        path = self._write(tmp_path, "broken.docx", b"PK\x03\x04truncated-garbage")
        with pytest.raises(ValueError):
            extract(path)

    def test_empty_docx_raises_value_error(self, tmp_path):
        path = self._write(tmp_path, "empty.docx", b"")
        with pytest.raises(ValueError):
            extract(path)


# --------------------------------------------------------------------------- #
# F4 — КРИТИЧНО (тихая порча + утечка): .txt в UTF-16 читается как cp1251
#      => моджибейк, ПДн не детектируются и утекают в искажённом виде
# --------------------------------------------------------------------------- #
class TestUtf16TxtSilentCorruption:
    """Реальный сценарий: пользователь сохранил документ Блокнотом Windows в кодировке
    "Юникод" (это UTF-16 LE с BOM) — очень частый случай на Windows. extract пробует
    utf-8-sig, ловит UnicodeDecodeError и МОЛЧА откатывается на cp1251, декодируя
    UTF-16-байты в мусор. Никакой ошибки не возникает: текст превращается в моджибейк,
    ИНН/телефоны/ФИО не находятся детекторами и проходят СКВОЗЬ анонимизацию в
    искажённом, но потенциально восстановимом виде. Это одновременно тихая порча
    данных и утечка конфиденциальной информации.
    """

    def test_utf16_txt_preserves_readable_text(self, tmp_path):
        p = tmp_path / "notepad_unicode.txt"
        # UTF-16 LE с BOM — именно так сохраняет "Юникод" из блокнота Windows.
        p.write_text("ИНН 7707083893, телефон +7 495 123-45-67", encoding="utf-16")

        doc = extract(str(p))
        joined = "\n".join(s.text for s in doc.segments)
        # Ожидаемо: осмысленный текст с распознаваемым ИНН. Фактически — моджибейк без него.
        assert "7707083893" in joined, (
            "UTF-16 .txt прочитан как cp1251: ИНН превратился в мусор, детекторы его не "
            "увидят и он не будет анонимизирован."
        )
