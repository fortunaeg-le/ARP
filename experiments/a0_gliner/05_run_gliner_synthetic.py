"""A0 bake-off, шаг 5 — GLiNER по синтетической подвыборке (venv_gliner).

Читает input/synthetic_segments.json, гоняет обе модели с ОСНОВНЫМ набором labels
(ru). threshold=0.0 -> все score, сетку применяем в анализе. Дамп raw по модели.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HOME", os.path.join(HERE, "hf_cache"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from gliner import GLiNER  # noqa: E402

MODELS = ["urchade/gliner_multi-v2.1", "urchade/gliner_multi_pii-v1"]
LABELS = ["организация", "человек", "адрес"]
TO_TYPE = {"организация": "ORG", "человек": "PERSON", "адрес": "ADDRESS"}


def slug(n):
    return n.split("/")[-1].replace(".", "_")


def main():
    data = json.load(open(os.path.join(HERE, "input", "synthetic_segments.json"),
                          encoding="utf-8"))
    for model_name in MODELS:
        print(f"=== {model_name} ===", flush=True)
        model = GLiNER.from_pretrained(model_name)
        model.eval()
        preds = {}
        t0 = time.perf_counter()
        for doc_id, segs in data.items():
            dp = []
            for seg in segs:
                t = seg["norm_text"]
                if not t.strip():
                    continue
                for sp in model.predict_entities(t, LABELS, threshold=0.0):
                    dp.append({"seg_id": seg["seg_id"], "text": sp["text"],
                               "type": TO_TYPE.get(sp["label"], sp["label"]),
                               "score": round(float(sp["score"]), 4),
                               "start": sp["start"], "end": sp["end"]})
            preds[doc_id] = dp
        latency = time.perf_counter() - t0
        out = {"model": model_name, "labels": LABELS,
               "latency_total_s": round(latency, 2),
               "n_spans_raw": sum(len(v) for v in preds.values()), "by_doc": preds}
        path = os.path.join(HERE, "dumps", f"raw_synth_{slug(model_name)}.json")
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"    {out['n_spans_raw']} спанов, {latency:.1f}c -> {os.path.basename(path)}",
              flush=True)


if __name__ == "__main__":
    main()
