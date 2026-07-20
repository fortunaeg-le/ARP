"""A0 bake-off, шаг 1 — ЧЕСТНЫЙ ВХОД (проектный venv, natasha).

Извлекает dogovor.docx РОВНО как штатный пайплайн (extract -> эвристика регистра),
затем для каждого сегмента строит НОРМАЛИЗОВАННУЮ копию (detection_view: омоглифы/
невидимые/дефисы + detection_text-регистр) — тот самый текст, который видят детекторы.

Выход:
  input/normalized_segments.json  — [{seg_id, source_type, orig_text, norm_text}]
                                     единый вход для GLiNER и Natasha.
  dumps/natasha_dogovor.json      — сущности Natasha (PER/ORG/ADDRESS) по норм-входу
                                     + латентность полного прогона.

Ничего в src/ не трогает. Запуск: venv/Scripts/python.exe experiments/a0_gliner/01_prepare_input.py
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from extractor import extract                      # noqa: E402
from normalizer import detection_view              # noqa: E402
from regex_detector import detect_regex            # noqa: E402
from ner_detector import detect_ner                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCX = os.path.join(_ROOT, "dogovor.docx")
CONFIG = os.path.join(_ROOT, "entity_types.yaml")


def main():
    doc = extract(DOCX)

    # --- Честный вход: нормализованный текст каждого сегмента ---
    segments = []
    for seg in doc.segments:
        if not seg.text:
            continue
        norm_text, _offset = detection_view(seg)
        segments.append({
            "seg_id": seg.id,
            "source_type": seg.source_type,
            "orig_text": seg.text,
            "norm_text": norm_text,
        })

    with open(os.path.join(HERE, "input", "normalized_segments.json"), "w",
              encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=1)

    total_norm_chars = sum(len(s["norm_text"]) for s in segments)
    print(f"Сегментов: {len(segments)}, символов норм-текста: {total_norm_chars}")

    # --- Natasha по ТОМУ ЖЕ нормализованному входу (штатный путь пайплайна) ---
    # detect_ner внутри сам вызывает detection_view -> работает по норм-тексту.
    # Замеряем латентность именно детекции (regex-карта нужна как barrier для адреса).
    t0 = time.perf_counter()
    regex_entities = detect_regex(doc, CONFIG)
    t1 = time.perf_counter()
    ner_entities = detect_ner(doc, CONFIG, regex_entities=regex_entities)
    t2 = time.perf_counter()

    ner_dump = []
    for e in ner_entities:
        ner_dump.append({
            "seg_id": e.segment_id,
            "type": e.entity_type,      # PERSON | ORG | ADDRESS
            "text": e.original_text,
            "start": e.start,
            "end": e.end,
        })

    out = {
        "engine": "natasha-1.6.0 (штатный detect_ner по норм-входу)",
        "latency_regex_s": round(t1 - t0, 3),
        "latency_ner_s": round(t2 - t1, 3),
        "latency_total_s": round(t2 - t0, 3),
        "n_entities": len(ner_dump),
        "by_type": _counts([d["type"] for d in ner_dump]),
        "entities": ner_dump,
    }
    with open(os.path.join(HERE, "dumps", "natasha_dogovor.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"Natasha NER: {len(ner_dump)} сущн., по типам {out['by_type']}")
    print(f"Латентность NER (detect_ner): {out['latency_ner_s']} c "
          f"(+ regex {out['latency_regex_s']} c)")


def _counts(items):
    c = {}
    for x in items:
        c[x] = c.get(x, 0) + 1
    return dict(sorted(c.items()))


if __name__ == "__main__":
    main()
