# ARCH-PROBE, проба 1: транзитивен ли _winner и зависит ли _resolve_overlaps
# от порядка входного списка.
#
# Гипотеза: правило T-ARB «ADDRESS сильнее голого PERSON независимо от длины»
# ломает транзитивность сравнения (ADDRESS > bare-PER по правилу, но
# ORG > ADDRESS по длине, а bare-PER > ORG по длине) => свёртка
# «глобально сильнейший» в _resolve_overlaps может давать разный результат
# на перестановках одного и того же набора сущностей.
import itertools
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from models import Entity
from tokenizer import _resolve_overlaps, _winner


def ent(etype, start, end, text, det="ner"):
    return Entity(
        id=str(uuid.uuid4()), segment_id="p0", start=start, end=end,
        original_text=text, entity_type=etype, detector=det, confidence=1.0,
    )


def main():
    # Три взаимно пересекающихся ner-кандидата на одном отрезке текста.
    # Текст-носитель (позиции условные, но original_text = срез той же длины):
    #   0         1         2
    #   0123456789012345678901234
    #   «Иваново ул Тверская д 5»
    # A: ADDRESS  [8, 23)  len 15  «ул Тверская д 5»
    # P: bare PERSON [0, 7) len 7  «Иваново» — голая фамилия без пробела/точки
    # O: ORG      [4, 20)  len 16 — длиннее всех
    # Пересечения: A∩O да, P∩O да, A∩P нет — а цикл _winner всё равно проявится
    # в свёртке? Для чистоты возьмём конфигурацию, где все трое попарно
    # пересекаются:
    A = ent("ADDRESS", 5, 20, "x" * 15)          # len 15
    P = ent("PERSON", 18, 26, "Иванченко")[0] if False else ent("PERSON", 18, 27, "Иванченко")  # len 9, без пробелов/точек
    O = ent("ORG", 0, 19, "y" * 19)              # len 19, пересекает и A, и P

    # Проверка цикла попарно:
    print("A vs P ->", _winner(A, P).entity_type, "(правило: ADDRESS бьёт голый PER)")
    print("P vs O ->", _winner(P, O).entity_type, "(ожидание по длине: ORG)")
    print("O vs A ->", _winner(O, A).entity_type, "(ожидание по длине: ORG)")

    outcomes = {}
    for perm in itertools.permutations([A, P, O]):
        res = _resolve_overlaps([e for e in perm])
        key = tuple(sorted((e.entity_type, e.start, e.end) for e in res))
        order = ",".join(e.entity_type for e in perm)
        outcomes.setdefault(key, []).append(order)

    print(f"\nРазных исходов на 6 перестановках: {len(outcomes)}")
    for key, orders in outcomes.items():
        print("  исход:", key)
        print("    порядки входа:", orders)

    # Вторая конфигурация: цикл в чистом виде A>P, P>O, O>A
    # A=ADDRESS len 8, P=bare PERSON len 12, O=ORG len 10; все пересекаются.
    A2 = ent("ADDRESS", 10, 18, "a" * 8)
    P2 = ent("PERSON", 8, 20, "Иванченковых")   # len 12 голая фамилия
    O2 = ent("ORG", 6, 16, "b" * 10)
    print("\nКонфигурация 2 (чистый цикл):")
    print("A2 vs P2 ->", _winner(A2, P2).entity_type)
    print("P2 vs O2 ->", _winner(P2, O2).entity_type)
    print("O2 vs A2 ->", _winner(O2, A2).entity_type)
    outcomes2 = {}
    for perm in itertools.permutations([A2, P2, O2]):
        res = _resolve_overlaps([e for e in perm])
        key = tuple(sorted((e.entity_type, e.start, e.end) for e in res))
        order = ",".join(e.entity_type for e in perm)
        outcomes2.setdefault(key, []).append(order)
    print(f"Разных исходов на 6 перестановках: {len(outcomes2)}")
    for key, orders in outcomes2.items():
        print("  исход:", key)
        print("    порядки входа:", orders)


if __name__ == "__main__":
    main()
