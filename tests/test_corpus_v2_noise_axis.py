# -*- coding: utf-8 -*-
"""Ось искажений распознавания (сессия NOISE-CORPUS) — сторожа прибора.

Прибор проверяется по трём линиям:
  1. ОПИСАНИЕ ЧЕСТНОЕ: у каждого класса объявлено, откуда он взялся
     (`observed-user` / `inferred`), и приём, который он называет, реализован;
  2. ПОРОЖДЕНИЕ СООТВЕТСТВУЕТ ОПИСАНИЮ: каждый объявленный класс встретился в
     корпусе, координаты сходятся с файлом на диске, а эталон знает ПРАВИЛЬНОЕ
     значение;
  3. КОРПУС V2 НЕ ТРОНУТ: ни одного вхождения оси искажений в gold_v2.json.
"""
import json
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_V2 = os.path.join(ROOT, "tests", "corpus_v2")
sys.path.insert(0, CORPUS_V2)

import corpus_lib as CL          # noqa: E402
import noise_lib as NL           # noqa: E402

NOISE = os.path.join(CORPUS_V2, "noise")
GOLD_NOISE = os.path.join(NOISE, "gold_noise.json")


@pytest.fixture(scope="module")
def classes():
    return NL.load_noise_classes()


@pytest.fixture(scope="module")
def gold():
    with open(GOLD_NOISE, encoding="utf-8") as f:
        return json.load(f)


def doc_text(doc_id, fmt):
    p = os.path.join(NOISE, "docs", "%s.%s" % (doc_id, fmt))
    if fmt == "txt":
        with open(p, encoding="utf-8", newline="") as f:
            return f.read()
    return CL.extract_docx(p)


# ------------------------------------------------------------------ описание
def test_classes_declare_evidence(classes):
    """Наблюдённое не смешано с предполагаемым, и хотя бы одно наблюдение есть.

    Прибор, целиком собранный из догадок, мерил бы догадки. Класс `paren_zero`
    — единственный, который видели у живого пользователя; если он исчезнет,
    краснеть должно здесь, а не в отчёте через полгода.
    """
    assert classes, "ни одного класса искажения не описано"
    kinds = {c["id"]: c["evidence"] for c in classes}
    assert "observed-user" in kinds.values(), "нет ни одного НАБЛЮДЁННОГО класса"
    for c in classes:
        assert c["why"].strip(), "%s: не сказано, ПОЧЕМУ распознавание так делает" % c["id"]
        assert c.get("hits", "").strip(), "%s: не сказано, по каким видам бьёт" % c["id"]


def test_value_ops_change_text(classes):
    """Приём над значением обязан что-то менять хотя бы на своём примере.

    Класс, который на всех примерах возвращает строку как есть, — мёртвый:
    в корпусе он даст ноль вхождений и в замере молча покажет «потерь нет».
    """
    for c in classes:
        if c["scope"] != "value":
            continue
        assert c["examples"], "%s: нет ни одного примера" % c["id"]
        rnd = random.Random(c["id"])
        changed = False
        for ex in c["examples"]:
            for _ in range(20):
                if NL.apply_value(c, ex["in"], rnd) != ex["in"]:
                    changed = True
                    break
        assert changed, "%s: приём не изменил ни один свой пример" % c["id"]


# ------------------------------------------------------------------ порождение
def test_every_class_present_in_corpus(classes, gold):
    """Объявлен — значит встречается. Иначе описание расходится с прибором."""
    hit = {}
    for d in gold:
        for e in d["entities"]:
            if e.get("noise"):
                hit[e["noise"]] = hit.get(e["noise"], 0) + 1
    missing = [c["id"] for c in classes if not hit.get(c["id"])]
    assert not missing, "классы объявлены, но в корпусе их нет: %s" % missing
    unknown = set(hit) - {c["id"] for c in classes}
    assert not unknown, "в корпусе есть искажения без файла-описания: %s" % sorted(unknown)


def test_spans_match_files(gold):
    """Координаты берутся из разметки, текст — с диска. Расхождений ноль."""
    for d in gold:
        text = doc_text(d["doc_id"], d["format"])
        for e in d["entities"]:
            got = text[e["start"]:e["end"]]
            assert got == e["text"], ("%s: спан [%d:%d] на диске %r, в разметке %r"
                                      % (d["doc_id"], e["start"], e["end"], got, e["text"]))


def test_gold_knows_clean_value(gold):
    """Эталон знает ПРАВИЛЬНОЕ значение — искажается только текст документа.

    Для приёмов над значением `clean` обязан отличаться от испорченного текста;
    для приёмов окружения (склейка с подписью, перестановка колонок) величина
    не меняется вовсе, и равенство здесь законно.
    """
    ctx = {c["id"] for c in NL.load_noise_classes() if c["scope"] == "context"}
    for d in gold:
        for e in d["entities"]:
            if not e.get("noise"):
                continue
            assert "clean" in e, "%s: искажение без правильного значения" % d["doc_id"]
            if e["noise"] not in ctx:
                assert e["clean"] != e["text"], (
                    "%s: класс %s не изменил величину %r"
                    % (d["doc_id"], e["noise"], e["text"]))


def test_pairs_are_complete(gold):
    """У каждой величины грязного документа есть двойник в чистом, и чистый
    двойник несёт РОВНО то значение, которое грязный объявил правильным."""
    clean = {e["pair"]: e for d in gold if d["doc_id"].startswith("nz_")
             for e in d["entities"] if e.get("pair")}
    dirty = [e for d in gold if d["doc_id"].startswith("nzd_")
             for e in d["entities"] if e.get("pair")]
    assert clean and dirty
    for e in dirty:
        c = clean.get(e["pair"])
        assert c is not None, "нет чистого двойника у %s" % e["pair"]
        assert c["type"] == e["type"]
        if e.get("clean"):
            assert c["text"] == e["clean"], (
                "%s: чистый двойник %r, а грязный считает правильным %r"
                % (e["pair"], c["text"], e["clean"]))


# ------------------------------------------------------------------ корпус v2
def test_corpus_v2_untouched():
    """В gold_v2.json оси искажений нет ни одним вхождением.

    Шумовой корпус — ОТДЕЛЬНЫЙ прибор рядом с корпусом v2, а не его правка:
    иначе все числа этапов до сегодняшнего стали бы несравнимы.
    """
    with open(os.path.join(CORPUS_V2, "gold_v2.json"), encoding="utf-8") as f:
        g = json.load(f)
    bad = [(d["doc_id"], e["text"]) for d in g for e in d["entities"]
           if e.get("noise") or e.get("clean") or e.get("pair")]
    assert not bad, "ось искажений протекла в корпус v2: %s" % bad[:5]
