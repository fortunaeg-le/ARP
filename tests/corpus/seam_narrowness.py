# -*- coding: utf-8 -*-
"""seam_narrowness.py — ДОКАЗАТЕЛЬСТВО УЗОСТИ правки «немаскируемый шов» (этап REG).

Правка прибора обязана быть доказана, а не объявлена. Скрипт сличает ДВА дампа
прогона корпуса, снятых на ОДНОМ И ТОМ ЖЕ коде системы (`src/` между ними не
менялся), и отвечает на один вопрос: изменилось ли хоть что-нибудь, кроме
недобора границ на символах шва.

    venv/Scripts/python.exe tests/corpus/seam_narrowness.py \
        tests/corpus/results_baseline.json tests/corpus/results_gate_current.json

Четыре проверки, каждая роняет вывод с ненулевым кодом:

  1. ВСЁ, КРОМЕ `bnd`, СОВПАДАЕТ. Записи документов сравниваются целиком после
     удаления поля `bnd` у каждой эталонной сущности: маски, ложные
     срабатывания, утечка (v1 и v2), found/exact/full, round-trip, подавления,
     непрочитанные зоны. Расхождение здесь означает, что правка задела не то,
     что обещала, — или что дампы сняты на разном коде системы.
  2. ПЕРЕБОР НЕ ДВИНУЛСЯ НИ У ОДНОЙ СУЩНОСТИ (`over`). Перебор считается по
     маскам, шов маской быть не может — значит любое движение здесь было бы
     дефектом правки.
  3. НЕДОБОР ТОЛЬКО УМЕНЬШАЕТСЯ. Рост недобора правкой прибора невозможен.
  4. КАЖДОЕ УМЕНЬШЕНИЕ ОБЪЯСНЕНО ШВОМ. У сущности, чей недобор упал, в
     ЭТАЛОННОМ ТЕКСТЕ обязан быть символ шва, и падение не может превышать
     число таких символов. Это проверка «вычли ровно шов, а не что попало»:
     эталонный текст — срез PT-1, поэтому символы шва внутри спана видны в нём
     напрямую.

Что скрипт НЕ доказывает и не притворяется, будто доказывает: что все вычтенные
символы были именно РАЗДЕЛИТЕЛЯМИ СБОРКИ, а не переносом `w:br` внутри абзаца.
Это обеспечено вторым условием в `measure_lib.seam_spans` (позиция не
принадлежит ни одному сегменту) и проверяется юнит-тестом
`tests/test_seam_boundary.py`, а не сличением дампов.
"""
import json
import sys

import measure_lib as ML  # noqa: E402  (лежит рядом; запуск из tests/corpus)


def _strip_bnd(rec):
    """Копия записи документа без поля границ у каждой эталонной сущности."""
    out = dict(rec)
    if "entities" in out:
        out["entities"] = [{k: v for k, v in e.items() if k != ML.BND_FIELD}
                           for e in out["entities"]]
    return out


def compare(before, after):
    """(ok: bool, lines: [str]) — человеческий отчёт о сличении двух дампов."""
    lines = []
    ok = True
    b_by = {r["doc_id"]: r for r in before}
    a_by = {r["doc_id"]: r for r in after}
    if set(b_by) != set(a_by):
        return False, ["РАЗНЫЙ СОСТАВ ДОКУМЕНТОВ: %d против %d"
                       % (len(b_by), len(a_by))]

    n_other_diff = 0
    n_over_diff = 0
    n_under_up = 0
    n_unexplained = 0
    n_changed = 0
    chars_freed = 0
    ent_freed = 0
    by_type = {}

    for doc_id in sorted(b_by):
        rb, ra = b_by[doc_id], a_by[doc_id]
        if _strip_bnd(rb) != _strip_bnd(ra):
            n_other_diff += 1
            if n_other_diff <= 3:
                lines.append("  (1) РАСХОЖДЕНИЕ ВНЕ границ: %s" % doc_id)
        eb, ea = rb.get("entities", []), ra.get("entities", [])
        if len(eb) != len(ea):
            continue
        for x, y in zip(eb, ea):
            bb, ba = x.get(ML.BND_FIELD), y.get(ML.BND_FIELD)
            if not bb or not ba:
                continue
            if bb["over"] != ba["over"]:
                n_over_diff += 1
                if n_over_diff <= 3:
                    lines.append("  (2) ПЕРЕБОР ДВИНУЛСЯ: %s / %s: %d -> %d"
                                 % (doc_id, x["type"], bb["over"], ba["over"]))
            if ba["under"] > bb["under"]:
                n_under_up += 1
                if n_under_up <= 3:
                    lines.append("  (3) НЕДОБОР ВЫРОС: %s / %s: %d -> %d"
                                 % (doc_id, x["type"], bb["under"], ba["under"]))
            if ba["under"] == bb["under"]:
                continue
            n_changed += 1
            delta = bb["under"] - ba["under"]
            seam_in_text = sum(x["text"].count(ch) for ch in ML.SEAM_CHARS)
            if seam_in_text == 0 or delta > seam_in_text:
                n_unexplained += 1
                if n_unexplained <= 5:
                    lines.append(
                        "  (4) НЕ ОБЪЯСНЕНО ШВОМ: %s / %s: -%d, символов шва в "
                        "эталоне %d, текст %r"
                        % (doc_id, x["type"], delta, seam_in_text, x["text"][:60]))
            chars_freed += delta
            s = by_type.setdefault(x["type"], {"ent": 0, "chars": 0, "to_exact": 0})
            s["ent"] += 1
            s["chars"] += delta
            if bb["direction"] in ("shorter", "both") and \
               ba["direction"] not in ("shorter", "both"):
                s["to_exact"] += 1
                ent_freed += 1

    ok = not (n_other_diff or n_over_diff or n_under_up or n_unexplained)
    head = [
        "СЛИЧЕНИЕ ДАМПОВ (доказательство узости правки «шов», этап REG)",
        "  документов: %d" % len(b_by),
        "  (1) расхождений ВНЕ поля границ: %d  %s"
        % (n_other_diff, "OK" if not n_other_diff else "ПРОВАЛ"),
        "  (2) сущностей с изменившимся ПЕРЕБОРОМ: %d  %s"
        % (n_over_diff, "OK" if not n_over_diff else "ПРОВАЛ"),
        "  (3) сущностей с ВЫРОСШИМ недобором: %d  %s"
        % (n_under_up, "OK" if not n_under_up else "ПРОВАЛ"),
        "  (4) уменьшений, НЕ объяснённых швом: %d  %s"
        % (n_unexplained, "OK" if not n_unexplained else "ПРОВАЛ"),
        "",
        "  ЧТО ИЗМЕНИЛОСЬ: сущностей с уменьшившимся недобором %d, "
        "символов снято %d, вышло из класса «недобор» %d"
        % (n_changed, chars_freed, ent_freed),
    ]
    if by_type:
        head.append("  по типам (сущностей / символов / вышло из недобора):")
        for t in sorted(by_type):
            s = by_type[t]
            head.append("      %-16s %4d / %4d / %4d"
                        % (t, s["ent"], s["chars"], s["to_exact"]))
    return ok, head + ([""] + lines if lines else [])


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    before = json.load(open(sys.argv[1], encoding="utf-8"))
    after = json.load(open(sys.argv[2], encoding="utf-8"))
    ok, lines = compare(before, after)
    print("\n".join(lines))
    print("\nВЕРДИКТ: " + ("ПРАВКА УЗКАЯ — тронут только шов"
                           if ok else "УЗОСТЬ НЕ ДОКАЗАНА (см. выше)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
