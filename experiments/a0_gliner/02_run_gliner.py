"""A0 bake-off, шаг 2 — ПРОГОН GLiNER (изолированный venv_gliner, torch CPU).

Читает честный вход input/normalized_segments.json (тот же норм-текст, что видит
Natasha) и гоняет ДВЕ модели GLiNER. Порог GLiNER — чистый ПОСТ-фильтр по score,
поэтому инференс делаем ОДИН раз на сегмент при threshold=0.0, снимаем ВСЕ спаны со
score, а сетку порогов {0.1,0.3,0.5,0.7,0.9} применяем фильтрацией в шаге 3. Это
эквивалентно прогону predict_entities на каждом пороге, но один forward-pass — и
латентность меряется чисто.

Labels зафиксированы: ["организация","человек","адрес"] (основной набор).
Один альтернативный набор — англ. ["organization","person","address"] — отдельной
колонкой, НЕ вместо (разрешено ТЗ).

Выход: dumps/raw_<model>_<labelset>.json — сырьё со ВСЕМИ score + латентность.
Модели грузятся ЛОКАЛЬНО из hf_cache, никаких облачных вызовов на инференсе.

Запуск: experiments/a0_gliner/venv_gliner/Scripts/python.exe experiments/a0_gliner/02_run_gliner.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HOME", os.path.join(HERE, "hf_cache"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from gliner import GLiNER  # noqa: E402

MODELS = [
    "urchade/gliner_multi-v2.1",
    "urchade/gliner_multi_pii-v1",
]

LABEL_SETS = {
    "ru": ["организация", "человек", "адрес"],
    "en": ["organization", "person", "address"],
}

# GLiNER-метка -> тип корпуса (для зеркал против Natasha)
TO_TYPE = {
    "организация": "ORG", "человек": "PERSON", "адрес": "ADDRESS",
    "organization": "ORG", "person": "PERSON", "address": "ADDRESS",
}


def slug(name):
    return name.split("/")[-1].replace(".", "_")


def main():
    with open(os.path.join(HERE, "input", "normalized_segments.json"),
              encoding="utf-8") as f:
        segments = json.load(f)

    for model_name in MODELS:
        print(f"\n=== Загрузка {model_name} ===", flush=True)
        t_load0 = time.perf_counter()
        model = GLiNER.from_pretrained(model_name)
        model.eval()
        t_load = time.perf_counter() - t_load0
        print(f"    загружена за {t_load:.1f} c", flush=True)

        for ls_name, labels in LABEL_SETS.items():
            preds = []
            t0 = time.perf_counter()
            for seg in segments:
                text = seg["norm_text"]
                if not text.strip():
                    continue
                # threshold=0.0 -> снимаем все спаны, сетку применим позже
                spans = model.predict_entities(text, labels, threshold=0.0)
                for sp in spans:
                    preds.append({
                        "seg_id": seg["seg_id"],
                        "text": sp["text"],
                        "label": sp["label"],
                        "type": TO_TYPE.get(sp["label"], sp["label"]),
                        "score": round(float(sp["score"]), 4),
                        "start": sp["start"],
                        "end": sp["end"],
                    })
            latency = time.perf_counter() - t0

            out = {
                "model": model_name,
                "label_set": ls_name,
                "labels": labels,
                "load_s": round(t_load, 2),
                "latency_full_doc_s": round(latency, 2),
                "n_segments": len(segments),
                "n_spans_raw": len(preds),
                "predictions": preds,
            }
            path = os.path.join(HERE, "dumps",
                                f"raw_{slug(model_name)}_{ls_name}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
            print(f"    [{ls_name}] {len(preds)} спанов, полный док "
                  f"{latency:.2f} c -> {os.path.basename(path)}", flush=True)


if __name__ == "__main__":
    main()
