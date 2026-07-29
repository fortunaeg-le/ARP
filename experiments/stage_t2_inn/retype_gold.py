# -*- coding: utf-8 -*-
"""
retype_gold.py — ЭТАП T2-INN, шаг 2: переразметка эталона под ДВА типа ИНН.

ЧТО ДЕЛАЕТ. Ровно одно: у записей `tests/corpus/gold.json` типа `INN`, значение
которых состоит из ДВЕНАДЦАТИ цифр, тип меняется на `INN_PER` (ИНН физического
лица). Записи с ДЕСЯТЬЮ цифрами не трогаются ВООБЩЕ — ни тип, ни границы, ни
категория, ни trick/note/checksum. Никакие другие типы не рассматриваются.

ПОЧЕМУ КЛАССИФИКАТОР НЕ `len(re.sub(r"\\D", "", text))`. Корпус адверсариальный:
внутри значения живут пробелы-разряды, дефисы, перенос строки, невидимые
символы (NBSP/ZWSP/мягкий перенос) и КИРИЛЛИЧЕСКИЕ ОМОГЛИФЫ НА МЕСТЕ ЦИФР
(«О» вместо «0», «б» вместо «6» — `corpus_lib.DIGIT2CYR`). Наивный подсчёт
`\\d` даёт на таких записях 4…11 «цифр» и отправил бы настоящий 12-значный ИНН
в 10-значную половину. Здесь применяется ТА ЖЕ таблица, которой корпус мутировал
(`corpus_lib.CYR2DIGIT`), плюс снятие разделителей — и после этого КАЖДАЯ из
1044 записей ИНН распознаётся однозначно (10 или 12, `остаток == 0`). Если
однозначно распознать не удалось хоть одну — скрипт НИЧЕГО не пишет и падает:
молча пропустить непонятную запись значило бы оставить ПДн в наборе организаций.

ЧТО ПРОВЕРЯЕТСЯ ПЕРЕД ЗАПИСЬЮ (все — до, не после):
  1. число записей эталона не изменилось (ни одной новой/удалённой);
  2. у ВСЕХ записей границы (start/end) и текст побайтно те же;
  3. у записей НЕ типа INN не изменилось ничего вообще;
  4. изменённые записи отличаются РОВНО одним полем — `type`;
  5. записи INN с 10 цифрами побайтно те же;
  6. пересечений между сущностями внутри документа не появилось (проверяется
     на всём эталоне, а не только на ИНН — дешевле, чем доказывать, что скрипт
     не мог их создать).

Дамп 20 записей «до/после» — случайная выборка с ЯВНЫМ зерном (--seed, по
умолчанию 20260729), пишется в `dump_before_after.txt` рядом со скриптом.

Запуск:
    venv/Scripts/python.exe experiments/stage_t2_inn/retype_gold.py --dry-run
    venv/Scripts/python.exe experiments/stage_t2_inn/retype_gold.py
"""
import argparse
import collections
import copy
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORPUS = os.path.join(ROOT, "tests", "corpus")
sys.path.insert(0, CORPUS)

from corpus_lib import CYR2DIGIT, INVISIBLES  # noqa: E402

GOLD = os.path.join(CORPUS, "gold.json")
DUMP = os.path.join(HERE, "dump_before_after.txt")

OLD_TYPE = "INN"
NEW_TYPE = "INN_PER"

#: Всё, что внутри реквизита является РАЗДЕЛИТЕЛЕМ, а не значением: пробелы всех
#: видов (включая невидимые из `corpus_lib.INVISIBLES`), дефисы/тире, перенос
#: строки и табуляция (сущность, разорванная границей ячейки .docx, приходит в
#: эталон с '\t'), косая черта.
SEPARATORS = set("   \n\r\t/") | set("-‐‑‒–—") | set(INVISIBLES)


