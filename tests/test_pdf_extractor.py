# -*- coding: utf-8 -*-
"""PDF-ARCH — извлечение текста из PDF (extractor.extract) и гарантия
полноты (pdf_completeness.scan_pdf_completeness), плюс сквозная CLI-проверка
через shifrator.py encrypt (ветка А: PDF с текстовым слоем, свой OCR не входит).

Обязательные приёмочные случаи задания:
  * синтетический PDF с текстовым слоем -> session_id и обезличенный текст;
  * PDF со страницей-картинкой -> программа РАБОТАЕТ (не падает, не отказывает),
    предупреждение с номером страницы и причиной в ТОМ ЖЕ выводе;
  * признак неполноты доступен ПРОГРАММНО (sidecar {sid}.unread.json несёт
    булев "incomplete", не только текст предупреждения).

Синтетические PDF строятся низкоуровневыми объектами pypdf (DictionaryObject/
DecodedStreamObject) — библиотека для генерации текстовых страниц (reportlab)
не входит в lock и не нужна: минимального валидного PDF с Tj-оператором
достаточно и для extract(), и для скана полноты.
"""
import os
import subprocess
import sys

import pytest

from testlib_policy import pin_all_types
from testlib_store import read_anon_text, read_unread_sidecar

SHIFRATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shifrator.py"
)


def _run(args, tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               USERPROFILE=str(tmp_path / "home"), HOME=str(tmp_path / "home"))
    pin_all_types(tmp_path / "home")
    return subprocess.run(
        [sys.executable, SHIFRATOR] + args,
        capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=os.path.dirname(SHIFRATOR),
    )


def _sessions_dir(tmp_path):
    return tmp_path / "home" / ".shifrator" / "sessions"


def _text_page_content(text: str) -> bytes:
    return f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")


def _add_text_page(writer, text: str):
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject()
    font.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    resources = DictionaryObject()
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})
    page[NameObject("/Resources")] = resources

    content = DecodedStreamObject()
    content.set_data(_text_page_content(text))
    content_ref = writer._add_object(content)
    page[NameObject("/Contents")] = content_ref
    return page


def _add_image_only_page(writer):
    """Страница БЕЗ текста, но с XObject-картинкой — синтетический «скан»."""
    from pypdf.generic import (
        ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
    )

    page = writer.add_blank_page(width=100, height=100)

    image = DecodedStreamObject()
    # 2x2 RGB чёрный растр — содержимое не важно, важен только факт, что это
    # /Subtype /Image (единственное, на что смотрит _page_has_image_xobject).
    image.set_data(b"\x00" * 12)
    image.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(2),
        NameObject("/Height"): NumberObject(2),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/BitsPerComponent"): NumberObject(8),
    })
    image_ref = writer._add_object(image)

    resources = DictionaryObject()
    resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): image_ref})
    page[NameObject("/Resources")] = resources

    content = DecodedStreamObject()
    content.set_data(b"q 100 0 0 100 0 0 cm /Im0 Do Q")
    content_ref = writer._add_object(content)
    page[NameObject("/Contents")] = content_ref
    return page


def _add_blank_page_no_image(writer):
    """Страница без текста и без картинки — no_text_layer, не image_only."""
    page = writer.add_blank_page(width=100, height=100)
    return page


@pytest.fixture
def pdf_text_layer(tmp_path):
    """PDF с текстовым слоем на всех страницах — приёмочный случай (а)."""
    from pypdf import PdfWriter

    path = tmp_path / "text_layer.pdf"
    writer = PdfWriter()
    _add_text_page(writer, "Dogovor okazaniya uslug INN 7707083893 data 01.01.2024")
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def pdf_with_image_page(tmp_path):
    """Страница 1 — текст, страница 2 — картинка без текста (скан)."""
    from pypdf import PdfWriter

    path = tmp_path / "with_image.pdf"
    writer = PdfWriter()
    _add_text_page(writer, "Contract page one INN 7707083893")
    _add_image_only_page(writer)
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def pdf_blank_page(tmp_path):
    """Страница без текста и без картинки — kind=no_text_layer."""
    from pypdf import PdfWriter

    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    _add_text_page(writer, "Page one has text INN 7707083893")
    _add_blank_page_no_image(writer)
    with open(path, "wb") as f:
        writer.write(f)
    return path


