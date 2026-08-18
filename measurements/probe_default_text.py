# -*- coding: utf-8 -*-
"""Живой прогон одного документа: что видно в анонимном тексте при
настройках ПО УМОЛЧАНИЮ на месте паспорта, перехваченного типом INN."""
import os, sys, io, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
import run_measurement as RM
from extractor import extract
from pipeline import run_detection
from tokenizer import resolve_for_masking, apply_masking, build_plain_text
import type_policy

CONFIG = os.path.join(ROOT, "entity_types.yaml")
w = io.open(os.path.join(HERE, "probe_default_text.txt"), "w", encoding="utf-8")

for did, needle in [("lease_0003", "174310"), ("loan_0009", "537618")]:
    path = os.path.join(ROOT, "tests", "corpus", "docs", did + ".docx")
    if not os.path.exists(path):
        cand = [p for p in os.listdir(os.path.join(ROOT, "tests", "corpus", "docs"))
                if p.startswith(did + ".")]
        path = os.path.join(ROOT, "tests", "corpus", "docs", cand[0]) if cand else None
    doc = extract(path)
    ents = run_detection(doc, CONFIG)
    resolved = resolve_for_masking(doc, ents, CONFIG)
    anon_def, kept_def = apply_masking(doc, resolved, CONFIG, type_policy.PERSONAL)
    anon_max, kept_max = apply_masking(doc, resolved, CONFIG, type_policy.MAXIMUM)
    plain = build_plain_text(doc)
    w.write("\n########## %s  (файл %s) ##########\n" % (did, os.path.basename(path)))
    w.write("набор PERSONAL (умолчание) = %s\n" % (sorted(type_policy.PERSONAL),))
    i = plain.find(needle)
    w.write("\n-- ИСХОДНЫЙ ТЕКСТ вокруг значения --\n%r\n" % plain[max(0, i-90):i+40])
    for tag, txt in (("МАКСИМУМ", anon_max), ("ПО УМОЛЧАНИЮ", anon_def)):
        j = txt.find(needle)
        w.write("\n-- анонимный текст, %s --\n" % tag)
        if j >= 0:
            w.write("  ЗНАЧЕНИЕ НАЙДЕНО В ОТКРЫТОМ ВИДЕ: %r\n" % txt[max(0, j-90):j+40])
        else:
            k = txt.find("Паспорт")
            if k < 0:
                k = txt.lower().find("паспорт")
            w.write("  значения нет; окрестность слова «паспорт»: %r\n" % txt[max(0, k-30):k+120])
    w.write("\n-- какими токенами закрыто значение --\n")
    for tag, kept in (("МАКСИМУМ", kept_max), ("ПО УМОЛЧАНИЮ", kept_def)):
        hits = [e for e in kept if needle.replace(" ", "") in
                "".join(ch for ch in (getattr(e, "text", "") or "") if ch.isdigit())]
        w.write("  %s: %s\n" % (tag, [(getattr(e, "type", None), getattr(e, "token", None),
                                       getattr(e, "text", None)) for e in hits]))
w.close()
print("ok")
