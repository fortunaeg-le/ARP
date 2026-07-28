"""Общие фикстуры для тестов SHIFRATOR.

Тяжёлые импорты (natasha через ner_detector) грузятся один раз за сессию
через module/session-scope фикстуры, чтобы не платить ~1с и сотни МБ RAM
на каждый тестовый модуль отдельно (см. HANDOFF_3, "Побочные эффекты импорта").
"""
import os
import sys

import pytest
from docx import Document

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

CONFIG_PATH = os.path.join(_PROJECT_ROOT, "entity_types.yaml")


@pytest.fixture(scope="session")
def config_path():
    return CONFIG_PATH


def pin_all_types(home_dir):
    """ЭТАП T1 — пришпилить набор «Максимум» для теста, ПРОВЕРЯЮЩЕГО ДЕТЕКЦИЮ.

    С этапа T1 набор ПО УМОЛЧАНИЮ — «только персональные данные»: реквизиты
    (ИНН/ОГРН/КПП/БИК/счета) и организации найдены, но не маскируются. Тест,
    который спрашивает «замаскировался ли ИНН», спрашивает про ДЕТЕКТОР, а не
    про настройку пользователя, и потому обязан задать набор явно — иначе он
    молча начнёт мерить умолчание (та же причина, по которой замер и гейт зовут
    токенизацию с `type_policy.MAXIMUM`, см. run_measurement.py).

    Пришпиливание идёт через НАСТОЯЩИЙ файл настроек в подменённом HOME, а не
    через хук в коде: тест обязан ходить тем же путём, что пользователь.
    """
    import json
    from pathlib import Path

    d = Path(home_dir) / ".shifrator"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(
        json.dumps({"profile": "maximum"}), encoding="utf-8")
    return d / "settings.json"


@pytest.fixture(scope="session")
def ner_detector_module():
    """Импортирует ner_detector (и тем самым natasha) один раз за сессию тестов."""
    import ner_detector
    return ner_detector


def make_docx(path, paragraphs_before, table_rows, paragraphs_after, heading=None):
    """Строит .docx: опциональный heading, параграфы before, таблица NxM, параграфы after.

    table_rows: list[list[str]] — текст ячеек по строкам.
    Возвращает путь к файлу.
    """
    doc = Document()
    if heading is not None:
        doc.add_heading(heading, level=1)
    for p in paragraphs_before:
        doc.add_paragraph(p)
    if table_rows:
        n_rows = len(table_rows)
        n_cols = len(table_rows[0])
        table = doc.add_table(rows=n_rows, cols=n_cols)
        for r, row in enumerate(table_rows):
            for c, text in enumerate(row):
                table.cell(r, c).text = text
    for p in paragraphs_after:
        doc.add_paragraph(p)
    doc.save(path)
    return str(path)


@pytest.fixture
def docx_factory(tmp_path):
    """Фабрика .docx файлов в tmp_path. Использование:
    path = docx_factory("name.docx", paragraphs_before=[...], table_rows=[[...]], paragraphs_after=[...])
    """
    def _factory(filename="doc.docx", paragraphs_before=(), table_rows=None, paragraphs_after=(), heading=None):
        path = tmp_path / filename
        return make_docx(path, list(paragraphs_before), table_rows, list(paragraphs_after), heading=heading)
    return _factory


@pytest.fixture
def txt_factory(tmp_path):
    """Фабрика .txt файлов в tmp_path с заданным содержимым и кодировкой."""
    def _factory(content, filename="doc.txt", encoding="utf-8-sig"):
        path = tmp_path / filename
        with open(path, "w", encoding=encoding, newline="") as f:
            f.write(content)
        return str(path)
    return _factory
