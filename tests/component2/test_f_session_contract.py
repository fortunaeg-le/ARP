"""Группа F — контракт с существующей системой (session_store, блок 5).

detokenize_file не должен маскировать ошибки хранилища сессий, не должен
портить исходный файл при отказе, и не должен молча затирать уже существующий
файл назначения.
"""
import os
import zipfile

import pytest

from file_detokenizer import detokenize_file
from session_store import SessionExpiredError, SessionNotFoundError

from conftest import W, sha256_of, make_zip


def _docx_with_token(path):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>[ORG_1]</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    return make_zip(path, {"word/document.xml": xml})


# ---------------------------------------------------------------------------
# F1 — истёкшая сессия -> SessionExpiredError пробрасывается наружу
# ---------------------------------------------------------------------------

def test_expired_session_propagates_session_expired_error(tmp_path, session_factory, storage_dir):
    src = _docx_with_token(tmp_path / "src.docx")
    sid = session_factory({"[ORG_1]": "ООО «Ромашка»"}, ttl_hours=-1)  # уже истекла

    with pytest.raises(SessionExpiredError):
        detokenize_file(src, sid, storage_dir=storage_dir)


# ---------------------------------------------------------------------------
# F2 — несуществующая сессия -> SessionNotFoundError
# ---------------------------------------------------------------------------

def test_nonexistent_session_raises_session_not_found_error(tmp_path, storage_dir):
    src = _docx_with_token(tmp_path / "src.docx")

    with pytest.raises(SessionNotFoundError):
        detokenize_file(src, "00000000-0000-0000-0000-000000000000", storage_dir=storage_dir)


# ---------------------------------------------------------------------------
# F3 — session_id невалидного формата (path traversal) -> не доходит до ФС,
# SessionNotFoundError, а не какое-то исключение файловой системы
# ---------------------------------------------------------------------------

def test_path_traversal_session_id_rejected_before_touching_filesystem(tmp_path, storage_dir):
    src = _docx_with_token(tmp_path / "src.docx")
    malicious_id = "../../../../etc/passwd"

    with pytest.raises(SessionNotFoundError):
        detokenize_file(src, malicious_id, storage_dir=storage_dir)

    # Ничего похожего на файл сессии с этим "именем" не появилось в storage_dir.
    for _, dirs, files in os.walk(storage_dir):
        for f in files:
            assert "passwd" not in f


def test_path_traversal_session_id_with_backslashes_rejected(tmp_path, storage_dir):
    src = _docx_with_token(tmp_path / "src.docx")

    with pytest.raises(SessionNotFoundError):
        detokenize_file(src, "..\\..\\windows\\system32\\config\\sam", storage_dir=storage_dir)


# ---------------------------------------------------------------------------
# F4 — dst_path == src_path -> ValueError, исходник не тронут
# ---------------------------------------------------------------------------

def test_dst_equals_src_raises_value_error_and_leaves_source_untouched(tmp_path, session_factory, storage_dir):
    src = _docx_with_token(tmp_path / "same.docx")
    before = sha256_of(src)
    sid = session_factory({"[ORG_1]": "ООО «Ромашка»"})

    with pytest.raises(ValueError):
        detokenize_file(src, sid, dst_path=src, storage_dir=storage_dir)

    assert sha256_of(src) == before


# ---------------------------------------------------------------------------
# F5 — dst_path уже существует -> FileExistsError, существующий файл не затёрт
# ---------------------------------------------------------------------------

def test_existing_dst_path_raises_file_exists_error_and_is_not_overwritten(tmp_path, session_factory, storage_dir):
    src = _docx_with_token(tmp_path / "src.docx")
    dst = tmp_path / "out.docx"
    dst.write_bytes(b"PRE-EXISTING CONTENT THAT MUST SURVIVE")
    before = sha256_of(dst)
    sid = session_factory({"[ORG_1]": "ООО «Ромашка»"})

    with pytest.raises(FileExistsError):
        detokenize_file(src, sid, dst_path=str(dst), storage_dir=storage_dir)

    assert sha256_of(dst) == before


# ---------------------------------------------------------------------------
# F6 — прерывание записи не оставляет недописанный файл под финальным именем
# ---------------------------------------------------------------------------

def test_interrupted_write_leaves_no_partial_file_under_final_name(tmp_path, session_factory, monkeypatch):
    src = _docx_with_token(tmp_path / "src.docx")
    dst = tmp_path / "out.docx"

    real_writestr = zipfile.ZipFile.writestr
    call_count = {"n": 0}

    def flaky_writestr(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("симулированный обрыв записи")
        return real_writestr(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", flaky_writestr)

    from docx_rewriter import rewrite

    with pytest.raises(OSError):
        rewrite(src, str(dst), lambda t: None)

    assert not dst.exists()
    leftover_tmp = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftover_tmp == [], f"остались недописанные временные файлы: {leftover_tmp}"
