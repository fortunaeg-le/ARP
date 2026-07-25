# -*- coding: utf-8 -*-
"""Этап S3 — зеркала двух починенных регрессий этапа E′.

M-2: «расплетающий» вид адреса рождал кандидатов, которых в исходном тексте не
     было (номер пункта «3.2», ярлык реквизита соседней строки, метка, паспортный
     хвост) — потому что НЕсклеенный \\n превращался в пробел и граница
     предложения исчезала.
M-1: cross-segment ORG эмитил обе половины ЛЮБОГО якоря, пересёкшего стык, —
     включая огрызки в одну букву и голые орг-формы.

Каждая пара зеркал: (−) мусор не появляется И (+) выигрыш E′ на месте.
"""
import os

from models import SourceDocument, TextSegment
from ner_detector import _addr_deweave
from pipeline import run_detection
from tokenizer import tokenize, build_plain_text, _org_half_carries_name
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
    return SourceDocument(
        segments=[TextSegment(id="l%d" % i, text=t, source_type="txt_line",
                              metadata={}) for i, t in enumerate(lines)],
        source_format="txt", source_path="x.txt")


def _kept(doc):
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    return anon, kept


def _of_type(kept, t):
    return [e for e in kept if e.entity_type == t]


# --------------------------------------------------------------------------- #
#                     M-2: вид чинит только разрыв слова                       #
# --------------------------------------------------------------------------- #
class TestDeweaveViewInvariant:
    def test_view_differs_from_source_only_at_glue(self):
        """ИНВАРИАНТ S3: вид отличается от исходного текста ТОЛЬКО в точках
        склейки. Всё, что не склеено, стоит на своём месте своим символом —
        значит вид не может породить кандидата, которого не было в тексте."""
        base = ("3.1. Место: 620014, г. Екатеринбург, Ленинградск\nая 36\n"
                "3.2. Контактное лицо — Иванов И. И.")
        view, idx, changed = _addr_deweave(base)
        assert changed          # «Ленинградск\nая» склеено
        for k, src in enumerate(idx):
            assert view[k] == base[src] or (
                view[k] == "\n" and base[src] in "\n\r") or (
                view[k] == " " and base[src] not in "\n\r"), (k, view[k], base[src])
        # ключевое: НЕсклеенный перенос остался переносом, а не стал пробелом
        assert "\n3.2." in view, repr(view)

    def test_unglued_newline_is_not_a_space(self):
        """Перенос между ЦЕЛЫМИ словами не склеивается и не размягчается."""
        view, _idx, changed = _addr_deweave("кв. 33\nИНН 2537479952")
        assert not changed
        assert view == "кв. 33\nИНН 2537479952"

    def test_torn_word_is_still_glued(self):
        """(+) выигрыш E′ на месте: разрыв ВНУТРИ слова по-прежнему склеивается."""
        view, _idx, changed = _addr_deweave("Ленинградск\nая 36")
        assert changed and "Ленинградская 36" == view

    def test_torn_index_is_still_glued(self):
        """(+) выигрыш E′ на месте: рваный пробелами индекс склеивается."""
        view, _idx, changed = _addr_deweave("г. Казань, 420 111")
        assert changed and view.endswith("420111")

    def test_index_torn_by_newline_is_glued(self):
        """(+) индекс, рваный ПЕРЕНОСОМ, склеивается: 4+2 = 6 цифр."""
        view, _idx, changed = _addr_deweave("г. Казань, 1430\n00, ул. Мира")
        assert changed and "143000" in view, repr(view)

    def test_line_end_number_is_not_welded_to_item_number(self):
        """(−) конец строки НЕ приваривается к номеру пункта следующей: «вл. 63»
        + «3.2.» дают не индекс, значит это не разрыв числа, а граница строк.
        Ровно из такой сварки («633.2») росла маска «3.2» (регрессия M-2)."""
        view, _idx, changed = _addr_deweave("просп. Тверская, вл. 63\n3.2. Уведомления")
        assert not changed
        assert "\n3.2." in view, repr(view)


