# -*- coding: utf-8 -*-
"""Этап E — зеркала: мультиспан (Entity.spans), единый id (реестр), канон при
восстановлении, формат сессии v2 + обратная совместимость v1.

Обязательные зеркала приёмки:
  2. МУЛЬТИСПАН: (+) одно значение, рваное разделителями/переносом — собралось;
     (−) два РАЗНЫХ значения рядом — НЕ слились в одну сущность.
  3. ЕДИНЫЙ ID: (+) одна сущность в падежах — один токен; (−) разные сущности
     одного типа — разные токены; однофамильцы — разные id.
  1a. Структурный round-trip (mode="exact") — побайтно 100%.
  1b. Канон: расхождения с оригиналом — ТОЛЬКО подстановка канона.
"""
import json
import os

import pytest

from models import SourceDocument, TextSegment, entity_spans
from multispan import collect_multispan
from pipeline import run_detection
from tokenizer import tokenize, build_plain_text
from session_store import save_session, load_session
from detokenizer import detokenize

CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "entity_types.yaml")


def _doc(*paras):
    return SourceDocument(
        segments=[TextSegment(id="p%d" % i, text=t, source_type="docx_paragraph",
                              metadata={"paragraph_index": i})
                  for i, t in enumerate(paras)],
        source_format="docx", source_path="x.docx")


def _pipeline(*paras, storage_dir=None):
    doc = _doc(*paras)
    ents = run_detection(doc, CFG)
    anon, kept = tokenize(doc, ents, CFG)
    return doc, anon, kept


# --------------------------------------------------------------------------- #
#                     ЗЕРКАЛО 2 — МУЛЬТИСПАН (+) / (−)                         #
# --------------------------------------------------------------------------- #
class TestMultispanMirror:
    def test_plus_phone_grouped_assembled(self):
        """(+) телефон в произвольной группировке собирается ЦЕЛИКОМ одной
        сущностью с несколькими спанами."""
        doc = _doc("Телефон: 8(383)22 1338б.")
        ms = collect_multispan(doc, CFG)
        phones = [e for e in ms if e.entity_type == "PHONE"]
        assert len(phones) == 1
        e = phones[0]
        assert e.original_text == "8(383)22 1338б"
        assert e.spans and len(e.spans) >= 2, "мультиспан обязан хранить куски"
        # spans отсортированы, не пересекаются, лежат внутри hull
        prev = e.start
        for s, sp_end in e.spans:
            assert e.start <= s < sp_end <= e.end
            assert s >= prev
            prev = sp_end

    def test_plus_account_torn_by_newline_assembled(self):
        """(+) счёт, рваный переносом строки, собирается (якорь счёта слева)."""
        doc = _doc("Расчётный счёт: 4070 2810\n7519 1637 6892 в банке.")
        ms = collect_multispan(doc, CFG)
        accs = [e for e in ms if e.entity_type == "BANK_ACCOUNT"]
        assert len(accs) == 1
        assert accs[0].original_text == "4070 2810\n7519 1637 6892"
        assert "\n" in accs[0].original_text  # разделитель внутри hull, байт-в-байт

    def test_plus_birthdate_torn_by_newline_assembled(self):
        doc = _doc("Дата рождения: 07.07.\n1963 г.")
        ms = collect_multispan(doc, CFG)
        bd = [e for e in ms if e.entity_type == "BIRTHDATE"]
        assert len(bd) == 1
        assert bd[0].original_text == "07.07.\n1963"
        assert bd[0].spans and len(bd[0].spans) == 2

    def test_minus_two_accounts_in_column_not_merged(self):
        """(−) ДВА разных счёта в столбик НЕ сливаются в одну сущность:
        суммарные 40 цифр != 20 -> цепочка отвергается целиком."""
        doc = _doc("счёт: 4070 2810\n7519 1637 6892\n3010 1810\n4000 0000 0225")
        ms = collect_multispan(doc, CFG)
        accs = [e for e in ms if e.entity_type == "BANK_ACCOUNT"]
        for e in accs:
            assert len(e.original_text.replace(" ", "").replace("\n", "")) <= 20, \
                "два счёта склеены в одну сущность: %r" % e.original_text

    def test_minus_two_phones_adjacent_not_merged(self):
        """(−) два телефона рядом (столбик) НЕ сливаются: телефонная цепочка не
        цепляется через перенос строки."""
        doc = _doc("Телефоны: 84б 82б85 1Ч\n8(383)22 1338б")
        ms = collect_multispan(doc, CFG)
        phones = [e for e in ms if e.entity_type == "PHONE"]
        assert len(phones) == 2, [e.original_text for e in phones]
        texts = {e.original_text for e in phones}
        assert "84б 82б85 1Ч" in texts and "8(383)22 1338б" in texts

    def test_minus_20_digit_chain_not_taken_as_phone(self):
        """(−) 10-значное под-окно внутри 20-значного счёта НЕ выдёргивается как
        телефон: под-окна запрещены, цепочка оценивается целиком."""
        doc = _doc("код: 7070 2810 7519 1637 6892")   # 20 цифр, начинается с 7
        ms = collect_multispan(doc, CFG)
        assert [e for e in ms if e.entity_type == "PHONE"] == []

    def test_structural_roundtrip_on_multispan_doc(self, tmp_path):
        """1a на документе с мультиспаном: exact-восстановление ПОБАЙТНО."""
        doc, anon, kept = _pipeline(
            "Телефон: 84б 82б85 1Ч.",
            "Расчётный счёт: 4070 2810\n7519 1637 6892.",
        )
        sid = save_session(kept, storage_dir=str(tmp_path))
        restored, unres = detokenize(anon, sid, storage_dir=str(tmp_path), mode="exact")
        assert unres == []
        assert restored == build_plain_text(doc)


