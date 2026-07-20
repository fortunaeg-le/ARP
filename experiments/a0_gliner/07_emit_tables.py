"""A0 bake-off, шаг 7 — сборка markdown-таблиц из results.json/results_synthetic.json."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GRID = ["0.1", "0.3", "0.5", "0.7", "0.9"]
R = json.load(open(os.path.join(HERE, "tables", "results.json"), encoding="utf-8"))
RS = json.load(open(os.path.join(HERE, "tables", "results_synthetic.json"), encoding="utf-8"))

out = []
w = out.append

w("# A0 GLiNER bake-off — таблицы по сетке\n")
w("Сгенерировано `07_emit_tables.py` из `tables/results.json` (dogovor) и "
  "`tables/results_synthetic.json` (подвыборка). Числа проверяемы по пер-точечным "
  "дампам в `dumps/gliner_*_thNN.json`.\n")

# --- dogovor ---
nat = R["natasha"]["mirror"]
w("## dogovor.docx — зеркала по сетке порогов\n")
w(f"**Natasha (baseline, порога нет):** {nat['n_spans']} спанов "
  f"{nat['by_type']}; мусор-категорий **{nat['garbage_categories_marked']}/6**; "
  f"Восход recall **{nat['voskhod_recall']:.0%}** ({nat['voskhod_occ_caught']}/"
  f"{nat['voskhod_occ_total']}), типов **{len(nat['voskhod_types'])}** "
  f"{nat['voskhod_types']}; ФИО 3/3 PER; адреса 3/3.\n")

for key in R:
    if key == "natasha" or "grid" not in R[key]:
        continue
    r = R[key]
    w(f"### {key}  (load {r['load_s']}c, полный документ **{r['latency_full_doc_s']}c**)\n")
    w("| порог | спанов | (a) мусор-кат. | Восход recall | типов | 1 тип | ФИО→PER | адрес→ADDR |")
    w("|---|---:|---:|---:|---:|:--:|---:|---:|")
    for th in GRID:
        m = r["grid"][th]
        n_per = sum(1 for v in m["people_caught"].values() if v["as_person"])
        n_addr = sum(1 for v in m["addr_caught"].values() if v["as_address"])
        w(f"| {th} | {m['n_spans']} | {m['garbage_categories_marked']}/6 | "
          f"{m['voskhod_recall']:.0%} ({m['voskhod_occ_caught']}/{m['voskhod_occ_total']}) | "
          f"{len(m['voskhod_types'])} | {'✓' if m['voskhod_single_type'] else '—'} | "
          f"{n_per}/3 | {n_addr}/3 |")
    w("")

# --- synthetic ---
w("## Синтетическая подвыборка (22 док.) — покрытие сущностей Natasha\n")
w(f"**Natasha:** {sum(RS['natasha_by_type'].values())} сущн. "
  f"{RS['natasha_by_type']}; латентность NER {RS['natasha_latency_s']}c.\n")
w("«Покрытие» = сущность Natasha перекрыта спаном GLiNER (любой/тот же тип). "
  "Приёмка требует пропуска **≤10 %**.\n")
for model, mr in RS["models"].items():
    w(f"### {model}  (подвыборка целиком {mr['latency_total_s']}c)\n")
    w("| порог | покрытие (любой тип) | пропуск | покрытие (тот же тип) | пропуск (тип) |")
    w("|---|---:|---:|---:|---:|")
    for th in GRID:
        g = mr["grid"][th]
        n = g["total_natasha"]
        any_pct = 100 * g["covered_any"] / n
        same_pct = 100 * g["covered_same_type"] / n
        w(f"| {th} | {g['covered_any']}/{n} ({any_pct:.0f}%) | **{100-any_pct:.0f}%** | "
          f"{g['covered_same_type']}/{n} ({same_pct:.0f}%) | {100-same_pct:.0f}% |")
    w("")

# --- (c) role-word over-masking по сетке (dogovor) ---
import re
ROLE = re.compile(
    r"^(участник\w*|обществ\w*|большинств\w*|дол(я|ю|и|е|ей|ям|ями|ях)|устав\w*"
    r"|прибыл\w*|сторон\w*|собрани\w*|решени\w*|договор\w*|бюджет\w*|совет\w*|прав\w*)$",
    re.I)


def _rolecount(spans):
    c = 0
    for sp in spans:
        t = " ".join(sp["text"].lower().split()).strip(" .,«»\"()")
        toks = t.split()
        if ROLE.match(t) or (len(toks) <= 3 and any(ROLE.match(w) for w in toks)):
            c += 1
    return c


nat_rc = _rolecount([{"text": e["text"]} for e in
                     json.load(open(os.path.join(HERE, "dumps", "natasha_dogovor.json"),
                                    encoding="utf-8"))["entities"]])
w("## (c) Новый мусор — over-masking родовых слов, по сетке (dogovor)\n")
w("Спаны, где значение — родовое юр-слово (участник/общество/большинство/доля/устав/"
  "…), НЕ ПДн. Natasha порога не имеет (плоско). Критерий «не хуже старого»: GLiNER "
  "должен быть ≤ Natasha.\n")
w("| порог | Natasha | multi-v2.1 ru | pii-v1 ru |")
w("|---|---:|---:|---:|")
for th in GRID:
    thf = float(th)
    cells = []
    for f in ("raw_gliner_multi-v2_1_ru.json", "raw_gliner_multi_pii-v1_ru.json"):
        raw = json.load(open(os.path.join(HERE, "dumps", f), encoding="utf-8"))
        cells.append(_rolecount([p for p in raw["predictions"] if p["score"] >= thf]))
    mark = lambda v: f"**{v}**" if v <= nat_rc else str(v)  # noqa: E731
    w(f"| {th} | {nat_rc} | {mark(cells[0])} | {mark(cells[1])} |")
w("\nЖирным — точки, где GLiNER-мусор ≤ Natasha (150): только th≥0.7 у multi-v2.1 "
  "(0.7→134, 0.9→36) — но там Восход recall падает до 41–30 % и адреса теряются. "
  "Т.е. (c) проходит лишь там, где проваливаются (b)/адреса; при рабочих th=0.1–0.5 "
  "мусор ×1.6–2.4. Единственный критерий, красный на ВСЕХ порогах, — синтетика "
  "(пропуск ≥19 %), на нём и держится «НЕТ».\n")

open(os.path.join(HERE, "tables", "summary.md"), "w", encoding="utf-8").write("\n".join(out))
print("tables/summary.md написан,", len(out), "строк")
