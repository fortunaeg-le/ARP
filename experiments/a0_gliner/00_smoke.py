"""A0 bake-off, шаг 0 — SMOKE-TEST + скачивание моделей.

Один абзац dogovor.docx через обе модели, labels на русском. Проверяем осмысленность
выхода, замеряем время одного прогона абзаца, оцениваем полный документ.
Первый from_pretrained скачивает модель в hf_cache (локально; на инференсе сети нет).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HOME", os.path.join(HERE, "hf_cache"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from gliner import GLiNER  # noqa: E402

MODELS = ["urchade/gliner_multi-v2.1", "urchade/gliner_multi_pii-v1"]
LABELS = ["организация", "человек", "адрес"]

segs = json.load(open(os.path.join(HERE, "input", "normalized_segments.json"),
                     encoding="utf-8"))
# абзац с ФИО+ИНₙ+доля (p8) и «Восход»-абзац (p31) — осмысленный тест
para = next(s for s in segs if s["seg_id"] == "p8")["norm_text"]
n_segments = len(segs)
total_chars = sum(len(s["norm_text"]) for s in segs)

for name in MODELS:
    print(f"\n=== {name} ===", flush=True)
    t0 = time.perf_counter()
    model = GLiNER.from_pretrained(name)
    model.eval()
    print(f"load+download: {time.perf_counter()-t0:.1f}s", flush=True)

    # прогрев (первый forward медленнее)
    model.predict_entities(para, LABELS, threshold=0.3)
    t1 = time.perf_counter()
    ents = model.predict_entities(para, LABELS, threshold=0.3)
    dt = time.perf_counter() - t1
    print(f"абзац p8: {repr(para[:80])}")
    for e in ents:
        print(f"   {e['label']:12s} {e['score']:.2f}  {e['text']!r}")
    print(f"время 1 абзаца: {dt*1000:.0f} мс; "
          f"оценка полного дока ({n_segments} сегм, {total_chars} симв): "
          f"~{dt*n_segments:.1f}s (грубо, по числу сегментов)")
