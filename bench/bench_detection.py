# -*- coding: utf-8 -*-
"""Замер времени детекции (этап A волны 2): основной посегментный проход detect_ner
и граничный проход B3 (_detect_boundary_entities). Один и тот же скрипт гоняется на
базисе (yargy-сканер) и после этапа A (гибрид) в ОТДЕЛЬНЫХ процессах — сравниваем
абсолюты на одной машине.

Документ синтетический, но по составу близкий к договору: ~85% строк без адреса
(проза, реквизиты, ФИО, суммы) и ~15% адресных — чтобы «выигрыш от снятия yargy со
сканирования» мерился на реалистичной плотности анкоров, а не на сплошных адресах.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import SourceDocument, TextSegment

CONFIG = os.path.join(os.path.dirname(__file__), "..", "entity_types.yaml")

# Блок строк, повторяемый до нужного объёма. 15% строк — адреса.
_BLOCK = [
    "Настоящий договор регулирует отношения сторон по поставке товара.",
    "Стороны договорились о нижеследующем в соответствии с законодательством.",
    "Поставщик обязуется передать товар в согласованные сроки и надлежащего качества.",
    "Покупатель обязуется принять и оплатить товар в порядке, установленном договором.",
    "ООО «Ромашка», ИНН 7707083893, ОГРН 1027700132195, КПП 770701001.",
    "В лице генерального директора Иванова Ивана Ивановича, действующего на основании устава.",
    "Общая сумма договора составляет 1 500 000 рублей, включая НДС.",
    "Оплата производится в течение 10 банковских дней с даты подписания акта.",
    "г. Москва, ул. Ленина, д. 5, оф. 12",                        # адрес
    "Юридический адрес: 350000, Краснодарский край, г. Краснодар, ул. Мира, 40",  # адрес
]


def build_doc(n_segments: int) -> SourceDocument:
    segments = []
    for i in range(n_segments):
        line = _BLOCK[i % len(_BLOCK)]
        segments.append(TextSegment(
            id=f"l{i}", text=line, source_type="txt_line",
            metadata={"line_index": i},
        ))
    return SourceDocument(segments=segments, source_format="txt", source_path="<bench>")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    doc = build_doc(n)
    from ner_detector import detect_ner

    # прогрев моделей (первый вызов тянет ленивую инициализацию тэггера)
    _warm = build_doc(10)
    detect_ner(_warm, CONFIG)

    t0 = time.perf_counter()
    ents = detect_ner(doc, CONFIG)
    t1 = time.perf_counter()
    main_s = t1 - t0
    n_addr = sum(1 for e in ents if e.entity_type == "ADDRESS")

    # B3-проход в том виде, как его зовёт tokenizer.tokenize
    from tokenizer import _detect_boundary_entities
    t0 = time.perf_counter()
    _detect_boundary_entities(doc, CONFIG)
    t1 = time.perf_counter()
    b3_s = t1 - t0

    print(f"segments        : {n}")
    print(f"detect_ner main : {main_s:8.2f} s  ({main_s/n*1000:6.2f} ms/seg)  ADDRESS={n_addr}")
    print(f"B3 boundary pass: {b3_s:8.2f} s  ({b3_s/n*1000:6.2f} ms/seg)")
    print(f"TOTAL detection : {main_s + b3_s:8.2f} s")


if __name__ == "__main__":
    main()
