"""ЭТАП MARKUP-SPREAD — ЦЕНА решения о распространении ручной разметки, числом.

ЗАЧЕМ. Распространение помеченного человеком значения по всему документу может
закрыть ЛИШНЕЕ: «Мороз» человек и «мороз» погода, «Самара» в адресе и «Самара» в
названии фирмы. Утверждать «мы это закрыли guard-ами» без цифры нельзя.

КАК СЧИТАЕТСЯ. Симуляция ровно того, что делает пользователь. Для КАЖДОЙ эталонной
сущности документа: считаем, что человек выделил ЕЁ (движок её не нашёл), и просим
`markup_spread.find_occurrences` распространить. Каждый найденный спан судится по
эталону ТОГО ЖЕ документа:

  ВЕРНО (true)    — спан лежит внутри эталонной сущности того же типа;
  ЛОЖНО (false)   — спан задевает `negatives` эталона (явно «не ПДн») либо не
                    пересекается ни с одной эталонной сущностью вообще;
  СЕРОЕ (gray)    — спан задевает `ignore` эталона (серая зона по решению корпуса)
                    либо эталонную сущность ДРУГОГО типа.

ПРИБАВКА полноты — сколько эталонных сущностей, НЕ покрытых якорем, оказались
покрыты распространением: это и есть польза, ради которой этап затеян.

Корпус v1 читается ТОЛЬКО отсюда (`experiments/`, роль измерителя), не из `src/`
— стена зоны детекции не нарушается.

Запуск: venv/Scripts/python.exe experiments/markup_spread/measure_spread.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import markup_spread                                # noqa: E402
from markup_spread import MORPH_TYPES, _key_of, find_occurrences  # noqa: E402
from models import SourceDocument, TextSegment      # noqa: E402

_ACCEPT_REAL = markup_spread._accept


def _accept_all(*_a, **_kw):
    """Подмена процедур точности на «пускать всё» — для замера цены guard-ов."""
    return True

# Тип эталона -> тип интерфейса. Эталон корпуса v1 зовёт людей PER.
GOLD_TO_UI = {"PER": "PERSON", "ORG": "ORG", "ADDRESS": "ADDRESS"}


def _overlap(a1, a2, b1, b2):
    return max(a1, b1) < min(a2, b2)


def judge(sp, ent_by_type, all_ents, negatives, ignores, ui_type):
    s, e = sp["start"], sp["end"]
    for (ns, ne) in negatives:
        if _overlap(s, e, ns, ne):
            return "false"
    for (ns, ne) in ignores:
        if _overlap(s, e, ns, ne):
            return "gray"
    for (gs, ge) in ent_by_type.get(ui_type, ()):
        if gs <= s and e <= ge:
            return "true"
    for (gs, ge, gt) in all_ents:
        if _overlap(s, e, gs, ge):
            return "true" if gt == ui_type else "gray"
    return "false"


def main():
    gold = json.load(open(ROOT / "tests/corpus/gold.json", encoding="utf-8"))
    docs_dir = ROOT / "tests/corpus/docs"

    verdicts = Counter()
    by_type = Counter()
    false_examples = []
    gray_examples = []
    recall_gain = 0
    recall_total = 0
    anchors_used = 0
    rejected = Counter()
    rejected_examples = []
    rejected_true_examples = []
    residual = 0
    repeats_total = 0
    residual_examples = []

    for entry in gold:
        if entry.get("format") != "txt" or entry.get("source") != "base":
            continue                       # мутации — тот же текст, лишний вес
        path = docs_dir / (entry["doc_id"] + ".txt")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Сегментация — ТА ЖЕ, что у настоящего экстрактора для .txt: одна
        # СТРОКА = один сегмент (`extractor.py`, «l{index}»/«txt_line»). Замер на
        # одном сегменте-файле врал: ORG-вид вырезает перенос строки внутри
        # сегмента, и соседние строки склеивались в один токен
        # («…дмитриевнойООО Ромашка-Плюс»), из-за чего вхождения не находились.
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        segments, base = [], {}
        pos = 0
        for i, line in enumerate(lines):
            sid = "l%d" % i
            segments.append(TextSegment(id=sid, text=line,
                                        source_type="txt_line",
                                        metadata={"line_index": i}))
            base[sid] = pos
            pos += len(line) + 1
        doc = SourceDocument(segments=segments, source_format="txt",
                             source_path=str(path))

        def to_local(gs, ge):
            """Глобальное смещение эталона -> (segment_id, start, end). None, если
            сущность пересекает границу строк (в замере такие пропускаем)."""
            for seg in segments:
                b = base[seg.id]
                if b <= gs and ge <= b + len(seg.text):
                    return seg.id, gs - b, ge - b
            return None

        def to_global(seg_id, s, e):
            return base[seg_id] + s, base[seg_id] + e

        ents = [(x["start"], x["end"], GOLD_TO_UI.get(x["type"], x["type"]))
                for x in entry["entities"]]
        ent_by_type = {}
        for (s, e, t) in ents:
            ent_by_type.setdefault(t, []).append((s, e))
        negatives = [(x["start"], x["end"]) for x in entry.get("negatives", [])]
        ignores = [(x["start"], x["end"]) for x in entry.get("ignore", [])]

        for x in entry["entities"]:
            ui_type = GOLD_TO_UI.get(x["type"], x["type"])
            loc = to_local(x["start"], x["end"])
            if loc is None:
                continue           # эталонная сущность через перенос строки
            anchors_used += 1
            # Ровно поведение продукта: человек пометил ОДНО вхождение, остальной
            # документ открыт (occupied пуст — движок не нашёл ничего).
            found = [dict(sp, **dict(zip(("start", "end"),
                                          to_global(sp["segment_id"], sp["start"], sp["end"]))))
                     for sp in find_occurrences(doc, {}, loc[0], loc[1], loc[2], ui_type)]
            # прибавка полноты: эталонные сущности того же типа, кроме якоря
            same = [(gs, ge) for (gs, ge) in ent_by_type.get(ui_type, ())
                    if not (gs == x["start"] and ge == x["end"])]
            recall_total += len(same)
            covered = set()
            for sp in found:
                v = judge(sp, ent_by_type, ents, negatives, ignores, ui_type)
                verdicts[v] += 1
                by_type[(ui_type, v)] += 1
                if v == "false" and len(false_examples) < 25:
                    false_examples.append((entry["doc_id"], ui_type,
                                           x["text"], sp["surface"]))
                if v == "gray" and len(gray_examples) < 15:
                    gray_examples.append((entry["doc_id"], ui_type,
                                          x["text"], sp["surface"]))
                for (gs, ge) in same:
                    if gs <= sp["start"] and sp["end"] <= ge:
                        covered.add((gs, ge))
            recall_gain += len(covered)

            # --- ЦЕНА GUARD-ОВ: тот же прогон с ОТКЛЮЧЁННЫМИ процедурами
            # точности. Разница списков — ровно те вхождения, которые guard-ы
            # отвергли; каждое судится эталоном так же. Это и есть ответ на
            # «покажи цену решения числом»: сколько верного guard-ы потеряли и
            # сколько ложного не пустили.
            markup_spread._accept = _accept_all
            try:
                raw = [dict(sp, **dict(zip(("start", "end"),
                                           to_global(sp["segment_id"], sp["start"], sp["end"]))))
                       for sp in find_occurrences(doc, {}, loc[0], loc[1], loc[2], ui_type)]
            finally:
                markup_spread._accept = _ACCEPT_REAL
            kept = {(sp["start"], sp["end"]) for sp in found}
            for sp in raw:
                if (sp["start"], sp["end"]) in kept:
                    continue
                v = judge(sp, ent_by_type, ents, negatives, ignores, ui_type)
                rejected[v] += 1
                if v != "true" and len(rejected_examples) < 25:
                    rejected_examples.append((entry["doc_id"], ui_type, v,
                                              x["text"], sp["surface"]))
                elif v == "true" and len(rejected_true_examples) < 15:
                    rejected_true_examples.append((entry["doc_id"], ui_type,
                                                   x["text"], sp["surface"]))

            # --- ОСТАТОЧНАЯ УТЕЧКА: эталонные сущности того же типа с ТЕМ ЖЕ
            # ключом лемм, что у якоря, оставшиеся НЕ закрытыми. Это цена
            # сознательного отказа расширять спан и строгих guard-ов.
            # «Тот же повтор» — по ЛЕММАМ только для морфологических типов; у
            # цифровых `_key_of` даёт None (или ('руб',) для сумм), и без этого
            # различения два РАЗНЫХ расчётных счёта считались бы одним значением.
            morph = ui_type in MORPH_TYPES
            akey = _key_of(x["text"])
            for (gs, ge) in same:
                if morph:
                    if akey is None or _key_of(text[gs:ge]) != akey:
                        continue
                elif text[gs:ge] != x["text"]:
                    continue
                repeats_total += 1
                if not any(sp["start"] < ge and gs < sp["end"] for sp in found):
                    residual += 1
                    if len(residual_examples) < 25:
                        residual_examples.append((entry["doc_id"], ui_type,
                                                  x["text"], text[gs:ge]))

    total = sum(verdicts.values())
    print("=== ЦЕНА РАСПРОСТРАНЕНИЯ РУЧНОЙ РАЗМЕТКИ (корпус v1, base/txt) ===")
    print("якорей просимулировано: %d" % anchors_used)
    print("распространённых вхождений всего: %d" % total)
    for v in ("true", "gray", "false"):
        pct = (100.0 * verdicts[v] / total) if total else 0.0
        print("  %-6s %5d  (%.2f%%)" % (v, verdicts[v], pct))
    print("точность распространения (true / (true+false)) = %.2f%%"
          % (100.0 * verdicts["true"] / max(1, verdicts["true"] + verdicts["false"])))
    print("прибавка полноты: %d из %d эталонных сущностей-неякорей закрыты (%.2f%%)"
          % (recall_gain, recall_total, 100.0 * recall_gain / max(1, recall_total)))
    rej_total = sum(rejected.values())
    print("\n--- ЦЕНА GUARD-ОВ (что отвергли процедуры точности) ---")
    print("отвергнуто вхождений: %d" % rej_total)
    for v in ("true", "gray", "false"):
        print("  %-6s %5d" % (v, rejected[v]))
    print("  не-верного (ложное+серое) не пущено: %d" % (rejected["gray"] + rejected["false"]))
    print("  верного потеряно: %d" % rejected["true"])
    print("\n--- ОСТАТОЧНАЯ УТЕЧКА (то же значение, осталось открытым) ---")
    print("повторов того же значения всего: %d; осталось открытыми: %d (%.2f%%)"
          % (repeats_total, residual, 100.0 * residual / max(1, repeats_total)))
    print("для сравнения: ДО этапа открытыми оставались ВСЕ %d" % repeats_total)
    print("\nпо типам:")
    for t in sorted({k[0] for k in by_type}):
        row = {v: by_type[(t, v)] for v in ("true", "gray", "false")}
        print("  %-10s true=%-5d gray=%-4d false=%-4d" % (t, row["true"], row["gray"], row["false"]))
    print("\nЛОЖНЫЕ (до 25): doc | тип | якорь -> закрыто")
    for r in false_examples:
        print("  %s | %s | %r -> %r" % r)
    print("\nСЕРЫЕ (до 15): doc | тип | якорь -> закрыто")
    for r in gray_examples:
        print("  %s | %s | %r -> %r" % r)
    print("\nGUARD ОТВЕРГ НЕ-ВЕРНОЕ (до 25): doc | тип | вердикт | якорь -> отвергнуто")
    for r in rejected_examples:
        print("  %s | %s | %s | %r -> %r" % r)
    print("\nОСТАЛОСЬ ОТКРЫТЫМ (до 25): doc | тип | якорь -> не закрыто")
    for r in residual_examples:
        print("  %s | %s | %r -> %r" % r)
    print("\nGUARD ОТВЕРГ ВЕРНОЕ (до 15): doc | тип | якорь -> потеряно")
    for r in rejected_true_examples:
        print("  %s | %s | %r -> %r" % r)


if __name__ == "__main__":
    main()
