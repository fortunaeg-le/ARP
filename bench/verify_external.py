# -*- coding: utf-8 -*-
"""Внешний набор (НЕ golden) + проверка избыточного захвата в контексте."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from models import SourceDocument, TextSegment
from ner_detector import detect_ner
CFG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")

def spans(text):
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path="<x>")
    out=[]
    for e in detect_ner(doc, CFG):
        out.append((e.entity_type, e.start, e.end, e.original_text))
    return out

def cov(spans_, gs, ge):
    c=[False]*(ge-gs)
    for t,s,e,_ in spans_:
        if t!="ADDRESS": continue
        for i in range(max(s,gs),min(e,ge)): c[i-gs]=True
    return all(c)

# --- 1. Внешний набор адресов (другие форматы/регионы/сокращения, НЕ из golden) ---
EXTERNAL = [
    "Респ. Башкортостан, г. Стерлитамак, ул. Артёма, д. 21",
    "г. Калининград, Ленинский проспект, 30-32",
    "628012, ХМАО-Югра, г. Ханты-Мансийск, ул. Мира, 14",
    "Чеченская Республика, г. Грозный, пр. Путина, 1",
    "г. Симферополь, ул. Севастопольская, д. 8",
    "г. Апатиты, Мурманская обл., ул. Ферсмана, 40",
    "693000, г. Южно-Сахалинск, Коммунистический пр-т, 32",
    "г. Магадан, ул. Пролетарская, д. 11, кв. 3",
    "Ямало-Ненецкий АО, г. Новый Уренгой, мкр. Оптимистов, 7",
    "г. Петропавловск-Камчатский, ул. Ленинская, 62",
    "Кемеровская область — Кузбасс, г. Кемерово, пр. Советский, 60",
    "г. Владимир, Октябрьский проспект, дом 47",
    "г. Курск, ул. Ленина, д. 2, литера Б",
    "Республика Крым, г. Ялта, ул. Московская, 1а",
    "г. Архангельск, наб. Северной Двины, 30",
]
print("=== ВНЕШНИЙ НАБОР (15 адресов, весь text = адрес) ===")
det=full=0
for t in EXTERNAL:
    sp=spans(t)
    gs,ge=0,len(t.rstrip())
    a=[x for x in sp if x[0]=="ADDRESS"]
    d=any(x[0]=="ADDRESS" for x in sp)
    f=cov(sp,gs,ge)
    det+=d; full+=f
    flag="" if f else "  <<< НЕ ЗАКРЫТ"
    print(f"  det={int(d)} full={int(f)}{flag}  {t!r}")
    if not f:
        print(f"        ADDRESS={[x[3] for x in a]}")
print(f"  ИТОГО внешний: detect={det}/15  full-closure={full}/15")

# --- 2. Избыточный захват: адрес + следующее осмысленное поле/имя ---
print("\n=== ИЗБЫТОЧНЫЙ ЗАХВАТ В КОНТЕКСТЕ ===")
CONTEXT = [
    ("г. Москва, ул. Ленина, д. 5 Директор Петров Сергей Иванович",
     "г. Москва, ул. Ленина, д. 5"),
    ("Адрес: г. Казань, ул. Баумана, д. 44 Телефон 8-900-123-45-67",
     "г. Казань, ул. Баумана, д. 44"),
    ("г. Уфа, ул. Ленина, д. 8 Иванов Иван Иванович подписал договор",
     "г. Уфа, ул. Ленина, д. 8"),
    ("Поставка в г. Сочи, ул. Мира, 3 Оплата по счёту в течение 5 дней",
     "г. Сочи, ул. Мира, 3"),
    ("г. Пермь, ул. Ленина, 50 ООО «Ромашка» является поставщиком",
     "г. Пермь, ул. Ленина, 50"),
    ("г. Тула, пр-т Ленина, 2 Ответственный Сидоров",
     "г. Тула, пр-т Ленина, 2"),
]
for text, addr in CONTEXT:
    sp=spans(text)
    a=[x for x in sp if x[0]=="ADDRESS"]
    ge_addr = text.index(addr)+len(addr)
    over=[]
    for t,s,e,ot in a:
        if e>ge_addr+1:
            over.append((ot, text[ge_addr:e]))
    print(f"  {text!r}")
    print(f"     ADDRESS={[x[3] for x in a]}")
    for ot,tail in over:
        print(f"     >>> ЗАХВАТ ЗА АДРЕСОМ: {tail!r}")
    print(f"     PER/ORG={[(x[0],x[3]) for x in sp if x[0] in ('PERSON','ORG')]}")