# --------------------------------------------------------------------------- #
#                     ЗЕРКАЛО 3 — ЕДИНЫЙ ID (+) / (−)                          #
# --------------------------------------------------------------------------- #
class TestUnifiedIdMirror:
    def test_plus_org_three_cases_one_token(self, ner_detector_module):
        doc, anon, kept = _pipeline(
            "ООО «Меркурий» поставляет товар.",
            "С Меркурием заключён договор. От Меркурия получено согласие.",
        )
        org_tokens = {e.token for e in kept if e.entity_type == "ORG"}
        assert len(org_tokens) == 1, org_tokens
        forms = {e.original_text for e in kept if e.entity_type == "ORG"}
        assert len(forms) >= 3, "падежные формы должны маскироваться тем же id: %s" % forms

    def test_minus_two_orgs_different_tokens(self, ner_detector_module):
        doc, anon, kept = _pipeline(
            "ООО «Ромашка» и ООО «Василёк» заключили договор.",
        )
        by_org = {}
        for e in kept:
            if e.entity_type == "ORG":
                by_org.setdefault(e.token, set()).add(e.original_text)
        assert len(by_org) == 2, by_org

    def test_plus_person_two_forms_one_token(self, ner_detector_module):
        doc, anon, kept = _pipeline(
            "В лице Новиковой Ольги Егоровны, действующей на основании устава.",
            "Подпись: Новикова О. Е.",
        )
        per_tokens = {e.token for e in kept if e.entity_type == "PERSON"}
        assert len(per_tokens) == 1, per_tokens

    def test_minus_namesakes_different_tokens(self, ner_detector_module):
        """Однофамильцы — РАЗНЫЕ персоны, разные id."""
        doc, anon, kept = _pipeline(
            "В лице Иванова Ивана Петровича, с одной стороны.",
            "В лице Иванова Сергея Максимовича, с другой стороны.",
        )
        pers = {}
        for e in kept:
            if e.entity_type == "PERSON":
                pers.setdefault(e.token, set()).add(e.original_text)
        assert len(pers) == 2, pers


