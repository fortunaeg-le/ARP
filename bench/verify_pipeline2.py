# -*- coding: utf-8 -*-
import os, sys, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from models import SourceDocument, TextSegment
from regex_detector import detect_regex
from ner_detector import detect_ner
from syntax_compound import merge_compound_entities
from tokenizer import tokenize
CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")

CASES = [
    "г. Уфа, ул. Ленина, д. 8 Иванов Иван Иванович подписал договор",
    "Адрес: г. Казань, ул. Баумана, д. 44 Телефон 8-900-123-45-67",
    "г. Казань, ул. Баумана, д. 44, ИНН 7707083893",
    "г. Пермь, ул. Ленина, 50 ООО «Ромашка» является поставщиком",
    "г. Москва, ул. Ленина, д. 5 Директор Петров Сергей Иванович",
]
out = io.StringIO()
for text in CASES:
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<p>")
    ents = detect_regex(doc, CFG) + detect_ner(doc, CFG)
    ents = merge_compound_entities(doc, ents)
    anon, final = tokenize(doc, ents, CFG)
    out.write("ВХОД:   " + text + "\n")
    out.write("ТОКЕНЫ: " + repr(sorted([(e.entity_type, e.original_text) for e in final])) + "\n")
    out.write("АНОНИМ: " + anon + "\n")
    # leak check: is any address substring left cleartext?
    for probe in ["Казань", "Баумана", "Ленина", "Уфа", "Пермь"]:
        if probe in anon:
            out.write("  !!! В АНОНИМЕ ОСТАЛОСЬ ОТКРЫТО: " + probe + "\n")
    out.write("\n")
with open(os.path.join(os.path.dirname(__file__), "pipe_out2.txt"), "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("done")
