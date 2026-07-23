# -*- coding: utf-8 -*-
"""Этап E′ — зеркала применения мультиспана к отложенным классам:
сборка рваных ADDRESS (в сегменте и через границу сегментов) + cross-segment ORG.

Обязательные зеркала приёмки:
  (+) рваный адрес собрался: границы чистые, тип ADDRESS;
  (−) два РАЗНЫХ адреса подряд через перенос НЕ сливаются в один
      (главный риск класса 1 — адреса в реквизитах идут блоками);
  cross-segment ORG: рваная границей сегментов орг-форма/имя собирается,
  якорные правила C′ не ослаблены (решение принимает прежний детектор).
"""
import os
import tempfile

from models import SourceDocument, TextSegment
from pipeline import run_detection
from tokenizer import tokenize, build_plain_text
from session_store import save_session
from detokenizer import detokenize

CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "entity_types.yaml")


def _para_doc(*paras):
    return SourceDocument(
        segments=[TextSegment(id="p%d" % i, text=t, source_type="docx_paragraph",
                              metadata={"paragraph_index": i})
                  for i, t in enumerate(paras)],
        source_format="docx", source_path="x.docx")


def _lines_doc(*lines):
    """.txt-стиль: строка = сегмент (там \\n-разрыв — граница сегментов)."""
    return SourceDocument(
        segments=[TextSegment(id="l%d" % i, text=t, source_type="txt_line",
                              metadata={}) for i, t in enumerate(lines)],
        source_format="txt", source_path="x.txt")


def _addrs(doc):
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    return anon, kept, [e for e in kept if e.entity_type == "ADDRESS"]


class TestAddressAssemblyPlus:
    def test_plus_word_torn_by_newline_in_segment(self):
        """(+) слово адреса рвано \\n ВНУТРИ сегмента (docx-ячейка): собирается
        одной сущностью с мультиспаном, тип ADDRESS, без «по адресу:»."""
        anon, kept, addrs = _addrs(_para_doc(
            "Место: по адресу: г. Химки, Ленинградск\nая 36, офис 4."))
        assert any("Ленинградск\nая 36" in a.original_text for a in addrs), addrs
        winner = next(a for a in addrs if "Ленинградск\nая 36" in a.original_text)
        assert winner.spans and len(winner.spans) >= 2
        assert "по адресу" not in winner.original_text
        assert winner.original_text.startswith("г. Химки")

    def test_plus_nbsp_torn_words(self):
        """(+) NBSP/невидимые внутри слов («Мир\\xa0а 18») — собирается."""
        anon, kept, addrs = _addrs(_para_doc(
            "Адрес: Нов​осиби‍рск,  Мир\xa0а 18"))
        assert addrs, "рваный невидимыми адрес не собран"
        assert any("Мир\xa0а 18" in a.original_text for a in addrs)

    def test_plus_digit_spaces_index(self):
        """(+) индекс, рваный пробелами («420 111»), под якорем а/я+город."""
        anon, kept, addrs = _addrs(_para_doc("а/я 54, г. Казань, 420 111"))
        assert any("420 111" in a.original_text for a in addrs), addrs

    def test_plus_cross_segment_address(self):
        """(+) адрес, рваный ГРАНИЦЕЙ СЕГМЕНТОВ (txt): обе половины замаскированы."""
        doc = _lines_doc("Адрес доставки: г. Химки, Ленинградск", "ая 36, офис 4.")
        anon, kept, addrs = _addrs(doc)
        texts = {a.original_text for a in addrs}
        assert "г. Химки, Ленинградск" in texts, texts
        assert any(t.startswith("ая 36") for t in texts), texts

    def test_plus_roundtrip_exact(self, tmp_path):
        """1a на сборках: exact-восстановление побайтно."""
        for doc in (
            _para_doc("по адресу: г. Химки, Ленинградск\nая 36, офис 4."),
            _lines_doc("Адрес доставки: г. Химки, Ленинградск", "ая 36, офис 4."),
        ):
            ents = run_detection(doc, CFG)
            anon, kept = tokenize(doc, ents, CFG)
            sid = save_session(kept, storage_dir=str(tmp_path))
            restored, unres = detokenize(anon, sid, storage_dir=str(tmp_path),
                                         mode="exact")
            assert unres == []
            assert restored == build_plain_text(doc)


