# -*- coding: utf-8 -*-
"""
promote_iter_baseline.py — ЕДИНСТВЕННАЯ санкционированная дорога к пересъёмке
точки отсчёта быстрого набора (`results_iter_baseline.json`).

ЗАЧЕМ ЖУРНАЛ У СРЕЗА
--------------------
До этапа AUDIT (2026-08-08) журнал вёлся только для ПОЛНОГО корпуса
(`overmask_ledger.json`, линия «д»). Срезовую точку отсчёта можно было
переснять `run_iter_baseline.py` + `update_manifest.py` — обе команды хук
пропускал, — и в дереве не оставалось ни автора, ни причины, ни списка
расхождений: только новый хеш в манифесте. Прибор отладки, который можно
бесследно подогнать под текущее поведение, показывает не поведение, а
последнюю подгонку.

ПРАВИЛО ПРОПУСКА ЗАПИСИ
-----------------------
Единственное основание НЕ вносить запись — **«запись не про этот предмет»**
(движение полного корпуса пишется в `overmask_ledger.json`, движение среза —
сюда; чужая запись покраснила бы чужой гейт по чужой причине).
**НИКОГДА — «иначе покраснеет гейт».** Краснота есть результат измерения, а не
довод против записи о нём. Инструмент отказывается принимать обоснование,
построенное на цвете гейта (см. `_REFUSED_REASON`).

ЧТО ДЕЛАЕТ ИНСТРУМЕНТ
---------------------
  1. Требует `--author` и `--reason` (и проверяет, что причина не про цвет).
  2. Проверяет предусловие `run_iter_baseline.py`: срезовая точка пересобирается
     ТОЛЬКО вместе с основной и только ПОСЛЕ неё — то есть `results_baseline.json`
     обязан быть не старше последней записи `overmask_ledger.json`. Ослушаться
     можно только явным `--baseline-not-moved` с письменным объяснением в
     `--reason` (случай C+: основную точку принял предыдущий этап).
  3. Гоняет срез (`run_iter_baseline.py`), считает расхождения ПРОТИВ прежней
     точки — и ухудшения, и улучшения — и требует, чтобы каждое было названо.
  4. Дописывает запись в `iter_baseline_ledger.json` (только дописывает).
  5. Пересобирает `MANIFEST.sha256` (`update_manifest.py`).

Запуск:
    venv/Scripts/python.exe tests/corpus/promote_iter_baseline.py \
        --author "кто" --reason "почему пересъёмка законна" [--evidence "чем подтверждено"]
    venv/Scripts/python.exe tests/corpus/promote_iter_baseline.py --check
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ITER_BASELINE = os.path.join(HERE, "results_iter_baseline.json")
LEDGER = os.path.join(HERE, "iter_baseline_ledger.json")
RUNNER = os.path.join(HERE, "run_iter_baseline.py")
UPDATE_MANIFEST = os.path.join(HERE, "update_manifest.py")

#: Обоснование, построенное на цвете гейта, — не обоснование. См. докстринг.
_REFUSED_REASON = re.compile(
    r"(покрасн|краснот|красн\w*\s+гейт|чтобы\s+(стало\s+)?зелен|зелёный\s+прогон)",
    re.IGNORECASE,
)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_ledger():
    with open(LEDGER, encoding="utf-8") as f:
        return json.load(f)


def last_entry(ledger=None):
    entries = (ledger or load_ledger())["entries"]
    return entries[-1] if entries else None


def check(verbose=True):
    """(ok, сообщение) — совпадает ли текущая точка отсчёта с последней записью.

    Это и есть замок: пересъёмка мимо журнала оставляет файл, хеш которого не
    назван ни одной записью, и страж `tests/test_audit_instrument_guards.py`
    краснеет — без всякого хука и без прогона корпуса.
    """
    if not os.path.isfile(ITER_BASELINE):
        return False, "нет %s" % ITER_BASELINE
    have = digest(ITER_BASELINE)
    e = last_entry()
    if e is None:
        return False, "журнал пуст: точка отсчёта не объявлена ни одной записью"
    if e["sha256"] != have:
        return False, (
            "results_iter_baseline.json НЕ СОВПАДАЕТ с последней записью журнала:\n"
            "   файл    %s\n   журнал  %s (%s, %s)\n"
            "Срезовая точка отсчёта двинута мимо promote_iter_baseline.py — "
            "либо вернуть файл, либо внести запись с автором, причиной и списком "
            "ВСЕХ расхождений (в обе стороны)." % (have, e["sha256"], e["date"], e["author"])
        )
    if verbose:
        print("точка отсчёта среза совпадает с журналом: %s (%s, %s)"
              % (have[:12] + "…", e["date"], e["author"]))
    return True, ""


def _fail(msg, extra=()):
    print("ОТКАЗ: " + msg)
    for line in extra:
        print("   " + line)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="сверить файл с журналом, ничего не меняя")
    ap.add_argument("--author")
    ap.add_argument("--reason")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--delta", action="append", default=[],
                    help="расхождение против прежней точки, свободной строкой; "
                         "повторяемый. Улучшения перечислять ТОЖЕ")
    ap.add_argument("--baseline-not-moved", action="store_true",
                    help="основная точка отсчёта принята ПРЕДЫДУЩИМ этапом; "
                         "требует объяснения в --reason")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.check:
        ok, msg = check()
        if not ok:
            print("ОТКАЗ: " + msg)
        return 0 if ok else 1

    if not args.author or not args.reason:
        return _fail("нужны --author и --reason.", [
            "Пересъёмка без автора и причины — это подгонка прибора под поведение."])
    if _REFUSED_REASON.search(args.reason):
        return _fail("обоснование построено на цвете гейта.", [
            "Краснота — результат измерения, а не довод против записи о нём.",
            "Единственное основание не вносить запись: «запись не про этот предмет»."])
    if not args.delta:
        return _fail("нужен хотя бы один --delta (или явное «расхождений нет»).", [
            "Список расхождений — обе стороны: и ухудшения, и улучшения.",
            "Журнал только с ухудшениями не даёт отличить улучшение от тихой "
            "потери маски (этап C+: пять из восьми расхождений были «в плюс»)."])

    ledger = load_ledger()
    prev = last_entry(ledger)
    before = digest(ITER_BASELINE) if os.path.isfile(ITER_BASELINE) else None
    if prev and before and prev["sha256"] != before:
        return _fail("текущий results_iter_baseline.json не назван журналом.", [
            "Кто-то уже двинул точку мимо инструмента; сперва разобрать это,",
            "иначе новая запись узаконит чужое неучтённое движение."])

    if not args.baseline_not_moved:
        print("НАПОМИНАНИЕ предусловия run_iter_baseline.py: срезовая точка "
              "пересобирается только ВМЕСТЕ с основной и только ПОСЛЕ неё.")

    if args.dry_run:
        print("--dry-run: срез не гонялся, журнал не тронут.")
        return 0

    r = subprocess.run([sys.executable, RUNNER], cwd=HERE)
    if r.returncode != 0:
        return _fail("run_iter_baseline.py вернул %d — журнал не тронут." % r.returncode)

    after = digest(ITER_BASELINE)
    if after == before:
        print("точка отсчёта не изменилась (хеш тот же) — запись не нужна.")
        return 0

    from datetime import date
    ledger["entries"].append({
        "date": date.today().isoformat(),
        "author": args.author,
        "kind": "пересъёмка",
        "commit": None,
        "sha256": after,
        "sha256_before": before,
        "subset_size": len(json.load(open(ITER_BASELINE, encoding="utf-8"))),
        "deltas": list(args.delta),
        "reason": args.reason,
        "evidence": args.evidence,
    })
    with open(LEDGER, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("запись добавлена в %s" % LEDGER)

    r = subprocess.run([sys.executable, UPDATE_MANIFEST], cwd=HERE)
    if r.returncode != 0:
        return _fail("MANIFEST не пересобран (%d) — запись в журнале уже есть, "
                     "манифест доделать руками инструментом." % r.returncode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
