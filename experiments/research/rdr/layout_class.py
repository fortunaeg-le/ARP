# -*- coding: utf-8 -*-
"""R-RDR/уточнение: сверка единиц счёта класса «вёрстка» и разбор по механизмам.

Часть 0 — воспроизвести обе метрики на ОДНОМ дампе:
  единица LAYOUT  = утекающая сущность (leak_v2 != none) с символом вёрстки
                    (\\n, нулевая ширина, NBSP) в ТЕКСТЕ ЭТАЛОНА;
  единица R-RDR   = любая ошибочная сущность, отнесённая к вёрстке по МЕТКЕ
                    приёма корпуса (trick).
Часть 2/3 — разбор по механизмам и проверка «прочитано или нет».
"""
import json, io, pathlib, sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).parent
ZW = "­​‌‍⁠"
NBSP = "  "

LAYOUT_TRICKS = {"mut:linebreak", "mut:cell_split", "mut:invisible", "linebreak_inside_entity",
                 "cell_boundary_split", "in_footnote", "in_header", "in_footer", "in_textbox",
                 "nested_table", "invisible_chars", "zwj_and_soft_hyphen", "bold_split_runs",
                 "dense_line", "bare_cell_no_sentence"}

MECH = {
    "перенос строки внутри значения": {"mut:linebreak", "linebreak_inside_entity"},
    "разрыв между ячейками таблицы": {"mut:cell_split", "cell_boundary_split"},
    "разрыв внутри абзаца (куски текста/runs)": {"bold_split_runs"},
    "невидимые символы внутри значения": {"mut:invisible", "invisible_chars", "zwj_and_soft_hyphen"},
    "значение вне тела (колонтитул/сноска/надпись/вложенная таблица)":
        {"in_header", "in_footer", "in_footnote", "in_textbox", "nested_table"},
    "плотная строка / голая ячейка без предложения": {"dense_line", "bare_cell_no_sentence"},
}


def err(e):
    b = e.get("bnd") or {}
    d = b.get("direction")
    if d == "not_found" or not e.get("found"):
        return "не найдено"
    if d in ("shorter", "both"):
        return "недобор"
    if d == "longer":
        return "перебор"
    if e.get("leaked"):
        return "выжило в другом месте"
    return "ok"


def main():
    d = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))
    out = io.StringIO(); W = lambda *a: print(*a, file=out)
    ents = [(doc, e) for doc in d for e in doc["entities"]]

    def has_layout_char(t):
        return ("\n" in t) or any(c in t for c in ZW) or any(c in t for c in NBSP)

    def leaks(e):
        lv = e.get("leak_v2")
        return isinstance(lv, dict) and lv.get("status") != "none"

    lay_unit = [(doc, e) for doc, e in ents if leaks(e) and has_layout_char(e["text"])]
    leak_all = [(doc, e) for doc, e in ents if leaks(e)]
    rdr_unit = [(doc, e) for doc, e in ents if err(e) != "ok" and e.get("trick") in LAYOUT_TRICKS]

    W("# ЧАСТЬ 0. СВЕРКА ЕДИНИЦ (один дамп, HEAD, 324 док.)")
    W("единица LAYOUT  — утекающих всего %d; из них с символом вёрстки в эталоне: %d"
      % (len(leak_all), len(lay_unit)))
    W("единица R-RDR   — ошибочных с меткой вёрстки: %d" % len(rdr_unit))
    a = {(doc["doc_id"], e["text"], id(e)) for doc, e in lay_unit}
    inter = sum(1 for doc, e in rdr_unit if leaks(e) and has_layout_char(e["text"]))
    W("пересечение двух определений: %d" % inter)
    W("только LAYOUT (утечка+символ, но метки вёрстки нет или ошибка не в поиске): %d"
      % (len(lay_unit) - inter))
    W("только R-RDR (метка вёрстки и ошибка, но НЕ утекает или символа нет): %d"
      % (len(rdr_unit) - inter))
    W("  из них не утекает вовсе: %d" % sum(1 for doc, e in rdr_unit if not leaks(e)))
    W("  из них утекает, но символа вёрстки в эталоне нет: %d"
      % sum(1 for doc, e in rdr_unit if leaks(e) and not has_layout_char(e["text"])))
    W("классы ошибок внутри единицы LAYOUT: %s" % Counter(err(e) for _, e in lay_unit).most_common())
    W()

    W("# ЧАСТЬ 2. МЕХАНИЗМЫ (единица: ошибочная сущность с меткой; в скобках — из них утекающих)")
    seen = set()
    for name, tricks in MECH.items():
        sel = [(doc, e) for doc, e in ents if e.get("trick") in tricks]
        bad = [(doc, e) for doc, e in sel if err(e) != "ok"]
        lk = [x for x in bad if leaks(x[1])]
        seen |= tricks
        W("  %-62s всего %5d  ошибок %5d  (утекает %4d)" % (name, len(sel), len(bad), len(lk)))
        sub = Counter(e["trick"] for _, e in bad)
        W("      %s" % ", ".join("%s %d" % kv for kv in sub.most_common()))
    rest = Counter(e.get("trick") for doc, e in ents
                   if e.get("trick") in LAYOUT_TRICKS - seen and err(e) != "ok")
    W("  прочие метки вёрстки: %s" % (rest.most_common() or "нет"))
    W()
    W("  МЕХАНИЗМЫ БЕЗ ДАННЫХ на корпусе v1 (метки такого приёма в разметке нет):")
    W("   поля и элементы управления содержимым — НЕ ИЗМЕРЕНО")
    W("   правки в режиме рецензирования        — НЕ ИЗМЕРЕНО")
    W("   гиперссылки                           — НЕ ИЗМЕРЕНО")
    W()

    W("# ЧАСТЬ 3. ЧТО ДОХОДИТ ДО ДЕТЕКЦИИ (зоны вне тела)")
    zone_tricks = MECH["значение вне тела (колонтитул/сноска/надпись/вложенная таблица)"]
    per_trick = defaultdict(lambda: [0, 0, 0])   # всего, ошибок, док. с отказной зоной
    docs_with_zone = Counter()
    for doc in d:
        kinds = {z["kind"] for z in doc.get("refused_zones", [])}
        for e in doc["entities"]:
            t = e.get("trick")
            if t in zone_tricks:
                per_trick[t][0] += 1
                if err(e) != "ok":
                    per_trick[t][1] += 1
                if kinds:
                    per_trick[t][2] += 1
                docs_with_zone[(t, tuple(sorted(kinds)))] += 1
    for t, (n, bad, z) in sorted(per_trick.items()):
        W("  %-16s всего %3d  ошибок %3d  из них в док. с объявленной непрочитанной зоной %3d"
          % (t, n, bad, z))
    W("  виды объявленных зон: %s"
      % Counter(k for doc in d for k in (z["kind"] for z in doc.get("refused_zones", []))).most_common())
    json.dump([{"doc": doc["doc_id"], "trick": e.get("trick"), "type": e["type"],
                "text": e["text"], "err": err(e), "leak": leaks(e)}
               for doc, e in ents if e.get("trick") in zone_tricks],
              open(HERE / "zone_entities.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    (HERE / "layout_class.txt").write_text(out.getvalue(), encoding="utf-8")
    print("ok")


if __name__ == "__main__":
    main()
