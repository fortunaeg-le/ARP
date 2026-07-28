# -*- coding: utf-8 -*-
"""
Состязательные приёмы над НОВЫМИ видами данных (задача 3 этапа CORPUS-V2-B).

ЧЕГО НЕ БЫЛО. До этого этапа приёмов у сумм, процентов, сроков и номеров не
было ВООБЩЕ. Гомоглифы, невидимые символы, разрывы форматированием и границей
ячейки применялись только к ПДн старого набора — ФИО, ИНН, счетам, адресам.
Новые виды данных измерялись на чистом тексте, и цифры по ним были заведомо
оптимистичны: реальный договор так не выглядит.

ПОЧЕМУ ПРИЁМ — НЕ ОСЬ. Правило теста осей — «ни одно значение не занимает
больше половины вхождений». Будь приём осью, значение «приёма нет» обязано
было бы уместиться в половину: половина сумм корпуса должна была бы быть
искажена. Это неправдоподобно и вдобавок противоречит требованию ТЗ отдать
разрыву форматированием БОЛЬШЕ ВСЕГО вхождений среди приёмов. Поэтому у
приёмов своё правило и свой тест — этот.

ЧТО ПРОВЕРЯЕТСЯ
  1. Каждый приём из списка ТЗ реально встречается.
  2. Разрыв форматированием встречается ЧАЩЕ ЛЮБОГО другого приёма — это
     самый вероятный реальный случай (Word рвёт ран на любой правке).
  3. Приёмы применены не к одному виду данных, а к нескольким.
  4. Разрыв форматированием и граница ячейки ВИДНЫ В OOXML: приём, не дошедший
     до файла, — это пометка в разметке и ничего больше.
  5. Разрыв форматированием НЕ МЕНЯЕТ текст документа. В этом весь смысл
     приёма: канонический текст прежний, а ранов два. Если бы он менял текст,
     он был бы неотличим от «пробелов внутри цифр».
"""
import json
import os
import re
import sys
import zipfile
from collections import Counter
from xml.sax.saxutils import escape

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_V2 = os.path.join(ROOT, "tests", "corpus_v2")
DOCS = os.path.join(CORPUS_V2, "docs")
GOLD_V2 = os.path.join(CORPUS_V2, "gold_v2.json")
sys.path.insert(0, CORPUS_V2)

import values as V  # noqa: E402

# Список приёмов задан ТЗ этапа. Держится здесь, а не выводится из корпуса:
# иначе исчезнувший приём просто перестал бы проверяться.
REQUIRED = ["format_split", "homoglyph", "invisible", "digit_spaces",
            "linebreak", "cell_split"]
MOST_FREQUENT = "format_split"
MIN_TYPES = 3            # приёмы обязаны лечь не на один вид данных

# Символы, которых в величине с разрывом форматированием быть НЕ ДОЛЖНО:
# следы других приёмов. Записаны кодами намеренно — в исходнике они
# неотличимы от пустоты, и правка «на глаз» их бы потеряла.
# NBSP (\u00a0) здесь СОЗНАТЕЛЬНО ОТСУТСТВУЕТ: это законное значение оси
# «разделитель разрядов» («10\u00a0000 рублей»), а не состязательный приём.
FORBIDDEN = (
    "\u200b",   # zero-width space
    "\u200c",   # zero-width non-joiner
    "\u200d",   # zero-width joiner
    "\u00ad",   # мягкий перенос
    "\u2060",   # word joiner
    "\u202f",   # narrow no-break space
    "\u200e",   # left-to-right mark
)


@pytest.fixture(scope="module")
def gold():
    if not os.path.exists(GOLD_V2):
        pytest.fail("Нет %s — соберите корпус: "
                    "venv/Scripts/python.exe tests/corpus_v2/generate.py" % GOLD_V2)
    with open(GOLD_V2, encoding="utf-8") as f:
        return json.load(f)


def tricked(gold):
    """Вхождения новых видов данных, к которым применён приём."""
    out = []
    for d in gold:
        for e in list(d["entities"]) + list(d.get("negatives", [])):
            if e.get("type") in V.AXES and e.get("trick"):
                out.append((d, e))
    return out


def test_every_required_trick_occurs(gold):
    c = Counter(e["trick"] for _, e in tricked(gold))
    missing = [t for t in REQUIRED if not c.get(t)]
    assert not missing, (
        "Приёмы из списка задачи 3 не встречаются в корпусе: %s.\n"
        "Найдено: %s.\n"
        "Метрика новых видов данных снова измерена по чистому тексту."
        % (", ".join(missing), dict(c)))


def test_format_split_is_the_most_frequent_trick(gold):
    """ТЗ: разрыву форматированием — больше всего вхождений."""
    c = Counter(e["trick"] for _, e in tricked(gold))
    others = {t: n for t, n in c.items() if t != MOST_FREQUENT}
    top = max(others.values()) if others else 0
    assert c[MOST_FREQUENT] > top, (
        "Разрыв форматированием встречается %d раз, а самый частый из "
        "остальных приёмов — %d. Разрыв ранов — самый вероятный реальный "
        "случай (Word рвёт ран на любой правке), и вхождений у него обязано "
        "быть больше всех. Распределение: %s"
        % (c[MOST_FREQUENT], top, dict(c)))