def digits_of(text: str) -> str:
    """Значение реквизита без разделителей, с омоглифами, сведёнными обратно к
    цифрам. Возвращает строку; если в ней остались не-цифры — значение не
    классифицируется, и это ошибка, а не повод угадывать."""
    return "".join(CYR2DIGIT.get(ch, ch) for ch in text if ch not in SEPARATORS)


def classify(entity) -> int | None:
    """Длина ИНН в цифрах (10 или 12) или None, если значение не разобрано."""
    d = digits_of(entity["text"])
    if not d.isdigit():
        return None
    return len(d)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def retype(gold):
    """Возвращает (новый_gold, changed) — changed: [(doc_id, idx, entity_before)]."""
    new = copy.deepcopy(gold)
    changed = []
    unresolved = []
    for doc_before, doc_after in zip(gold, new):
        for i, (e_before, e_after) in enumerate(zip(doc_before["entities"], doc_after["entities"])):
            if e_before["type"] != OLD_TYPE:
                continue
            n = classify(e_before)
            if n is None or n not in (10, 12):
                unresolved.append((doc_before["doc_id"], i, e_before, n))
                continue
            if n == 12:
                e_after["type"] = NEW_TYPE
                changed.append((doc_before["doc_id"], i, e_before))
    if unresolved:
        lines = ["НЕРАЗОБРАННЫЕ записи ИНН (%d) — эталон НЕ тронут:" % len(unresolved)]
        for doc_id, i, e, n in unresolved[:20]:
            lines.append("  %s #%d %r -> %r (цифр: %s)"
                         % (doc_id, i, e["text"], digits_of(e["text"]), n))
        raise SystemExit("\n".join(lines))
    return new, changed


# --------------------------------------------------------------------------- #
#                              ИНВАРИАНТЫ                                      #
# --------------------------------------------------------------------------- #
def check(before, after):
    """Список нарушений (пустой = можно писать)."""
    bad = []
    if len(before) != len(after):
        bad.append("число документов: %d -> %d" % (len(before), len(after)))
        return bad
    for db, da in zip(before, after):
        if db["doc_id"] != da["doc_id"]:
            bad.append("порядок документов сменился: %s != %s" % (db["doc_id"], da["doc_id"]))
            continue
        if len(db["entities"]) != len(da["entities"]):
            bad.append("%s: число записей %d -> %d"
                       % (db["doc_id"], len(db["entities"]), len(da["entities"])))
            continue
        if db.get("negatives") != da.get("negatives") or db.get("ignore") != da.get("ignore"):
            bad.append("%s: изменились негативы/ignore" % db["doc_id"])
        for i, (eb, ea) in enumerate(zip(db["entities"], da["entities"])):
            if (eb["start"], eb["end"], eb["text"]) != (ea["start"], ea["end"], ea["text"]):
                bad.append("%s #%d: сдвинулись границы/текст" % (db["doc_id"], i))
                continue
            diff = {k for k in set(eb) | set(ea) if eb.get(k) != ea.get(k)}
            if not diff:
                continue
            if diff != {"type"}:
                bad.append("%s #%d: изменились поля %s (ожидалось только type)"
                           % (db["doc_id"], i, sorted(diff)))
                continue
            if eb["type"] != OLD_TYPE or ea["type"] != NEW_TYPE:
                bad.append("%s #%d: смена типа %s -> %s вне разрешённой"
                           % (db["doc_id"], i, eb["type"], ea["type"]))
                continue
            if classify(eb) != 12:
                bad.append("%s #%d: переразмечена запись, в которой не 12 цифр"
                           % (db["doc_id"], i))
        # пересечения внутри документа (после = не хуже, чем было)
        spans_b = sorted((e["start"], e["end"]) for e in db["entities"])
        spans_a = sorted((e["start"], e["end"]) for e in da["entities"])
        if spans_b != spans_a:
            bad.append("%s: множество границ изменилось" % db["doc_id"])
        overlaps_b = _overlaps(spans_b)
        overlaps_a = _overlaps(spans_a)
        if overlaps_a > overlaps_b:
            bad.append("%s: появились пересечения сущностей (%d -> %d)"
                       % (db["doc_id"], overlaps_b, overlaps_a))
    return bad


