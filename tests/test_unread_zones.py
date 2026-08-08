# -*- coding: utf-8 -*-
"""Этап 1a — тесты сканера непрочитанных зон (src/unread_zones.py).

Эталон НЕ выдуман здесь: он берётся из замороженного корпуса
(tests/corpus/gold.json, поле features). Документы с признаком hdr_pii /
ftr_pii / footnote_pii / textbox_pii / nested_table ГАРАНТИРОВАННО содержат зону
соответствующего типа — так их построил generate.py.

Проверяются ОБА направления, и оба обязательны для приёмки:
  * полнота — на каждом документе с признаком X сканер обязан вернуть зону X
    (ложноотрицательный = молча утёкший кусок договора, недопустим);
  * отсутствие ложных срабатываний — на документах БЕЗ единого зонного признака
    сканер обязан вернуть пустой список.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from unread_zones import scan_unread_zones  # noqa: E402

CORPUS = os.path.join(ROOT, "tests", "corpus")
DOCS = os.path.join(CORPUS, "docs")
GOLD = os.path.join(CORPUS, "gold.json")

# Признак gold.json -> kind, который сканер обязан вернуть.
#
# ЭТАП NODES: hdr_pii / ftr_pii отсюда УБРАНЫ — колонтитулы теперь читаются
# наравне с телом, и объявлять их зоной было бы ложью. Обратная сторона того
# же контракта проверяется классом TestHeadersAreReadNotDeclared ниже: на тех
# же документах их текст обязан ПОЯВИТЬСЯ в сегментах extract().
FEATURE_TO_KIND = {
    "footnote_pii": "footnote",
    "textbox_pii": "textbox",
    "nested_table": "nested_table",
}

# Признаки, которые больше НЕ дают зоны, потому что зона стала читаемой.
FEATURE_READ_NOW = ("hdr_pii", "ftr_pii")


def _load_gold():
    with open(GOLD, encoding="utf-8") as f:
        return json.load(f)


def _zone_docs():
    """(doc_id, ожидаемые kind'ы) для каждого docx хотя бы с одним зонным признаком."""
    out = []
    for d in _load_gold():
        if d["format"] != "docx":
            continue
        kinds = {FEATURE_TO_KIND[f] for f in d["features"] if f in FEATURE_TO_KIND}
        if kinds:
            out.append((d["doc_id"], sorted(kinds)))
    return out


def _clean_docs():
    """docx БЕЗ единого зонного признака — на них сканер обязан молчать."""
    return [
        d["doc_id"] for d in _load_gold()
        if d["format"] == "docx"
        and not any(f in FEATURE_TO_KIND for f in d["features"])
    ]


def _hdrftr_docs():
    """docx с ПДн в колонтитуле — теперь ЧИТАЕМЫЕ, а не зонные."""
    return [
        d["doc_id"] for d in _load_gold()
        if d["format"] == "docx"
        and any(f in FEATURE_READ_NOW for f in d["features"])
    ]


class TestScannerCompleteness:
    """Направление 1: ни одного ложноотрицательного."""

    @pytest.mark.parametrize("doc_id,expected_kinds", _zone_docs())
    def test_every_declared_zone_is_detected(self, doc_id, expected_kinds):
        zones = scan_unread_zones(os.path.join(DOCS, doc_id + ".docx"))
        found = {z.kind for z in zones}
        missing = set(expected_kinds) - found
        assert not missing, (
            f"{doc_id}: сканер НЕ нашёл зоны {sorted(missing)} "
            f"(признаки gold.json обещают {expected_kinds}); найдено: {sorted(found)}"
        )

    def test_corpus_actually_has_zone_documents(self):
        """Сам параметризованный список не должен молча оказаться пустым —
        иначе тест выше «зелёный» ни на чём."""
        # было >= 100; после этапа NODES колонтитульные документы ушли из этого
        # списка в TestHeadersAreReadNotDeclared — осталось 90
        assert len(_zone_docs()) >= 80


class TestScannerNoFalsePositives:
    """Направление 2: ни одного ложноположительного на чистых документах."""

    @pytest.mark.parametrize("doc_id", _clean_docs())
    def test_document_without_zone_features_yields_no_zones(self, doc_id):
        zones = scan_unread_zones(os.path.join(DOCS, doc_id + ".docx"))
        assert zones == [], (
            f"{doc_id}: gold.json не объявляет ни одной зоны, а сканер вернул "
            f"{[(z.kind, z.part, z.char_count) for z in zones]}"
        )

    def test_corpus_actually_has_clean_documents(self):
        assert len(_clean_docs()) >= 10


class TestZoneShape:
    """Контракт Zone: непустой сырой текст и корректный char_count — на них
    опирается sidecar этапа 1b."""

    def test_zone_fields_are_consistent(self):
        doc_id = _zone_docs()[0][0]
        zones = scan_unread_zones(os.path.join(DOCS, doc_id + ".docx"))
        assert zones
        for z in zones:
            assert z.kind in FEATURE_TO_KIND.values() or z.kind in ("endnote", "comment")
            assert z.char_count == len(z.text)
            assert z.text.strip(), "зона без непробельного текста не должна возвращаться"
            assert z.part.startswith("word/")


class TestHeadersAreReadNotDeclared:
    """ЭТАП NODES, часть 2: колонтитул перестал быть зоной, потому что читается.

    Проверяются ОБА следствия сразу — иначе тест зелен и на регрессе «зону
    убрали, читать не научили», то есть ровно на тихой утечке:
      * сканер НЕ возвращает kind header/footer;
      * текст части word/header*.xml / word/footer*.xml присутствует в сегментах.
    """

    @pytest.mark.parametrize("doc_id", _hdrftr_docs())
    def test_header_zone_is_not_declared_and_content_is_extracted(self, doc_id):
        import sys as _sys
        from unread_zones import scan_unread_zones as _scan
        from extractor import extract

        path = os.path.join(DOCS, doc_id + ".docx")
        kinds = {z.kind for z in _scan(path)}
        assert not (kinds & {"header", "footer"}), (
            f"{doc_id}: колонтитул объявлен зоной, хотя обязан читаться: {sorted(kinds)}"
        )

        parts = {s.metadata.get("part") for s in extract(path).segments}
        parts.discard(None)
        assert any(p.startswith(("word/header", "word/footer")) for p in parts), (
            f"{doc_id}: gold.json обещает ПДн в колонтитуле, а сегментов "
            f"колонтитула нет вовсе: {sorted(parts)}"
        )

    def test_corpus_actually_has_header_documents(self):
        assert len(_hdrftr_docs()) >= 10