class TestAddressAssemblyMinus:
    def test_minus_two_addresses_in_column_one_segment(self):
        """(−) два РАЗНЫХ адреса в столбик в ОДНОМ сегменте (docx-ячейка) —
        НЕ сливаются: оба сильные, деление по бывшему \\n."""
        anon, kept, addrs = _addrs(_para_doc(
            "Юридический адрес: 630099, г. Новосибирск, ул. Ленина, д. 5\n"
            "Фактический адрес: 420111, г. Казань, ул. Баумана, д. 12"))
        merged = [a for a in addrs
                  if "Ленина" in a.original_text and "Баумана" in a.original_text]
        assert not merged, "два адреса склеены: %r" % [a.original_text for a in merged]
        joined = " | ".join(a.original_text for a in addrs)
        assert "Ленина" in joined and "Баумана" in joined, "потерян один из адресов"

    def test_minus_two_addresses_cross_segment(self):
        """(−) два адреса в столбик ЧЕРЕЗ границу сегментов (txt) — не сливаются."""
        doc = _lines_doc(
            "Юр. адрес: 630099, г. Новосибирск, ул. Ленина, д. 5",
            "Факт. адрес: 420111, г. Казань, ул. Баумана, д. 12")
        anon, kept, addrs = _addrs(doc)
        merged = [a for a in addrs
                  if "Ленина" in a.original_text and "Баумана" in a.original_text]
        assert not merged, [a.original_text for a in addrs]

    def test_minus_label_of_next_line_not_absorbed(self):
        """Хвост не перелезает через бывший \\n на метку следующей строки:
        «…кв 33\\nИНН …» — «ИНН» в адрес не входит (значение ИНН — свой токен)."""
        anon, kept, addrs = _addrs(_para_doc(
            "Почтовый адрес: Москва Профсоюзная 88/1 кв 33\nИНН 2537479952"))
        for a in addrs:
            assert not a.original_text.rstrip().endswith("ИНН"), a.original_text


class TestCrossSegmentOrg:
    def test_plus_org_torn_across_segments(self):
        """(+) «ПАО «Альтаи|р»» через границу сегментов: обе половины ORG."""
        doc = _lines_doc("Работодатель: ПАО «Альтаи", "р», в лице директора.")
        ents = run_detection(doc, CFG)
        anon, kept = tokenize(doc, ents, CFG)
        orgs = [e for e in kept if e.entity_type == "ORG"]
        texts = {e.original_text for e in orgs}
        assert "ПАО «Альтаи" in texts and "р»" in texts, texts

    def test_minus_no_org_without_anchor_in_window(self):
        """(−) якорные правила НЕ ослаблены: рваный текст без орг-формы/кавычек
        ORG в окне не даёт («Сторона» через перенос — не организация)."""
        doc = _lines_doc("Как согласовано, Сторо", "на обязуется исполнить.")
        ents = run_detection(doc, CFG)
        anon, kept = tokenize(doc, ents, CFG)
        assert [e for e in kept if e.entity_type == "ORG"] == []

    def test_org_roundtrip_exact(self, tmp_path):
        doc = _lines_doc("Работодатель: ПАО «Альтаи", "р», в лице директора.")
        ents = run_detection(doc, CFG)
        anon, kept = tokenize(doc, ents, CFG)
        sid = save_session(kept, storage_dir=str(tmp_path))
        restored, unres = detokenize(anon, sid, storage_dir=str(tmp_path), mode="exact")
        assert unres == []
        assert restored == build_plain_text(doc)
