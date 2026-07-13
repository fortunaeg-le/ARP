"""Группа C — unresolved: посимвольная сохранность, дедупликация, порядок.

Дедупликация и порядок "по первому появлению" — контракт фасада (блок 12,
file_detokenizer.detokenize_file), а не отдельных адаптеров, поэтому эти тесты
идут через саму detokenize_file, а не через rewrite() напрямую.
"""
from lxml import etree

from file_detokenizer import detokenize_file

from conftest import W, read_part


def test_unknown_token_survives_character_for_character(docx_builder, session_factory, storage_dir):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>Известно: [ORG_1]. Неизвестно: [ORG_99].</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    sid = session_factory({"[ORG_1]": "ООО «Ромашка»"})

    dst, _, unresolved = detokenize_file(src, sid, storage_dir=storage_dir)

    assert unresolved == ["[ORG_99]"]
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    assert root.find(f".//{{{W}}}t").text == "Известно: ООО «Ромашка». Неизвестно: [ORG_99]."


def test_repeated_unknown_token_deduplicated_to_single_entry(docx_builder, session_factory, storage_dir):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>[ORG_99] и снова [ORG_99], и ещё раз [ORG_99].</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    sid = session_factory({})

    dst, _, unresolved = detokenize_file(src, sid, storage_dir=storage_dir)

    assert unresolved == ["[ORG_99]"]


def test_unresolved_order_is_first_appearance(docx_builder, session_factory, storage_dir):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>[ZEBRA_1] потом [ALPHA_2] потом снова [ZEBRA_1] потом [MID_3].</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    sid = session_factory({})

    dst, _, unresolved = detokenize_file(src, sid, storage_dir=storage_dir)

    assert unresolved == ["[ZEBRA_1]", "[ALPHA_2]", "[MID_3]"]


def test_no_tokens_in_file_gives_empty_unresolved_and_intact_copy(docx_builder, session_factory, storage_dir):
    xml = f"""<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>Обычный текст без единого токена.</w:t></w:r></w:p>
</w:body></w:document>""".encode("utf-8")
    src = docx_builder("src.docx", xml)
    sid = session_factory({"[ORG_1]": "ООО «Ромашка»"})

    dst, _, unresolved = detokenize_file(src, sid, storage_dir=storage_dir)

    assert unresolved == []
    root = etree.fromstring(read_part(dst, "word/document.xml"))
    assert root.find(f".//{{{W}}}t").text == "Обычный текст без единого токена."