def _overlaps(spans):
    n = 0
    for i in range(1, len(spans)):
        if spans[i][0] < spans[i - 1][1]:
            n += 1
    return n


def stats(gold):
    c = collections.Counter(e["type"] for d in gold for e in d["entities"])
    return c


# --------------------------------------------------------------------------- #
#                                  ДАМП                                        #
# --------------------------------------------------------------------------- #
def dump(before, after, changed, seed, path):
    rnd = random.Random(seed)
    inn_rows = [(d["doc_id"], i, e)
                for d in before
                for i, e in enumerate(d["entities"]) if e["type"] == OLD_TYPE]
    sample = rnd.sample(inn_rows, 20)
    idx_after = {(d["doc_id"], i): e for d in after for i, e in enumerate(d["entities"])}

    lines = [
        "ЭТАП T2-INN, шаг 2 — дамп 20 записей эталона ДО и ПОСЛЕ переразметки.",
        "Зерно выборки: %d (random.Random(seed).sample из всех %d записей типа %s)."
        % (seed, len(inn_rows), OLD_TYPE),
        "Показаны: doc_id, номер записи в документе, границы, категория/trick,",
        "значение как оно лежит в тексте (repr — видны разделители и омоглифы),",
        "значение, сведённое к цифрам, и тип ДО -> ПОСЛЕ.",
        "",
    ]
    for doc_id, i, e in sample:
        a = idx_after[(doc_id, i)]
        lines.append("%-28s #%-3d [%5d,%5d) %-11s %-24s" % (
            doc_id, i, e["start"], e["end"], e.get("category"), e.get("trick") or "-"))
        lines.append("    значение : %r" % e["text"])
        lines.append("    цифры    : %s (%d)" % (digits_of(e["text"]), len(digits_of(e["text"]))))
        lines.append("    тип      : %s -> %s%s" % (
            e["type"], a["type"], "   (ИЗМЕНЁН)" if e["type"] != a["type"] else "   (не тронут)"))
        lines.append("")
    lines.append("Изменено записей всего: %d из %d." % (len(changed), len(inn_rows)))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return sample


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=20260729)
    args = ap.parse_args(argv)

    before = load(GOLD)
    after, changed = retype(before)

    sb, sa = stats(before), stats(after)
    print("Записей эталона всего: %d -> %d" % (sum(sb.values()), sum(sa.values())))
    print("  %s : %d -> %d" % (OLD_TYPE, sb[OLD_TYPE], sa[OLD_TYPE]))
    print("  %s : %d -> %d" % (NEW_TYPE, sb[NEW_TYPE], sa[NEW_TYPE]))
    print("Переразмечено записей: %d" % len(changed))

    bad = check(before, after)
    if bad:
        print("\nИНВАРИАНТЫ НАРУШЕНЫ (%d) — эталон НЕ записан:" % len(bad))
        for b in bad[:30]:
            print("  -", b)
        return 1
    print("Инварианты: границы/тексты/поля/пересечения — без изменений (проверено на всём эталоне).")

    dump(before, after, changed, args.seed, DUMP)
    print("Дамп 20 записей: %s (зерно %d)" % (DUMP, args.seed))

    if args.dry_run:
        print("\n--dry-run: gold.json не тронут.")
        return 0

    # Формат записи ТОТ ЖЕ, что у файла (json.dumps indent=1, ensure_ascii=False,
    # перевод строки LF) — проверено побайтным round-trip'ом до правки: файл,
    # прочитанный и записанный этими параметрами без изменений, совпадает сам с
    # собой. Иначе диф эталона утонул бы в переформатировании 3.6 МБ.
    with open(GOLD, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(after, ensure_ascii=False, indent=1))
    print("\nЗаписан %s" % GOLD)
    print("ДАЛЬШЕ, В ЭТОМ ПОРЯДКЕ: пересобрать results_baseline.json, "
          "results_iter_baseline.json, затем MANIFEST.sha256.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
