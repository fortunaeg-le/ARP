"""Общие фикстуры для тестов Компонента 2 (детокенизация файлов).

Изолированы от общего conftest.py (tests/conftest.py) блоков 1-7: та фикстура
тянет natasha через ner_detector_module — здесь она не нужна и не запрашивается
(этим тестам не нужен ни NER, ни regex-детектор, ни tokenizer).

sys.path на корень проекта уже настроен родительским tests/conftest.py (pytest
подключает conftest.py всех родительских директорий).
"""
import hashlib
import zipfile

import pytest

from models import Entity


# ---------------------------------------------------------------------------
# Сессии (обёртка над session_store, изолированная в tmp_path)
# ---------------------------------------------------------------------------

def make_entity(token, original_text, entity_type="ORG"):
    return Entity(
        id=f"e-{token}",
        segment_id="s1",
        start=0,
        end=0,
        original_text=original_text,
        entity_type=entity_type,
        detector="regex",
        confidence=1.0,
        token=token,
    )


@pytest.fixture
def storage_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return str(d)


@pytest.fixture
def session_factory(storage_dir):
    """session_factory({"[ORG_1]": "ООО «Ромашка»", ...}, ttl_hours=24) -> session_id."""
    from session_store import save_session

    def _factory(mapping: dict, ttl_hours: int = 24, session_id=None):
        entities = [make_entity(tok, val) for tok, val in mapping.items()]
        return save_session(entities, session_id=session_id, ttl_hours=ttl_hours, storage_dir=storage_dir)

    return _factory


# ---------------------------------------------------------------------------
# Утилиты общего назначения
# ---------------------------------------------------------------------------

def sha256_of(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read_part(path, name) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name)


def namelist(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def resolver(mapping):
    return lambda token: mapping.get(token)


def assert_untouched_parts_identical(src_path, dst_path, touched_names):
    """Все части архива, не входящие в touched_names, побайтово идентичны."""
    with zipfile.ZipFile(src_path) as zs, zipfile.ZipFile(dst_path) as zd:
        src_names = set(zs.namelist())
        dst_names = set(zd.namelist())
        assert src_names == dst_names, "набор записей архива изменился"
        for name in src_names:
            if name in touched_names:
                continue
            assert zs.read(name) == zd.read(name), f"часть {name!r} изменилась, хотя не должна была"


# ---------------------------------------------------------------------------
# Билдеры OOXML-файлов "вручную" (без python-docx/openpyxl/python-pptx —
# те же ограничения, что и у самих адаптеров блоков 9-11).
# ---------------------------------------------------------------------------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def make_zip(path, parts: dict, compress=zipfile.ZIP_DEFLATED) -> str:
    with zipfile.ZipFile(path, "w", compress) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return str(path)


@pytest.fixture
def docx_builder(tmp_path):
    def _build(filename, document_xml: bytes, extra_parts: dict = None):
        parts = {"word/document.xml": document_xml}
        if extra_parts:
            parts.update(extra_parts)
        return make_zip(tmp_path / filename, parts)
    return _build


@pytest.fixture
def pptx_builder(tmp_path):
    def _build(filename, slide_xml: bytes, extra_parts: dict = None):
        parts = {"ppt/slides/slide1.xml": slide_xml}
        if extra_parts:
            parts.update(extra_parts)
        return make_zip(tmp_path / filename, parts)
    return _build


@pytest.fixture
def xlsx_builder(tmp_path):
    def _build(filename, shared_strings_xml: bytes = None, sheet_xml: bytes = None, extra_parts: dict = None):
        parts = {}
        if shared_strings_xml is not None:
            parts["xl/sharedStrings.xml"] = shared_strings_xml
        if sheet_xml is not None:
            parts["xl/worksheets/sheet1.xml"] = sheet_xml
        if extra_parts:
            parts.update(extra_parts)
        return make_zip(tmp_path / filename, parts)
    return _build
