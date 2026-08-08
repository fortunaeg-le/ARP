# -*- coding: utf-8 -*-
"""typedef_lib.py — ГЕНЕРАТОРНАЯ сторона фабрики типов.

Читает tests/corpus_v2/typedefs/*.yaml и отдаёт generate.py то, что нужно для
вплетения нового типа в корпус: примеры значений, негативы по видам случая и
шаблоны обрамления (weave). Типы с `generator: legacy` (15 дофабричных)
пропускаются — их формы живут в values.py, как и жили.

Это ЗАКОННАЯ сторона стены: генератор и измеритель видят примеры, детектор —
никогда (он читает только собранный entity_types.yaml, где generator-секции
нет по построению; страж — tests/test_type_factory.py).
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
TYPEDEFS = os.path.join(HERE, "typedefs")


def load_factory_types():
    """[{type, status, examples, negatives, weave}] — только типы с полной
    generator-секцией, в алфавитном порядке имён (детерминизм корпуса)."""
    out = []
    if not os.path.isdir(TYPEDEFS):
        return out
    for fn in sorted(os.listdir(TYPEDEFS)):
        if not fn.endswith(".yaml"):
            continue
        with open(os.path.join(TYPEDEFS, fn), encoding="utf-8") as f:
            td = yaml.safe_load(f)
        gen = td.get("generator")
        if not isinstance(gen, dict):
            continue
        weave = gen.get("weave") or {}
        out.append({
            "type": td["type"],
            "status": td.get("status"),
            "examples": list(gen.get("examples") or []),
            "negatives": list(gen.get("negatives") or []),
            "intro": weave.get("intro", ""),
            "outro": weave.get("outro", "."),
            "neg_intro": weave.get("neg_intro", ""),
            # Ограничение по типам договора («транши живут в займе и цессии») —
            # пустой список = во все документы.
            "only_ctypes": list(weave.get("only_ctypes") or []),
        })
    return out
