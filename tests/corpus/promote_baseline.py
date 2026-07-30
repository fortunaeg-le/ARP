# -*- coding: utf-8 -*-
"""
promote_baseline.py — ЕДИНСТВЕННАЯ санкционированная дорога к движению точки
отсчёта регресс-гейта (`results_baseline.json`).

ЗАЧЕМ ИНСТРУМЕНТ, ЕСЛИ РАНЬШЕ ХВАТАЛО `cp`. Пересборка точки отсчёта — самое
опасное действие в проекте: она объявляет текущее поведение нормой. Пока это
делалось копированием файла, у действия не было ни автора, ни причины, ни следа
— в истории git оставался дифф на сотни тысяч строк JSON, из которого ничего не
прочитать. Отдельно про линию «д» (порча документа) владелец решил прямо:
поднять планку молча нельзя.

ЧТО ДЕЛАЕТ ИНСТРУМЕНТ (по порядку, отказ на любом шаге прекращает работу):

  1. Оценивает новый дамп ДЕЙСТВУЮЩИМ гейтом против текущей точки отсчёта.
  2. Если порча документа (линия «д») ВЫРОСЛА — требует `--author` и `--reason`
     по существу. Без них ОТКАЗЫВАЕТСЯ: это и есть «планку молча не поднять».
  3. Если красная любая ДРУГАЯ линия — отказывается вообще. Принятие регресса
     по утечке/маскировке/точности не автоматизируется: это отдельное решение
     владельца, и оно не должно проходить как побочный эффект пересборки.
  4. ДОПИСЫВАЕТ запись в журнал обоснований (`overmask_ledger.json`) — вместе с
     числами, автором, датой, причиной и подтверждением.
  5. Пишет новую точку отсчёта и пересобирает `MANIFEST.sha256` (оба baseline и
     журнал под контрольной суммой — иначе «поднять молча» осталось бы
     возможным правкой JSON).

Запуск:
    venv/Scripts/python.exe tests/corpus/promote_baseline.py <dump.json> \\
        --author "имя" --reason "почему рост законен" [--evidence "чем подтверждён"]
    ... --dry-run       # показать, что будет сделано, ничего не записывая

Дамп берётся ГОТОВЫЙ (обычно `results_gate_current.json` после `gate.py`):
инструмент корпус не гоняет — прогон и решение о его принятии это два разных
действия, и смешивать их нельзя.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gate as G          # noqa: E402
import measure_lib as ML  # noqa: E402
import overmask_ledger as OL  # noqa: E402

BASELINE = os.path.join(HERE, "results_baseline.json")
UPDATE_MANIFEST = os.path.join(HERE, "update_manifest.py")


def _fail(title, lines):
    print("\n" + "=" * 78)
    print("ПЕРЕСБОРКА ТОЧКИ ОТСЧЁТА ОТМЕНЕНА — " + title)
    print("=" * 78)
    for ln in lines:
        print("  " + ln)
    print("=" * 78)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dump", help="готовый дамп прогона (results_gate_current.json)")
    ap.add_argument("--author", default="", help="кто принимает решение")
    ap.add_argument("--reason", default="", help="почему рост порчи документа законен")
    ap.add_argument("--evidence", default="",
                    help="чем подтверждена законность (лог, разбор, поимённый список)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.dump):
        return _fail("дампа нет", ["файл не найден: %s" % a.dump])

    current = json.load(open(a.dump, encoding="utf-8"))
    baseline = json.load(open(BASELINE, encoding="utf-8"))

    base_num = OL.overmask_numbers(ML.precision_by_type(baseline))
    cur_num = OL.overmask_numbers(ML.precision_by_type(current))

    # --- журнал обязан описывать ТЕКУЩУЮ точку отсчёта -------------------- #
    drift = OL.check_against_baseline(base_num)
    if drift:
        return _fail("журнал не описывает нынешнюю точку отсчёта", drift + [
            "",
            "Это значит, что предыдущее движение планки прошло мимо этого",
            "инструмента. Сначала объясните ЕГО (допишите запись в журнал с",
            "автором и причиной), потом двигайте дальше.",
        ])

    kl_ids, _ = G._known_leaks()
    v = G.evaluate(baseline, current, kl_ids)

    grown = OL.grown_types(base_num, cur_num)
    overmask_reds = [r for r in v["regressions"] if r.startswith("(д)")]
    other_reds = [r for r in v["regressions"] if not r.startswith("(д)")]

    print("Порча документа (линия «д»): %d -> %d" % (base_num["total"], cur_num["total"]))
    for t, (b, c) in sorted(grown.items()):
        print("   ВЫРОС  %-10s %d -> %d (+%d)" % (t, b, c, c - b))
    if not grown:
        print("   роста нет")

    # --- шаг 3: чужая краснота не проходит вообще ------------------------- #
    if other_reds:
        return _fail("гейт красный не только по линии «д»", other_reds + [
            "",
            "Принятие регресса по утечке / маскировке / точности НЕ",
            "автоматизируется: это отдельное решение владельца, и оно не должно",
            "проходить побочным эффектом пересборки точки отсчёта.",
        ])

    # --- шаг 2: рост порчи требует обоснования ---------------------------- #
    if grown or overmask_reds:
        problems = OL.validate_reason(a.reason, a.author)
        if problems:
            return _fail("рост порчи документа не обоснован", problems + [
                "",
                "Выросло: " + ", ".join("%s %d -> %d" % (t, b, c)
                                        for t, (b, c) in sorted(grown.items())),
                "",
                "Линия «д» блокирующая по решению владельца, и планка не",
                "поднимается молча. Повторите запуск, назвав автора и причину:",
                '  --author "имя" --reason "почему этот рост законен"',
                "  --evidence \"чем подтверждён: лог, разбор, поимённый список масок\"",
                "",
                "Если рост НЕ законен — это находка, а не повод двигать планку.",
            ])
        kind = "поднятие уровня"
    else:
        kind = "пересборка без роста"

    date = datetime.date.today().isoformat()
    print("\nБудет записано в журнал (%s):" % os.path.basename(OL.LEDGER))
    print("  дата: %s   автор: %s   вид: %s" % (date, a.author or "—", kind))
    print("  уровень: %d %s" % (cur_num["total"], dict(sorted(cur_num["per_type"].items()))))
    if a.reason:
        print("  обоснование: %s" % a.reason)
    if a.evidence:
        print("  подтверждение: %s" % a.evidence)

    if a.dry_run:
        print("\n--dry-run: ничего не записано.")
        return 0

    OL.append(cur_num, date=date, author=a.author or "—", reason=a.reason,
              evidence=a.evidence, kind=kind)
    shutil.copyfile(a.dump, BASELINE)
    print("\nТочка отсчёта обновлена: %s" % BASELINE)

    r = subprocess.run([sys.executable, UPDATE_MANIFEST], capture_output=True,
                       text=True, encoding="utf-8")
    print((r.stdout or "").strip() or (r.stderr or "").strip())
    if r.returncode != 0:
        return _fail("MANIFEST не пересобран", [
            "точка отсчёта записана, но контрольные суммы не обновлены —",
            "запустите update_manifest.py вручную и проверьте gate.py",
        ])

    print("\nГотово. Проверьте: venv/Scripts/python.exe tests/corpus/gate.py --results %s"
          % a.dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
