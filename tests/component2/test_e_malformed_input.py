"""Группа E — устойчивость к мусорному входу.

Файл пришёл из внешней LLM/агента, мы его не контролируем: он может быть не
ZIP-ом вовсе, обрезанным архивом, ZIP-бомбой или валидным ZIP без единой
нужной части внутри. Ожидание — внятный `OoxmlError`, а не трейсбек, и это
без реальной распаковки (бомбы должны отбрасываться быстро).
"""
import time
import zipfile

import pytest

from ooxml_core import OoxmlError
from docx_rewriter import rewrite as docx_rewrite
from file_detokenizer import detokenize_file

from conftest import W, resolver, make_zip, CONTENT_TYPES


def _resolver_empty(token):
    return None


# ---------------------------------------------------------------------------
# E1 — переименованный .txt -> .docx (произвольные не-ZIP байты)
# ---------------------------------------------------------------------------

def test_renamed_txt_file_raises_ooxml_error_not_traceback(tmp_path):
    src = tmp_path / "fake.docx"
    src.write_text("Это обычный текстовый файл, а не .docx.\n[ORG_1] тест.", encoding="utf-8")
    dst = str(tmp_path / "out.docx")

    with pytest.raises(OoxmlError):
        docx_rewrite(str(src), dst, _resolver_empty)


# ---------------------------------------------------------------------------
# E2 — пустой файл 0 байт
# ---------------------------------------------------------------------------

def test_empty_file_raises_ooxml_error(tmp_path):
    src = tmp_path / "empty.docx"
    src.write_bytes(b"")
    dst = str(tmp_path / "out.docx")

    with pytest.raises(OoxmlError):
        docx_rewrite(str(src), dst, _resolver_empty)


# ---------------------------------------------------------------------------
# E3 — обрезанный архив (валидный ZIP, у которого отрезан конец с central
# directory / EOCD)
# ---------------------------------------------------------------------------

def test_truncated_archive_raises_ooxml_error(tmp_path):
    full = tmp_path / "full.docx"
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>[ORG_1]</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    make_zip(full, {"word/document.xml": xml})

    data = full.read_bytes()
    truncated = tmp_path / "truncated.docx"
    truncated.write_bytes(data[: len(data) // 2])
    dst = str(tmp_path / "out.docx")

    with pytest.raises(OoxmlError):
        docx_rewrite(str(truncated), dst, _resolver_empty)


# ---------------------------------------------------------------------------
# E4 — валидный ZIP-контейнер без единой части, которую компонент считает
# "докс-подобной" (никакого word/document.xml, header/footer и т.п.)
#
# ЗАФИКСИРОВАННЫЙ ДЕФЕКТ (см. COMPONENT2_TEST_REPORT.md): реализация сейчас
# НЕ бросает OoxmlError в этом случае — она молча производит побайтовую копию
# входа с unresolved == [], как будто файл успешно раскрыт. Пользователь не
# может отличить "в файле правда не было токенов" от "это вообще не docx".
# Тест держит ожидание спецификации и должен оставаться красным, пока дефект
# не будет исправлен (правку кода этот набор тестов не делает).
# ---------------------------------------------------------------------------

def test_valid_zip_without_any_target_part_raises_ooxml_error(tmp_path):
    src = make_zip(tmp_path / "not_really_docx.docx", {"unrelated/part.txt": b"hello world"})
    dst = str(tmp_path / "out.docx")

    with pytest.raises(OoxmlError):
        docx_rewrite(src, dst, _resolver_empty)


# ---------------------------------------------------------------------------
# E5 — zip-бомба по числу записей (> 5000), отклоняется без распаковки, быстро
# ---------------------------------------------------------------------------

def test_zip_bomb_entry_count_rejected_fast(tmp_path):
    src = tmp_path / "many_entries.docx"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_STORED) as zf:
        for i in range(5001):
            zf.writestr(f"word/junk{i}.xml", b"")
    dst = str(tmp_path / "out.docx")

    t0 = time.time()
    with pytest.raises(OoxmlError):
        docx_rewrite(str(src), dst, _resolver_empty)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"защита от zip-бомбы (по числу записей) отработала за {elapsed:.3f}с, ожидалось < 1с"


# ---------------------------------------------------------------------------
# E5b — zip-бомба по суммарному распакованному размеру (> 200 МБ), отклоняется
# без реальной распаковки, быстро (сжатая "бомба" на диске — единицы КБ).
# ---------------------------------------------------------------------------

def test_zip_bomb_total_size_rejected_fast_without_decompressing(tmp_path):
    src = tmp_path / "size_bomb.docx"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\x00" * (210 * 1024 * 1024))
    dst = str(tmp_path / "out.docx")

    t0 = time.time()
    with pytest.raises(OoxmlError):
        docx_rewrite(str(src), dst, _resolver_empty)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"защита от zip-бомбы (по размеру) отработала за {elapsed:.3f}с, ожидалось < 1с"


# ---------------------------------------------------------------------------
# E6 — файл без единого токена -> корректная копия, unresolved пуст
# ---------------------------------------------------------------------------

def test_file_without_any_token_produces_intact_copy(tmp_path):
    image_bytes = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-DATA"
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>Текст совсем без токенов.</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = make_zip(tmp_path / "clean.docx", {
        "[Content_Types].xml": CONTENT_TYPES,
        "word/document.xml": xml,
        "word/media/image1.png": image_bytes,
    })
    dst = str(tmp_path / "out.docx")

    replaced, unresolved = docx_rewrite(src, dst, _resolver_empty)

    assert unresolved == []
    assert replaced == 0  # в файле нет токенов — раскрывать нечего
    with zipfile.ZipFile(dst) as zf:
        assert zf.read("word/media/image1.png") == image_bytes
        assert b"\xd0\xa2\xd0\xb5\xd0\xba\xd1\x81\xd1\x82" in zf.read("word/document.xml")  # "Текст" utf-8


# ---------------------------------------------------------------------------
# E7 — .pdf на входе -> ValueError с внятным сообщением
# ---------------------------------------------------------------------------

def test_pdf_input_raises_value_error(tmp_path, session_factory, storage_dir):
    src = str(tmp_path / "contract.pdf")  # даже не должен существовать: проверка расширения раньше
    sid = session_factory({})

    with pytest.raises(ValueError, match=r"(?i)pdf"):
        detokenize_file(src, sid, storage_dir=storage_dir)