# --------------------------------------------------------------------------- #
# extract() — библиотечный уровень
# --------------------------------------------------------------------------- #
class TestExtractPdf:
    def test_extract_returns_pdf_source_format(self, pdf_text_layer):
        from extractor import extract

        doc = extract(str(pdf_text_layer))
        assert doc.source_format == "pdf"
        assert len(doc.segments) == 1
        assert "7707083893" in doc.segments[0].text
        assert doc.segments[0].source_type == "pdf_page"
        assert doc.segments[0].metadata["page_number"] == 1

    def test_extract_one_segment_per_page(self, pdf_with_image_page):
        from extractor import extract

        doc = extract(str(pdf_with_image_page))
        assert len(doc.segments) == 2
        assert doc.segments[1].text.strip() == ""  # картинка — текста нет


# --------------------------------------------------------------------------- #
# pdf_completeness — сканирование полноты
# --------------------------------------------------------------------------- #
class TestPdfCompleteness:
    def test_clean_text_pdf_has_no_issues(self, pdf_text_layer):
        from pdf_completeness import scan_pdf_completeness

        issues = scan_pdf_completeness(str(pdf_text_layer))
        assert issues == []

    def test_image_only_page_reported_with_page_number_and_kind(self, pdf_with_image_page):
        from pdf_completeness import scan_pdf_completeness

        issues = scan_pdf_completeness(str(pdf_with_image_page))
        assert len(issues) == 1
        assert issues[0].page_number == 2
        assert issues[0].kind == "image_only"

    def test_blank_page_without_image_is_no_text_layer(self, pdf_blank_page):
        from pdf_completeness import scan_pdf_completeness

        issues = scan_pdf_completeness(str(pdf_blank_page))
        assert len(issues) == 1
        assert issues[0].page_number == 2
        assert issues[0].kind == "no_text_layer"

    def test_legal_summary_explains_consequence_in_plain_russian(self, pdf_with_image_page):
        from pdf_completeness import scan_pdf_completeness, legal_summary

        issues = scan_pdf_completeness(str(pdf_with_image_page))
        summary = legal_summary(issues)
        assert "страница 2" in summary
        assert "НЕ была анонимизирована" in summary
        assert "проверьте вручную" in summary


# --------------------------------------------------------------------------- #
# CLI сквозняк — обязательные приёмочные случаи
# --------------------------------------------------------------------------- #
class TestCliPdfEncrypt:
    def test_text_layer_pdf_prints_session_id_and_anon_text(self, pdf_text_layer, tmp_path):
        r = _run(["encrypt", str(pdf_text_layer)], tmp_path)
        assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        sid = r.stdout.strip()
        assert sid
        anon = read_anon_text(_sessions_dir(tmp_path), sid)
        assert anon

    def test_image_page_does_not_refuse_and_prints_session_id(self, pdf_with_image_page, tmp_path):
        r = _run(["encrypt", str(pdf_with_image_page)], tmp_path)
        assert r.returncode == 0, f"PDF-путь не отказывает; stdout={r.stdout!r} stderr={r.stderr!r}"
        sid = r.stdout.strip()
        assert sid

    def test_image_page_warning_carries_page_number_and_reason(self, pdf_with_image_page, tmp_path):
        r = _run(["encrypt", str(pdf_with_image_page)], tmp_path)
        assert "2" in r.stderr
        assert "скан" in r.stderr or "изображение" in r.stderr

    def test_incomplete_flag_is_programmatically_readable(self, pdf_with_image_page, tmp_path):
        """Признак неполноты доступен программно — не парсингом текста
        предупреждения: sidecar несёт булев incomplete и список issues."""
        r = _run(["encrypt", str(pdf_with_image_page)], tmp_path)
        sid = r.stdout.strip()
        payload = read_unread_sidecar(_sessions_dir(tmp_path), sid)
        assert payload["incomplete"] is True
        assert payload["format"] == "pdf"
        assert payload["issues"][0]["page_number"] == 2
        assert payload["issues"][0]["kind"] == "image_only"

    def test_clean_pdf_gets_no_sidecar(self, pdf_text_layer, tmp_path):
        r = _run(["encrypt", str(pdf_text_layer)], tmp_path)
        sid = r.stdout.strip()
        assert not (_sessions_dir(tmp_path) / f"{sid}.unread.json").exists()
