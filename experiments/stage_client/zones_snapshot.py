# -*- coding: utf-8 -*-
"""Слепок вердиктов сканера зон по ВСЕМ .docx обоих корпусов.

Нужен, чтобы доказать узость правки части 2 механизмом, а не рассуждением:
слепок снимается на СТАРОМ коде (`C:\\Jesus\\ARP\\src`) и на НОВОМ
(`C:\\Jesus\\ARP_CLIENT\\src`) и сличается побайтно. Совпали — чтение
документов не изменилось, значит и вход замера прежний.

Запуск: <python> zones_snapshot.py <корень с src> <корень с корпусами> <выход.json>
"""
import json
import os
import sys

SRC_ROOT, CORPUS_ROOT, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(1, os.path.join(SRC_ROOT, "src"))

from unread_zones import scan_unread_zones  # noqa: E402


def main() -> None:
    docs = []
    for base in ("tests/corpus", "tests/corpus_v2", "tests/fixtures"):
        root = os.path.join(CORPUS_ROOT, base)
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower().endswith(".docx"):
                    full = os.path.join(dirpath, fn)
                    docs.append((os.path.relpath(full, CORPUS_ROOT).replace("\\", "/"), full))
    docs.sort()

    out = {}
    for rel, full in docs:
        try:
            zones = scan_unread_zones(full)
            out[rel] = [[z.kind, z.part, z.char_count, z.detail, z.text_preview]
                        for z in zones]
        except Exception as exc:                      # noqa: BLE001
            out[rel] = [["ОШИБКА", type(exc).__name__, 0, "", str(exc)[:200]]]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    n_zones = sum(len(v) for v in out.values())
    print(f"документов {len(out)}, зон всего {n_zones} -> {OUT}")


if __name__ == "__main__":
    main()