# --------------------------------------------------------------------------- #
#              ЧАСТЬ 3 — канон при восстановлении + сессия v2                  #
# --------------------------------------------------------------------------- #
class TestCanonicalRestore:
    def test_canonical_substituted_for_case_forms(self, tmp_path, ner_detector_module):
        doc, anon, kept = _pipeline(
            "ООО «Меркурий», ИНН 7701234567.",
            "С Меркурием заключён договор.",
        )
        sid = save_session(kept, storage_dir=str(tmp_path))
        restored, _ = detokenize(anon, sid, storage_dir=str(tmp_path))  # canonical
        assert "ООО «Меркурий», ИНН 7701234567." in restored
        # падежная форма заменена каноном (юридическая форма из реестра)
        assert "Меркурием" not in restored
        assert restored.count("ООО «Меркурий»") == 2

    def test_1b_diffs_are_only_canon_substitutions(self, tmp_path, ner_detector_module):
        """1b: расхождения canonical-восстановления с оригиналом — ТОЛЬКО замена
        формы сущности каноном. Доказательство: exact-восстановление побайтно
        равно оригиналу, а canonical отличается от exact только в позициях масок."""
        doc, anon, kept = _pipeline(
            "ООО «Меркурий», ИНН 7701234567.",
            "С Меркурием заключён договор. От Меркурия получено.",
            "В лице Новиковой Ольги Егоровны.",
        )
        sid = save_session(kept, storage_dir=str(tmp_path))
        exact, _ = detokenize(anon, sid, storage_dir=str(tmp_path), mode="exact")
        assert exact == build_plain_text(doc)  # 1a: структурная целостность
        # canonical == exact с заменой surface->canonical в каждой маске
        sess = load_session(sid, storage_dir=str(tmp_path))
        expected = anon
        for r in sess["entities"]:
            expected = expected.replace(r["token"], r.get("canonical") or r["original_text"])
        canon, _ = detokenize(anon, sid, storage_dir=str(tmp_path))
        assert canon == expected

    def test_session_v2_stores_every_occurrence_surface(self, tmp_path, ner_detector_module):
        """КРИТИЧНО (часть 3): файл сессии хранит ИСХОДНУЮ ПОВЕРХНОСТНУЮ ФОРМУ
        КАЖДОГО вхождения — даже если восстановление сейчас читает только канон."""
        doc, anon, kept = _pipeline(
            "ООО «Меркурий» поставляет.",
            "С Меркурием заключён договор. От Меркурия получено согласие.",
        )
        sid = save_session(kept, storage_dir=str(tmp_path))
        sess = load_session(sid, storage_dir=str(tmp_path))
        assert sess.get("format") == 2
        org = next(r for r in sess["entities"] if r["entity_type"] == "ORG")
        surfaces = [o["surface"] for o in org["occurrences"]]
        assert len(surfaces) >= 3
        assert "Меркурием" in surfaces and "Меркурия" in surfaces
        for o in org["occurrences"]:
            assert {"segment_id", "start", "end", "spans", "surface"} <= set(o)

    def test_v1_session_backward_compatible(self, tmp_path):
        """Старые сессии (v1: без format/canonical/occurrences) продолжают
        восстанавливаться обоими режимами — original_text как раньше."""
        import json as _json
        from cryptography.fernet import Fernet
        from datetime import datetime, timedelta
        # создаём хранилище с ключом штатным способом
        sid = save_session([], storage_dir=str(tmp_path))
        key = (tmp_path / "key.bin").read_bytes()
        old_sid = "11111111-1111-1111-1111-111111111111"
        created = datetime.now().astimezone()
        payload = {
            "session_id": old_sid,
            "created_at": created.isoformat(),
            "expires_at": (created + timedelta(hours=24)).isoformat(),
            "entities": [
                {"token": "[PHONE_1]", "entity_type": "PHONE",
                 "original_text": "8-916-123-45-67", "segment_id": "p0"},
            ],
        }
        raw = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        (tmp_path / f"{old_sid}.enc").write_bytes(Fernet(key).encrypt(raw))
        for mode in ("canonical", "exact"):
            restored, unres = detokenize("тел. [PHONE_1]!", old_sid,
                                         storage_dir=str(tmp_path), mode=mode)
            assert restored == "тел. 8-916-123-45-67!"
            assert unres == []

    def test_masks_neither_lost_nor_duplicated(self, tmp_path, ner_detector_module):
        """1a: число восстановленных вхождений = числу замаскированных."""
        doc, anon, kept = _pipeline(
            "ООО «Меркурий», ИНН 7701234567. С Меркурием договор.",
        )
        import re
        n_masks = len(re.findall(r"\[[A-Z_]+_\d+\]", anon))
        assert n_masks == len(kept)
        sid = save_session(kept, storage_dir=str(tmp_path))
        restored, unres = detokenize(anon, sid, storage_dir=str(tmp_path), mode="exact")
        assert unres == []
        assert re.findall(r"\[[A-Z_]+_\d+\]", restored) == []
