"""test_integration.py — сквозной прогон encrypt -> decrypt (раздел "Приёмка" блока 7).

Проверяет весь пайплайн блоков 1-6 через их публичные функции (не через CLI-подпроцесс,
чтобы иметь прямой доступ к build_plain_text как независимому эталону). Изоляция
хранилища сессий — только через tmp_path, реальная ~/.shifrator/sessions не трогается.
"""
from extractor import extract
from regex_detector import detect_regex
from tokenizer import tokenize, build_plain_text
from session_store import save_session
from detokenizer import detokenize


class TestEndToEndEncryptDecrypt:
    """Спека, блок 7, Приёмка: восстановленный текст идентичен build_plain_text(doc)."""

    def test_restored_text_matches_build_plain_text_reference(self, docx_factory, ner_detector_module, config_path, tmp_path):
        """Спека, блок 7, Приёмка: decrypt(encrypt(text)) без изменений == build_plain_text(doc), не бинарный .docx."""
        path = docx_factory(
            heading="Договор поставки",
            paragraphs_before=[
                "Договор подписал Иванов И.И. от лица ООО «Ромашка», ИНН 7707083893.",
                "Адрес: г. Москва, ул. Ленина, д. 5, оф. 12.",
            ],
            table_rows=[["Поставщик", "ООО «Ромашка»"], ["Покупатель", "Петров П.П."]],
            paragraphs_after=["Подписи сторон скреплены печатями организаций."],
        )
        doc = extract(path)
        entities = detect_regex(doc, config_path) + ner_detector_module.detect_ner(doc, config_path)
        anon_text, final_entities = tokenize(doc, entities, config_path)
        sid = save_session(final_entities, storage_dir=str(tmp_path))

        restored, unresolved = detokenize(anon_text, sid, storage_dir=str(tmp_path))

        assert restored == build_plain_text(doc)
        assert unresolved == []

    def test_anonymized_text_has_no_original_sensitive_values(self, docx_factory, ner_detector_module, config_path, tmp_path):
        """Спека, блок 4/7: анонимизированный текст (передаваемый LLM) не содержит оригинальных значений."""
        path = docx_factory(
            paragraphs_before=["Договор подписал Иванов И.И. от лица ООО «Ромашка», ИНН 7707083893."],
        )
        doc = extract(path)
        # Этап A': ORG даёт структурный движок (detect_org), Natasha-ORG выключена;
        # detect_org считается ДО detect_ner и передаётся туда барьером адреса (A-4).
        from anchor_registry import detect_org, suppress_conflicts
        rx = detect_regex(doc, config_path)
        org = detect_org(doc, regex_entities=rx)
        ner = ner_detector_module.detect_ner(doc, config_path, regex_entities=rx, org_entities=org)
        ner = suppress_conflicts(org, ner)
        anon_text, final_entities = tokenize(doc, rx + ner + org, config_path)

        assert "7707083893" not in anon_text
        assert "ООО «Ромашка»" not in anon_text

    def test_all_final_entities_have_nonempty_token_end_to_end(self, docx_factory, ner_detector_module, config_path):
        """Спека, блок 4: сквозной прогон подтверждает all(e.token for e in result) на реальном документе."""
        path = docx_factory(
            paragraphs_before=["Договор подписал Иванов И.И. от лица ООО «Ромашка», ИНН 7707083893."],
        )
        doc = extract(path)
        entities = detect_regex(doc, config_path) + ner_detector_module.detect_ner(doc, config_path)
        _, final_entities = tokenize(doc, entities, config_path)

        assert all(e.token for e in final_entities)

    def test_unknown_token_in_llm_reply_is_reported_as_unresolved(self, docx_factory, ner_detector_module, config_path, tmp_path):
        """Спека, блок 6, Приёмка: если LLM вставила несуществующий токен, он остаётся в тексте и попадает в unresolved."""
        path = docx_factory(paragraphs_before=["ИНН 7707083893."])
        doc = extract(path)
        entities = detect_regex(doc, config_path) + ner_detector_module.detect_ner(doc, config_path)
        anon_text, final_entities = tokenize(doc, entities, config_path)
        sid = save_session(final_entities, storage_dir=str(tmp_path))

        llm_reply = anon_text + " а также неизвестный [ORG_99]"
        restored, unresolved = detokenize(llm_reply, sid, storage_dir=str(tmp_path))

        assert "[ORG_99]" in restored
        assert unresolved == ["[ORG_99]"]

    def test_txt_input_roundtrips_through_full_pipeline(self, txt_factory, ner_detector_module, config_path, tmp_path):
        """Спека, блок 7, Приёмка: пайплайн также корректно работает на .txt-входе (не только .docx)."""
        path = txt_factory("ИНН 7707083893, email test@example.com\r\nВторая строка\r\n", encoding="utf-8-sig")
        doc = extract(path)
        entities = detect_regex(doc, config_path) + ner_detector_module.detect_ner(doc, config_path)
        anon_text, final_entities = tokenize(doc, entities, config_path)
        sid = save_session(final_entities, storage_dir=str(tmp_path))

        restored, unresolved = detokenize(anon_text, sid, storage_dir=str(tmp_path))

        assert restored == build_plain_text(doc)
        assert unresolved == []