class TestNoProseOverMask:
    def test_minus_item_number_of_next_line_is_not_address(self):
        """(−) номер пункта договора на следующей строке НЕ попадает под
        [ADDRESS] (регрессия M-2: 50 масок «3.2» на корпусе)."""
        doc = _para_doc(
            "3.1. Место исполнения: 620014, г. Екатери нбург, просп. Тверская, вл. 63\n"
            "3.2. Уведомления направляются Кузнецовой Марии Ивановне.")
        _anon, kept = _kept(doc)
        for a in _of_type(kept, "ADDRESS"):
            assert "3.2" not in a.original_text, a.original_text

    def test_minus_item_number_across_segments_is_not_address(self):
        """(−) то же через ГРАНИЦУ СЕГМЕНТОВ (B3-окно): половина «3.2» не эмитится."""
        doc = _lines_doc(
            "3.1. Место исполнения: 620014, г. Екатери нбург, просп. Тверская, вл. 63.",
            "3.2. Уведомления направляются Кузнецовой Марии Ивановне.")
        _anon, kept = _kept(doc)
        heads = [a.original_text for a in _of_type(kept, "ADDRESS")
                 if a.segment_id == "l1"]
        assert heads == [], heads

    def test_minus_requisite_label_of_next_line_is_not_address(self):
        """(−) ярлык реквизита следующей строки («ИНН») не уходит под адресную
        маску даже когда где-то в том же сегменте есть склейка."""
        doc = _para_doc(
            "Почтовый адрес: Москва, Профсоюзн​ая 88/1 кв 33\nИНН 2537479952")
        _anon, kept = _kept(doc)
        for a in _of_type(kept, "ADDRESS"):
            assert not a.original_text.rstrip().endswith("ИНН"), a.original_text

    def test_plus_torn_address_still_assembled(self):
        """(+) выигрыш E′ сохранён: рваное слово адреса собирается одной
        сущностью и после фикса M-2."""
        _anon, kept = _kept(_para_doc(
            "Место: по адресу: г. Химки, Ленинградск\nая 36, офис 4."))
        addrs = _of_type(kept, "ADDRESS")
        assert any("Ленинградск\nая 36" in a.original_text for a in addrs), addrs


# --------------------------------------------------------------------------- #
#                  M-1: cross-segment ORG — ремонт, не расползание             #
# --------------------------------------------------------------------------- #
class TestOrgHalfPredicate:
    def test_short_fragment_carries_no_name(self):
        assert not _org_half_carries_name("к")
        assert not _org_half_carries_name("с»")
        assert not _org_half_carries_name(" »")

    def test_bare_form_carries_no_name(self):
        assert not _org_half_carries_name("ПАО")
        assert not _org_half_carries_name("ООО ")

    def test_name_piece_carries_name(self):
        assert _org_half_carries_name(" «Альтаир»")
        assert _org_half_carries_name("ПАО «Гр")
        assert _org_half_carries_name("аир")


class TestCrossSegmentOrgS3:
    def test_minus_single_letter_half_not_emitted(self):
        """(−) «ПАО «Альтаи|р»»: огрызок «р»» маской не накрывается."""
        doc = _lines_doc("Работодатель: ПАО «Альтаи", "р», в лице директора.")
        _anon, kept = _kept(doc)
        texts = {e.original_text for e in _of_type(kept, "ORG")}
        assert "ПАО «Альтаи" in texts and "р»" not in texts, texts

    def test_minus_bare_form_half_not_emitted(self):
        """(−) «ПАО|«Альтаир»»: голая форма маской не накрывается, имя — да."""
        doc = _lines_doc("Работодатель: ПАО", "«Альтаир», в лице директора.")
        _anon, kept = _kept(doc)
        texts = {e.original_text for e in _of_type(kept, "ORG")}
        assert not any(t.strip() == "ПАО" for t in texts), texts
        assert any("Альтаир" in t for t in texts), texts

    def test_minus_no_pair_when_org_already_found_in_segment(self):
        """(−) главный механизм M-1: организация НАЙДЕНА посегментно, а через
        стык переползает лишь хвост якоря — пары нет вовсе (иначе в соседний
        сегмент уезжает маска «к» из «к/с»)."""
        doc = _lines_doc("Банк получателя: ПАО Сбербанк", "к/с 30101810400000000225")
        _anon, kept = _kept(doc)
        for e in _of_type(kept, "ORG"):
            assert e.segment_id != "l1", (e.segment_id, e.original_text)

    def test_plus_torn_org_still_assembled(self):
        """(+) выигрыш E′ сохранён: имя, рваное стыком, найдено (тип ORG)."""
        doc = _lines_doc("Работодатель: ПАО «Гр", "анит», в лице директора.")
        _anon, kept = _kept(doc)
        texts = {e.original_text for e in _of_type(kept, "ORG")}
        assert any("Гр" in t for t in texts), texts

    def test_roundtrip_exact_after_s3(self, tmp_path):
        """Целостность: восстановление побайтно на обеих конструкциях."""
        for doc in (
            _lines_doc("Работодатель: ПАО «Альтаи", "р», в лице директора."),
            _lines_doc("Банк получателя: ПАО Сбербанк", "к/с 30101810400000000225"),
            _para_doc("3.1. Место: 620014, г. Екатери нбург, Тверская, вл. 63\n"
                      "3.2. Уведомления направляются."),
        ):
            ents = run_detection(doc, CFG)
            anon, kept = tokenize(doc, ents, CFG)
            sid = save_session(kept, storage_dir=str(tmp_path))
            restored, unres = detokenize(anon, sid, storage_dir=str(tmp_path),
                                         mode="exact")
            assert unres == []
            assert restored == build_plain_text(doc)
