# -*- coding: utf-8 -*-
"""ЭТАП METRIC-FIX, часть 2 — сторожа локализации ПО ЧАСТЯМ пакета.

ЧЕМ ВРАЛ ПРИБОР. Этап NODES научил систему читать колонтитулы: их сегменты идут
в `doc.segments` после тела и несут `metadata["part"]`. Локализация же искала
текст сегмента курсором-вперёд от начала ТЕЛА, а верхний колонтитул стоит в
PT-1 ПЕРЕД телом — значит не находился никогда. Маски колонтитула не получали
координат, и эталонная сущность записывалась «не найдено», хотя была
замаскирована (в анонимном тексте её нет). Враньё В ПЕССИМИЗМ: recall занижен
ложно, а мы по этим числам решаем, где чинить.

Ниже — три стража: участки частей считаются по правилу PT-1; сегменты
колонтитула локализуются; значение колонтитула действительно замаскировано
(живая проба полным конвейером, а не доверие полю дампа).
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(HERE, "corpus")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, CORPUS)

import measure_lib as ML  # noqa: E402

DOC_ID = "agency_0002"          # .docx с верхним колонтитулом (PHONE + EMAIL)
DOC = os.path.join(CORPUS, "docs", DOC_ID + ".docx")


@pytest.fixture(scope="module")
def gold_doc():
    gold = json.load(open(os.path.join(CORPUS, "gold.json"), encoding="utf-8"))
    return next(d for d in gold if d["doc_id"] == DOC_ID)


def test_part_regions_cover_pt1_in_order():
    """Участки частей идут в порядке правила PT-1 и не налезают друг на друга."""
    G = ML.pt1_text(DOC)
    reg = ML.part_regions(DOC, G)
    assert None in reg, "участок тела обязан быть всегда"
    assert "word/header1.xml" in reg
    spans = sorted(reg.values())
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 < s2, f"участки пересеклись: {spans}"
    hs, he = reg["word/header1.xml"]
    bs, be = reg[None]
    assert hs == 0 and he < bs, "верхний колонтитул обязан стоять ПЕРЕД телом"
    # текст участка совпадает с текстом части — иначе координаты враньё
    header, body, _notes, _footer = ML._docx_part_texts(DOC)
    assert G[hs:he] == header
    assert G[bs:be] == body


def test_header_segments_are_localized():
    """Сегменты колонтитула получают координаты. Без участков — не получали."""
    from extractor import extract
    doc = extract(DOC)
    G = ML.pt1_text(DOC)
    body_start = ML.body_offset(DOC, G)
    hdr = [s for s in doc.segments
           if s.metadata.get("part") == "word/header1.xml" and s.text]
    assert hdr, "в документе нет сегментов верхнего колонтитула — тест не о том"

    old, _ = ML.build_segment_offsets(doc, G, body_start)
    new, _ = ML.build_segment_offsets(doc, G, body_start,
                                      regions=ML.part_regions(DOC, G))
    assert all(old[s.id] is None for s in hdr), (
        "прежнее поведение изменилось — тест перестал доказывать разницу")
    assert all(new[s.id] is not None for s in hdr)
    # тело от правки не двигается ни на символ
    body_ids = [s.id for s in doc.segments if not s.metadata.get("part")]
    assert {i: old[i] for i in body_ids} == {i: new[i] for i in body_ids}


def test_header_value_is_actually_masked(gold_doc):
    """ЖИВАЯ ПРОБА, а не поле дампа: значение колонтитула найдено и закрыто.

    Именно этот факт делал прежний вердикт «не найдено» ВРАНЬЁМ, а не
    консервативной оценкой."""
    import run_measurement as RM
    rec = RM.process_doc(gold_doc)
    G = ML.pt1_text(DOC)
    reg = ML.part_regions(DOC, G)
    hs, he = reg["word/header1.xml"]
    in_header = [e for e in gold_doc["entities"] if hs <= e["start"] and e["end"] <= he]
    assert in_header, "в эталоне нет сущностей колонтитула — тест не о том"
    # сопоставляем по порядку: ent_records идут в порядке gold_doc["entities"]
    by_pos = {i: r for i, r in enumerate(rec["entities"])}
    for i, e in enumerate(gold_doc["entities"]):
        if not (hs <= e["start"] and e["end"] <= he):
            continue
        assert by_pos[i]["found"], (
            f"{e['type']} {e['text']!r} из колонтитула снова считается ненайденным")
        assert by_pos[i]["leak_v2"]["status"] == "none", (
            f"{e['type']} из колонтитула не замаскирован — тогда пессимизм был правдой")
