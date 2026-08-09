# -*- coding: utf-8 -*-
"""Часть 3: «не прочитано» против «прочитано, но не найдено» — по факту, не по вере.

Для каждой сущности вне тела проверяем, доехало ли её значение до конвейера:
берём ровно тот текст, который видит детекция (tokenizer.build_plain_text по
extractor.extract), и ищем в нём значение эталона.
"""
import json, io, sys, pathlib
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "corpus"))
from extractor import extract              # noqa: E402
from tokenizer import build_plain_text     # noqa: E402
import measure_lib as ML                   # noqa: E402

DOCS = ROOT / "tests" / "corpus" / "docs"


def squash(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def main():
    zone = json.load(open(HERE / "zone_entities.json", encoding="utf-8"))
    dump = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))
    fmt = {d["doc_id"]: d["format"] for d in dump}
    refused = {d["doc_id"]: [z["kind"] for z in d.get("refused_zones", [])] for d in dump}
    by_doc = defaultdict(list)
    for z in zone:
        by_doc[z["doc"]].append(z)
    out = io.StringIO(); W = lambda *a: print(*a, file=out)
    res = Counter(); holes = []
    for doc_id, items in sorted(by_doc.items()):
        path = str(DOCS / (doc_id + "." + fmt[doc_id]))
        plain = squash(build_plain_text(extract(path)))
        for z in items:
            read = squash(z["text"]) in plain
            key = (z["trick"], "прочитано" if read else "НЕ прочитано",
                   "ошибка" if z["err"] != "ok" else "ok")
            res[key] += 1
            if not read and not refused.get(doc_id):
                holes.append((doc_id, z["trick"], z["type"], z["text"][:40]))
    W("# Часть 3: доезжает ли значение вне тела до детекции")
    for k, v in sorted(res.items()):
        W("  %-14s %-13s %-7s %4d" % (k[0], k[1], k[2], v))
    W()
    W("НЕ прочитано И отказ не объявлен (дыра в страже): %d" % len(holes))
    for h in holes[:15]:
        W("   %s" % (h,))
    (HERE / "zone_read.txt").write_text(out.getvalue(), encoding="utf-8")
    print("ok")


if __name__ == "__main__":
    main()