def test_tricks_hit_several_data_types(gold):
    by_trick = {}
    for _, e in tricked(gold):
        by_trick.setdefault(e["trick"], set()).add(e["type"])
    thin = {t: sorted(s) for t, s in by_trick.items() if len(s) < MIN_TYPES}
    assert not thin, (
        "Приёмы легли меньше чем на %d вида данных: %s.\n"
        "Приём, живущий на одном виде, проверяет этот вид, а не приём."
        % (MIN_TYPES, thin))


def _split_chunks(model):
    """Чанки модели, помеченные разрывом форматированием, с позицией разрыва."""
    out = []

    def walk(blocks):
        for b in blocks:
            if b.get("t") == "tbl":
                for row in b["rows"]:
                    for c in row:
                        walk(c["blocks"])
                continue
            for ch in b.get("chunks", ()):
                mark = (ch.get("e") or {}).get("trick") or \
                       (ch.get("n") or {}).get("trick")
                if mark == "format_split" and isinstance(ch.get("bs"), int) \
                        and ch["bs"] is not True:
                    out.append((ch["s"], ch["bs"]))
    walk(model["body"])
    return out


def test_format_split_really_splits_the_run(gold):
    """Приём, не дошедший до файла, — это пометка в разметке и ничего больше.

    Проверка ТОЧНАЯ, и обе более простые её версии оказались негодны:

      * «в документе есть <w:b/>» — зелено всегда: жирные заголовки есть в
        каждом документе, приём мог не ставиться вовсе;
      * «текста величины нет в XML сплошным куском» — ЛОЖНО КРАСНЕЕТ, когда
        точно такая же величина встречается в документе ещё раз, уже без
        приёма. Так и вышло на cx_agency_0016.

    Поэтому строится ОЖИДАЕМЫЙ фрагмент XML: два рана, первый жирный, граница
    между ними — в позиции разрыва, взятой ИЗ МОДЕЛИ (а не пересчитанной здесь
    заново: пересчёт дублировал бы логику генератора и проверял бы её саму).
    """
    CL_dir = CORPUS_V2
    checked, missing = 0, []
    for d in gold:
        if d["format"] != "docx":
            continue
        with open(os.path.join(CL_dir, "_model", d["doc_id"] + ".json"),
                  encoding="utf-8") as f:
            model = json.load(f)
        pairs = _split_chunks(model)
        if not pairs:
            continue
        with zipfile.ZipFile(os.path.join(DOCS, d["doc_id"] + ".docx")) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        for s, k in pairs:
            checked += 1
            want = ('<w:r><w:rPr><w:b/></w:rPr>'
                    '<w:t xml:space="preserve">%s</w:t></w:r>'
                    '<w:r><w:t xml:space="preserve">%s</w:t></w:r>'
                    % (escape(s[:k]), escape(s[k:])))
            if want not in xml:
                missing.append((d["doc_id"], s, k))
    assert checked, "Ни одного .docx с пометкой format_split — приём не поставлен."
    assert not missing, (
        "Разрыв форматированием помечен в разметке, но в .docx величина на два "
        "рана не разорвана: %s" % missing[:5])


def test_cell_split_really_crosses_a_cell_boundary(gold):
    """Половины величины обязаны лежать в РАЗНЫХ ячейках.

    Признак в каноническом тексте — табуляция внутри спана: разделитель ячеек
    по правилу PT-1 (corpus_lib._emit_blocks). Спан без неё границу ячейки не
    пересёк, чем бы его ни пометили.
    """
    marked = [(d["doc_id"], e) for d in gold
              for e in list(d["entities"]) + list(d.get("negatives", []))
              if e.get("trick") == "cell_split"]
    assert marked, "Приём «граница ячейки» в корпусе отсутствует."
    flat = [(doc, e["text"]) for doc, e in marked if "\t" not in e["text"]]
    assert not flat, (
        "Величины с пометкой cell_split не содержат разделителя ячеек: %s.\n"
        "Значит, границу ячейки они не пересекают." % flat[:5])
    with_tbl = 0
    for doc, _ in marked:
        with zipfile.ZipFile(os.path.join(DOCS, doc + ".docx")) as z:
            if re.search(r"<w:tbl[ >]", z.read("word/document.xml").decode("utf-8")):
                with_tbl += 1
    assert with_tbl == len(marked), (
        "В %d из %d документов с cell_split нет таблицы в OOXML."
        % (len(marked) - with_tbl, len(marked)))


def test_format_split_does_not_change_the_text(gold):
    """Разрыв ранов невидим в тексте — иначе это другой приём.

    Величина с этим приёмом обязана читаться как обычная запись: ни лишних
    пробелов, ни переводов строки, ни подменённых символов. Ловушка здесь для
    детектора, а не для читателя.
    """
    bad = []
    for d in gold:
        for e in list(d["entities"]) + list(d.get("negatives", [])):
            if e.get("trick") != "format_split":
                continue
            t = e["text"]
            if "\n" in t or "\t" in t or any(ch in t for ch in FORBIDDEN):
                bad.append((d["doc_id"], t))
    assert not bad, (
        "Величины с разрывом форматированием содержат следы ДРУГИХ приёмов "
        "(перевод строки, табуляция, невидимые символы): %s.\n"
        "Смысл приёма в том, что текст НЕ меняется, а ранов два."
        % bad[:5])
