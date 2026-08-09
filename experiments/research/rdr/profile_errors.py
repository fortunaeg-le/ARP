# -*- coding: utf-8 -*-
"""R-RDR часть 0: пересчёт профиля ошибок на СВЕЖЕМ дампе гейта (после LAYOUT/NODES/MERGE-2).

Читает tests/corpus/results_gate_current.json (324 док., HEAD), раскладывает
каждую эталонную сущность на один класс ошибки и одну причину.
Ничего не пишет за пределы experiments/research/rdr/.
"""
import json, io, pathlib, sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).parent

# --- класс ошибки (взаимоисключающие, порядок = приоритет) -------------------
def err_class(e):
    b = e.get("bnd") or {}
    d = b.get("direction")
    if d == "not_found" or not e.get("found"):
        mr = e.get("miss_reason") or ""
        if mr.startswith("wrong_type"):
            return "не_найдено:чужой_тип"
        return "не_найдено:вовсе"
    if d == "shorter":
        return "границы:недобор"
    if d == "longer":
        return "границы:перебор"
    if d == "both":
        return "границы:сдвиг"
    if e.get("leaked"):
        return "утечка_без_сдвига"
    if not e.get("exact"):
        return "неточно_прочее"
    return "ok"

# --- причина (по метке приёма корпуса) --------------------------------------
LAYOUT = {"mut:linebreak", "mut:cell_split", "mut:invisible", "linebreak_inside_entity",
          "cell_boundary_split", "in_footnote", "in_header", "in_footer", "in_textbox",
          "nested_table", "invisible_chars", "zwj_and_soft_hyphen", "bold_split_runs",
          "dense_line", "bare_cell_no_sentence"}
CASEHOM = {"mut:case", "mut:homoglyph", "uppercase", "lowercase", "alternating_case",
           "homoglyph_cyrillic_in_digits", "homoglyph_in_email", "homoglyph_latin_in_cyrillic"}
DIGITS = {"mut:digit_spaces", "digit_grouping", "digit_grouping_dashes"}
GLUE = {"glued_to_prev_entity", "glued_to_next_entity"}

def cause(e):
    t = e.get("trick")
    if t in LAYOUT:
        return "вёрстка"
    if t in CASEHOM:
        return "регистр/омоглифы"
    if t == "mut:combo":
        return "смесь (омоглиф+невидимые+регистр)"
    if t in DIGITS:
        return "разрядка цифр"
    if t in GLUE:
        return "склейка с соседом"
    if t is None:
        return "чистый текст"
    return "прочая метка: %s" % t

def main():
    d = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))
    out = io.StringIO()
    W = lambda *a: print(*a, file=out)

    ents = [e for doc in d for e in doc["entities"]]
    N = len(ents)
    cls = Counter(); by_type = defaultdict(Counter); by_cause = Counter()
    cross = defaultdict(Counter)           # класс -> причина
    cause_type = defaultdict(Counter)      # причина -> тип
    bad_ents = []
    for e in ents:
        c = err_class(e)
        cls[c] += 1
        by_type[e["type"]][c] += 1
        if c != "ok":
            cu = cause(e)
            by_cause[cu] += 1
            cross[c][cu] += 1
            cause_type[cu][e["type"]] += 1
            bad_ents.append(e)

    W("# ПРОФИЛЬ ОШИБОК — дамп results_gate_current.json, 324 док.")
    W("Эталонных сущностей: %d.  Верно: %d (%.2f %%).  Остаток ошибок: %d (%.2f %%)."
      % (N, cls["ok"], 100.0 * cls["ok"] / N, N - cls["ok"], 100.0 * (N - cls["ok"]) / N))
    W()
    W("## Класс ошибки")
    tot = N - cls["ok"]
    for k, v in cls.most_common():
        if k == "ok":
            continue
        W("  %-24s %6d   %5.1f %% остатка   %5.2f %% всех" % (k, v, 100.0 * v / tot, 100.0 * v / N))
    W()
    W("## Причина (метка приёма корпуса)")
    for k, v in by_cause.most_common():
        W("  %-36s %6d   %5.1f %% остатка" % (k, v, 100.0 * v / tot))
    W()
    W("## Класс × причина")
    for c in cls:
        if c == "ok":
            continue
        W("  %s (%d):" % (c, cls[c]))
        for k, v in cross[c].most_common():
            W("      %-36s %5d  (%4.1f %%)" % (k, v, 100.0 * v / cls[c]))
    W()
    W("## Причина × тип (первые 6 типов в причине)")
    for cu, cnt in by_cause.most_common():
        W("  %-36s %s" % (cu, ", ".join("%s %d" % kv for kv in cnt and cause_type[cu].most_common(6))))
    W()
    W("## По типам: остаток / всего")
    rows = []
    for t, c in by_type.items():
        tt = sum(c.values()); bad = tt - c["ok"]
        rows.append((bad, t, tt, c))
    rows.sort(reverse=True)
    W("  %-10s %6s %6s %7s | %s" % ("тип", "всего", "ошибок", "доля", "разбивка"))
    for bad, t, tt, c in rows:
        br = ", ".join("%s %d" % (k.replace("границы:", "гр:").replace("не_найдено:", "нн:"), v)
                       for k, v in c.most_common() if k != "ok")
        W("  %-10s %6d %6d %6.1f %% | %s" % (t, tt, bad, 100.0 * bad / tt, br))

    # --- перебор в масках (шум), из линии «д» ------------------------------
    fp = [f for doc in d for f in doc["false_positives"]]
    fp_neg = [f for f in fp if f.get("on_negative")]
    fp_cross = [f for f in fp if f.get("cross_type")]
    W()
    W("## Другая сторона: маски мимо эталона (линия «д»)")
    W("  всего ложных масок: %d;  из них на негативе: %d;  чужим типом: %d"
      % (len(fp), len(fp_neg), len(fp_cross)))
    ft = Counter(f["gtype"] for f in fp)
    W("  по типам: %s" % ", ".join("%s %d" % kv for kv in ft.most_common(10)))
    ftn = Counter(f["gtype"] for f in fp_neg)
    W("  на негативе по типам: %s" % ", ".join("%s %d" % kv for kv in ftn.most_common(10)))

    # --- утечка -------------------------------------------------------------
    lv2 = Counter()
    for e in ents:
        s = (e.get("leak_v2") or {}).get("status") if isinstance(e.get("leak_v2"), dict) else None
        lv2[s] += 1
    W()
    W("## Утечка leak_v2 по сущностям: %s" % dict(lv2))
    leaked = [e for e in ents if isinstance(e.get("leak_v2"), dict) and e["leak_v2"]["status"] != "none"]
    W("  утечка есть у %d сущностей (%.2f %% всех); их классы: %s"
      % (len(leaked), 100.0 * len(leaked) / N, dict(Counter(err_class(e) for e in leaked))))
    W("  их причины: %s" % dict(Counter(cause(e) for e in leaked)))

    (HERE / "profile_errors.txt").write_text(out.getvalue(), encoding="utf-8")
    json.dump([{"type": e["type"], "text": e["text"], "trick": e.get("trick"),
                "cls": err_class(e), "cause": cause(e), "bnd": e.get("bnd"),
                "miss": e.get("miss_reason"), "cat": e.get("category")} for e in bad_ents],
              open(HERE / "residual.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("ok", len(bad_ents))

if __name__ == "__main__":
    main()
