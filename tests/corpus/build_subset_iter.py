# -*- coding: utf-8 -*-
"""
build_subset_iter.py — ОДНОРАЗОВЫЙ построитель состава итерационного среза.

Результат — `tests/corpus/subset_iter.json`, СПИСОК doc_id. Инструменты этот
список читают, а НЕ пересчитывают: пересчёт при каждом запуске означал бы, что
состав среза меняется вместе с любой правкой gold или сканера структуры, и
сравнение с замороженным baseline среза теряет смысл (docs/archive/reports/PERF_REPORT.md §П.2.2).

Процедура (детерминированная, без произвольных решений):
  1. принудительно включить `subset_lib.BOUNDARY_DOC_IDS` (4 документа);
  2. принудительно добрать по одному документу на каждый из 14 суффиксов
     мутаций (7 трюков x 2 маркера m1337/m2718) — требование «все
     adversarial-мутации по обоим маркерам», которое жадность иначе может
     срезать, взяв один документ, закрывающий сразу несколько классов;
  3. жадно закрывать остаток универсума страт (`subset_lib.doc_classes`),
     на каждом шаге беря документ с максимальным приростом покрытия;
     ничья разрешается по `doc_id` (лексикографически) — поэтому результат
     не зависит от порядка чтения gold;
  4. добрать contract_type, оставшиеся без представителя, и оба формата
     (после шага 3 они, как правило, уже покрыты — шаг страхующий);
  5. проверить потолок `MAX_SUBSET_DOCS` (28 — см. обоснование в subset_lib.py)
     и 100% покрытие.

Запуск (нужен только при изменении корпуса или универсума страт):
    venv/Scripts/python.exe tests/corpus/build_subset_iter.py
    venv/Scripts/python.exe tests/corpus/build_subset_iter.py --dry-run
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import subset_lib as SL  # noqa: E402


def _forced(gold):
    """Шаг 1: только граничные документы.

    Отдельного принудительного добора «по документу на каждый суффикс мутации»
    НЕТ намеренно: класс ('mut', суффикс) входит в универсум страт, поэтому все
    14 суффиксов (7 трюков x m1337/m2718) закрывает сама жадность — и закрывает
    дешевле, выбирая документы, которые попутно вносят и другие классы.
    Принудительный добор давал те же 14 мутаций, но составом в 30 документов
    против 25 разрешённых: он брал «первый по doc_id», не глядя на прирост.
    """
    by_id = {d["doc_id"]: d for d in gold}
    return [i for i in SL.BOUNDARY_DOC_IDS if i in by_id]


def build(gold, structure):
    by_id = {d["doc_id"]: d for d in gold}
    want = SL.universe(gold, structure)
    picked = _forced(gold)
    got = set()
    for i in picked:
        got |= SL.doc_classes(by_id[i], structure)

    # Шаг 3 — жадность.
    while got != want:
        best, best_gain = None, 0
        for doc_id in sorted(by_id):
            if doc_id in picked:
                continue
            gain = len(SL.doc_classes(by_id[doc_id], structure) - got)
            if gain > best_gain:
                best, best_gain = doc_id, gain
        if best is None:
            break
        picked.append(best)
        got |= SL.doc_classes(by_id[best], structure)

    # Шаг 4 — страховка по contract_type и формату.
    have_ct = {by_id[i]["contract_type"] for i in picked}
    for ct in sorted({d["contract_type"] for d in gold}):
        if ct in have_ct:
            continue
        cand = sorted(d["doc_id"] for d in gold if d["contract_type"] == ct)
        picked.append(cand[0])
        have_ct.add(ct)
    have_fmt = {by_id[i]["format"] for i in picked}
    for fmt in sorted({d["format"] for d in gold}):
        if fmt in have_fmt:
            continue
        picked.append(sorted(d["doc_id"] for d in gold if d["format"] == fmt)[0])

    # Шаг 5 — прополка. Жадность закрывает классы по одному документу за шаг и
    # потому оставляет избыточные: документ, взятый ради двух редких классов,
    # позже может оказаться целиком перекрыт более поздними находками. Проход
    # детерминирован: идём в
    # ОБРАТНОМ порядке отбора (последние взятые — самые вероятные дубликаты),
    # выбрасываем документ, только если покрытие остаётся полным. Граничные
    # документы неприкосновенны: они в срезе не за покрытие классов, а за
    # исторически пропущенные дефекты.
    keep = list(picked)
    for doc_id in reversed(picked):
        if doc_id in SL.BOUNDARY_DOC_IDS:
            continue
        trial = [i for i in keep if i != doc_id]
        rest = set()
        for i in trial:
            rest |= SL.doc_classes(by_id[i], structure)
        if rest >= want:
            keep = trial
    return sorted(set(keep))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="не записывать файл")
    args = ap.parse_args()

    gold = SL.load_gold()
    structure = SL.load_structure()
    want = SL.universe(gold, structure)
    doc_ids = build(gold, structure)
    got, missing = SL.coverage(doc_ids, gold, structure)

    print("универсум страт: %d" % len(want))
    for kind in ("tct", "feat", "ct", "parties", "fmt", "struct", "struct_ext", "mut"):
        n = sum(1 for c in want if c[0] == kind)
        k = sum(1 for c in got if c[0] == kind)
        print("  %-11s %3d/%3d" % (kind, k, n))
    print("документов в срезе: %d (потолок %d)" % (len(doc_ids), SL.MAX_SUBSET_DOCS))
    by_id = {d["doc_id"]: d for d in gold}
    for i in doc_ids:
        d = by_id[i]
        st = structure["docs"].get(i)
        print("  %-34s %-4s %-12s %s" % (i, d["format"], d["parties"],
                                         st["config"] if st else "-"))
    if missing:
        print("!!! НЕ ПОКРЫТО: %d" % len(missing))
        for c in sorted(missing, key=repr):
            print("    " + SL.describe_class(c))
        sys.exit(1)
    if len(doc_ids) > SL.MAX_SUBSET_DOCS:
        print("!!! состав превысил потолок %d" % SL.MAX_SUBSET_DOCS)
        sys.exit(1)
    print("покрытие 100%, потолок соблюдён.")

    if args.dry_run:
        return
    payload = {
        "_comment": ("ЗАМОРОЖЕННЫЙ состав итерационного среза. Читается "
                     "subsample.py (всегда), etalon.py/acceptance.py (--subset). "
                     "gate.py его НЕ читает и гоняет весь корпус. Пересобирать "
                     "только build_subset_iter.py и только вместе с "
                     "results_iter_baseline.json."),
        "n_docs": len(doc_ids),
        "n_classes_covered": len(got),
        "n_classes_total": len(want),
        "generator": "tests/corpus/build_subset_iter.py",
        "structure_source": "tests/corpus/structure.json",
        "doc_ids": doc_ids,
    }
    with open(SL.SUBSET_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("записано: %s" % SL.SUBSET_PATH)


if __name__ == "__main__":
    main()
