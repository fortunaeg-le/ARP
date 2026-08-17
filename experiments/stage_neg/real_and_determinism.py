# -*- coding: utf-8 -*-
"""Реальный документ + детерминизм (приёмка этапа NEG).

Даёт две вещи одним прогоном, чтобы документ читался с диска один раз:
  * СКОЛЬКО МАСОК КАЖДОГО ТИПА на целевом документе владельца — сравнение с
    числами MERGE-1 (STATE §3) показывает, не потерял ли этап полноту ТАМ, ГДЕ
    ЭТО ВАЖНО, а не только на порождённом корпусе;
  * ОТПЕЧАТОК НАБОРА СПАНОВ в трёх прогонах подряд — детерминизм.

РЕАЛЬНЫЙ ТЕКСТ ОТСЮДА НЕ ВЫВОДИТСЯ ВООБЩЕ (запрет 7 корневого CLAUDE.md:
публичный репозиторий, в истории уже была утечка договора). Печатаются только
количества и хеш.

Путь к документу задаётся снаружи — переменной окружения ARP_REAL_DOC или первым
аргументом командной строки; значение по умолчанию не предусмотрено.

Запуск:
    ARP_REAL_DOC=<путь> venv/Scripts/python.exe experiments/stage_neg/real_and_determinism.py
    venv/Scripts/python.exe experiments/stage_neg/real_and_determinism.py <путь>
"""
import collections
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from extractor import extract          # noqa: E402
from pipeline import run_detection     # noqa: E402
from tokenizer import resolve_for_masking  # noqa: E402

DOC = os.environ.get("ARP_REAL_DOC") or (sys.argv[1] if len(sys.argv) > 1 else None)
CONFIG = os.path.join(ROOT, "entity_types.yaml")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def main():
    if not DOC:
        print("нужен путь к документу: задайте переменную окружения ARP_REAL_DOC=<путь> "
              "или передайте путь первым аргументом командной строки")
        return 1
    if not os.path.exists(DOC):
        print("НЕТ ДОКУМЕНТА:", DOC)
        return 1
    doc = extract(DOC)
    prints = []
    for run in range(3):
        # ЭТАП FIX-2 — считаем ПРОДУКТОВЫЙ путь целиком. run_detection —
        # только посегментная детекция; граничные окна (а с ними и якорь через
        # границу ячейки) живут в resolve_for_masking. До этого этапа замер
        # реального документа шёл по короткому пути и класс DOG-ANCHOR-SPLIT
        # был для него невидим в обе стороны.
        ents = resolve_for_masking(doc, run_detection(doc, CONFIG), CONFIG)
        by_type = collections.Counter(e.entity_type for e in ents)
        key = sorted((e.entity_type, e.start, e.end) for e in ents)
        prints.append(hashlib.sha256(repr(key).encode()).hexdigest()[:16])
        if run == 0:
            print("=== МАСОК ПО ТИПАМ НА ЦЕЛЕВОМ ДОКУМЕНТЕ ===")
            for t in sorted(by_type):
                print("  %-12s %4d" % (t, by_type[t]))
            print("  %-12s %4d" % ("ВСЕГО", sum(by_type.values())))
    print("\n=== ДЕТЕРМИНИЗМ: отпечаток набора спанов, 3 прогона ===")
    print("  ", " ".join(prints), "->", "СОВПАЛИ" if len(set(prints)) == 1 else "РАЗОШЛИСЬ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
