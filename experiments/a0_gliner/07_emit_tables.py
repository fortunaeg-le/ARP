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

open(os.path.join(HERE, "tables", "summary.md"), "w", encoding="utf-8").write("\n".join(out))
print("tables/summary.md написан,", len(out), "строк")
