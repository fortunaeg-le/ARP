# -*- coding: utf-8 -*-
"""ЭТАП T-ARB — поимённый дифф двух дампов dump_masks.py (до/после правки).

Каждая маска — кортеж (entity_type, segment_id, start, end, original_text).
Для каждого документа считает симметрическую разность множеств и печатает
ВСЕ расхождения без исключений и без фильтра по классу — классификация
(«это B-ADDR-HOMONYM или нет») делается ГЛАЗАМИ по печати, не молча.
"""
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    before = load(sys.argv[1])
    after = load(sys.argv[2])
    out_path = sys.argv[3] if len(sys.argv) > 3 else None
    lines = []

    doc_ids = sorted(set(before) | set(after))
    total_removed = total_added = 0
    docs_with_diff = 0

    for doc_id in doc_ids:
        b = {tuple(m) for m in before.get(doc_id, [])}
        a = {tuple(m) for m in after.get(doc_id, [])}
        removed = sorted(b - a)
        added = sorted(a - b)
        if not removed and not added:
            continue
        docs_with_diff += 1
        total_removed += len(removed)
        total_added += len(added)
        lines.append(f"=== {doc_id} ===")
        for m in removed:
            lines.append(f"  - {m}")
        for m in added:
            lines.append(f"  + {m}")

    lines.append("")
    lines.append(f"Документов с расхождением: {docs_with_diff} / {len(doc_ids)}")
    lines.append(f"Масок ушло (было, не стало): {total_removed}")
    lines.append(f"Масок пришло (не было, стало): {total_added}")

    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
