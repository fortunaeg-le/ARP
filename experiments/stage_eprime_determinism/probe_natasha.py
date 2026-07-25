# -*- coding: utf-8 -*-
"""Изолированный проб: детерминизм самого Natasha NewsNERTagger на большом
куске текста, вне пайплайна SHIFRATOR (искать причину именно в NER-слое)."""
import sys, os, hashlib, random
sys.path.insert(0, r"C:\Jesus\ARP\src")

from natasha import Segmenter, Doc, NewsEmbedding, NewsNERTagger

segmenter = Segmenter()
emb = NewsEmbedding()
tagger = NewsNERTagger(emb)

rnd = random.Random(1)
WORDS = ("Воронцов Александр Дмитриевич проживает по адресу город Новоуральск "
         "улица Полевая дом 12 согласно Федеральному закону об акционерных "
         "обществах ИНН 7701234567 ОГРН 1027700123456 стороны договора "
         "Общество с ограниченной ответственностью Меридиан заключили ").split()

N_WORDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
N_RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 10

text = " ".join(rnd.choice(WORDS) for _ in range(N_WORDS))
print("text length (chars):", len(text))

hashes = []
span_counts = []
for i in range(N_RUNS):
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(tagger)
    spans = [(s.start, s.stop, s.type) for s in doc.spans]
    h = hashlib.sha256(repr(spans).encode()).hexdigest()
    hashes.append(h)
    span_counts.append(len(spans))
    print("run %2d: spans=%d hash=%s" % (i, len(spans), h[:16]))

print("\nunique hashes:", len(set(hashes)), "span_counts:", span_counts)
