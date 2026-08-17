# -*- coding: utf-8 -*-
"""generate_noise.py — порождение ПАРНОГО корпуса «чистый / испорченный».

УСТРОЙСТВО. Берём готовые документы корпуса v2 (их МОДЕЛИ из `_model/`), для
каждого пишем ДВА документа:

    nz_<id>   — чистый двойник (модель не тронута, текст побайтно тот же);
    nzd_<id>  — тот же документ, пропущенный через классы искажений
                распознавания (`noise/classes/*.yaml`).

ПОЧЕМУ ПАРАМИ, А НЕ ОДНИМ ГРЯЗНЫМ НАБОРОМ. Иначе «сколько мы теряем на грязном
тексте» пришлось бы сравнивать с числами по ДРУГИМ документам, и в разницу
попала бы разница документов, а не искажений. В паре содержимое совпадает
дословно, различие ровно одно — искажение.

ЭТАЛОН ЗНАЕТ ПРАВИЛЬНОЕ ЗНАЧЕНИЕ. Каждое испорченное вхождение несёт `clean`
(что там было), `noise` (класс) и `pair` (ключ той же величины в чистом
двойнике). Координаты не хранятся, а вычисляются сериализатором из модели —
соврать в них нельзя по построению (тот же приём, что у `tests/corpus/mutate.py`).

КОРПУС V2 НЕ ТРОГАЕТСЯ. Ни `docs/`, ни `_model/`, ни `gold_v2.json`, ни
MANIFEST: всё порождённое пишется в `noise/docs`, `noise/_model`,
`noise/gold_noise.json`.

Запуск:
    venv/Scripts/python.exe tests/corpus_v2/generate_noise.py
    venv/Scripts/python.exe tests/corpus_v2/generate_noise.py --docs 8
"""
import argparse
import copy
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import corpus_lib as CL                                        # noqa: E402
import noise_lib as NL                                         # noqa: E402

NOISE_ROOT = os.path.join(HERE, "noise")
GOLD_NOISE = os.path.join(NOISE_ROOT, "gold_noise.json")

#: Сколько документов корпуса берём за основу. Пара = два прогона замера,
#: поэтому число выбрано так, чтобы полный замер укладывался в один заход.
DEFAULT_DOCS = 16

#: Сколько мест на документ отдаётся ОДНОМУ приёму окружения. Приёмы окружения
#: (склейка с подписью, склейка соседей, перестановка колонок) редки по
#: конструкции: каждый из них требует определённого места в документе, и
#: применить их «ко всем вхождениям» нельзя — их просто столько нет.
CONTEXT_SITES = 3


# --------------------------------------------------------------------------- #
#                            ОБХОД МОДЕЛИ ДОКУМЕНТА                            #
# --------------------------------------------------------------------------- #
def iter_paras(blocks):
    """Все абзацы и врезки модели, в порядке сериализации, вместе со СПИСКОМ
    блоков-владельцем (он нужен приёму перестановки колонок)."""
    for i, b in enumerate(blocks):
        if b["t"] in ("p", "tb"):
            yield blocks, i, b
            if b.get("fn"):
                yield None, None, {"t": "p", "chunks": b["fn"]}
        elif b["t"] == "tbl":
            for row in b["rows"]:
                for c in row:
                    for x in iter_paras(c["blocks"]):
                        yield x


def all_paras(model):
    for key in ("header", "body", "footer"):
        if model.get(key):
            for x in iter_paras(model[key]):
                yield x


def eid_counts(model):
    """{eid: сколько чанков несут это вхождение}."""
    n = {}
    for _, _, p in all_paras(model):
        for ch in p["chunks"]:
            if "e" in ch:
                k = ch["e"]["id"]
                n[k] = n.get(k, 0) + 1
    return n


def ent_chunks(model):
    """Чанки, несущие величину ЦЕЛИКОМ, в порядке сериализации.

    ВХОЖДЕНИЯ ИЗ ДВУХ ЧАНКОВ ПРОПУСКАЮТСЯ. Корпус v2 умеет разрывать величину
    границей ячейки или ранов (`cell_split`, `format_split`), и тогда одно
    вхождение собрано из двух кусков, между которыми в каноническом тексте
    стоит табуляция от таблицы. Испортив один кусок, мы записали бы в `clean`
    ПОЛОВИНУ правильного значения — то есть эталон, знающий неправду. Такие
    вхождения остаются нетронутыми и попадают в строку «(не искажено)».
    """
    counts = eid_counts(model)
    for _, _, p in all_paras(model):
        for ch in p["chunks"]:
            if "e" in ch and ch["s"].strip() and counts[ch["e"]["id"]] == 1:
                yield ch


# --------------------------------------------------------------------------- #
#                           ПРИЁМЫ ОКРУЖЕНИЯ                                   #
# --------------------------------------------------------------------------- #
def _mark(ch, cls_id, clean=None):
    e = ch["e"]
    e["noise"] = cls_id
    e["clean"] = clean if clean is not None else ch["s"]


def ctx_label_glue(model, rnd, limit, singles):
    """Пробел между подписью и значением потерян. Величина цела."""
    done = 0
    for _, _, p in all_paras(model):
        chunks = p["chunks"]
        for i, ch in enumerate(chunks):
            if done >= limit:
                return done
            if "e" not in ch or i == 0 or ch["e"]["id"] not in singles:
                continue
            prev = chunks[i - 1]
            if "e" in prev or "n" in prev or not prev["s"].endswith((" ", " ")):
                continue
            if ch["e"].get("noise"):
                continue
            prev["s"] = prev["s"].rstrip("  ")
            _mark(ch, "label_glue")
            done += 1
    return done


def ctx_glue_neighbours(model, rnd, limit, singles):
    """Две соседние величины слились: разделитель между ними исчез."""
    done = 0
    for _, _, p in all_paras(model):
        chunks = p["chunks"]
        for i in range(len(chunks) - 2):
            if done >= limit:
                return done
            a, mid, b = chunks[i], chunks[i + 1], chunks[i + 2]
            if "e" not in a or "e" not in b or "e" in mid or "n" in mid:
                continue
            if a["e"]["id"] not in singles or b["e"]["id"] not in singles:
                continue
            if a["e"].get("noise") or b["e"].get("noise"):
                continue
            if len(mid["s"]) > 12 or not mid["s"].strip(" ,;/ ") == "":
                continue
            if not mid["s"]:
                continue
            mid["s"] = ""
            _mark(a, "glue_neighbours")
            _mark(b, "glue_neighbours")
            done += 1
    return done


def ctx_column_reorder(model, rnd, limit, singles):
    """Строки двух колонок перемешаны: соседние абзацы с величинами меняются
    местами. Ни один символ величины не меняется — ломается только порядок."""
    done = 0
    seen = []
    for owner, idx, p in all_paras(model):
        if owner is None:
            continue
        seen.append((owner, idx, p))
    i = 0
    while i < len(seen) - 1 and done < limit:
        (o1, i1, p1), (o2, i2, p2) = seen[i], seen[i + 1]
        if o1 is not o2 or i2 != i1 + 1:
            i += 1
            continue
        e1 = [c for c in p1["chunks"] if "e" in c and not c["e"].get("noise")
              and c["e"]["id"] in singles]
        e2 = [c for c in p2["chunks"] if "e" in c and not c["e"].get("noise")
              and c["e"]["id"] in singles]
        if not e1 or not e2:
            i += 1
            continue
        o1[i1], o1[i2] = o1[i2], o1[i1]
        for c in e1 + e2:
            _mark(c, "column_reorder")
        done += 1
        i += 2
    return done


CTX = {"label_glue": ctx_label_glue,
       "glue_neighbours": ctx_glue_neighbours,
       "column_reorder": ctx_column_reorder}


# --------------------------------------------------------------------------- #
#                                  ПОРОЖДЕНИЕ                                  #
# --------------------------------------------------------------------------- #
def tag_pairs(model, base_id):
    """Ключ пары на КАЖДУЮ величину. Ставится ДО искажения и одинаков у обоих
    двойников: искажение меняет текст, а по тексту потом не сопоставишь."""
    for _, _, p in all_paras(model):
        for ch in p["chunks"]:
            if "e" in ch:
                ch["e"]["pair"] = "%s#%s" % (base_id, ch["e"]["id"])


def make_clean(model, base_id):
    m = copy.deepcopy(model)
    m["doc_id"] = "nz_" + base_id
    m["source"] = "noise:clean:" + base_id
    tag_pairs(m, base_id)
    return m


def make_dirty(model, base_id, classes, doc_i):
    m = copy.deepcopy(model)
    m["doc_id"] = "nzd_" + base_id
    m["source"] = "noise:dirty:" + base_id
    tag_pairs(m, base_id)
    rnd = random.Random("noise|%s" % base_id)

    singles = {k for k, v in eid_counts(m).items() if v == 1}
    counts = {}
    # 1) приёмы окружения — им нужно определённое место в документе, поэтому
    #    они выбирают его сами и делают это ПЕРВЫМИ; иначе все подходящие
    #    величины уже были бы заняты приёмами над значением.
    for cls in classes:
        if cls["scope"] != "context":
            continue
        n = CTX[cls["op"]](m, rnd, CONTEXT_SITES, singles)
        counts[cls["id"]] = counts.get(cls["id"], 0) + n

    # 2) приёмы над значением — по кругу, со сдвигом на номер документа, чтобы
    #    на каждый класс попадали РАЗНЫЕ виды данных, а не одни и те же.
    vals = [c for c in classes if c["scope"] == "value"]
    k = doc_i
    for ch in ent_chunks(m):
        e = ch["e"]
        if e.get("noise"):
            continue
        for attempt in range(len(vals)):
            cls = vals[(k + attempt) % len(vals)]
            if not NL.applies_to(cls, ch["s"]):
                continue
            s = NL.apply_value(cls, ch["s"], rnd)
            if s == ch["s"]:
                continue
            clean = ch["s"]
            ch["s"] = s
            _mark(ch, cls["id"], clean=clean)
            counts[cls["id"]] = counts.get(cls["id"], 0) + 1
            k += 1
            break
    return m, counts


def pick_base(gold, n):
    """База — документы ПРОСТОЙ структуры, только .txt.

    Сложная группа исключена сознательно: её собственные приёмы (вложенные
    таблицы, огромные абзацы, колонтитулы) — самостоятельный источник потерь,
    и в разнице «чистый/грязный» они бы смешались с искажениями распознавания.

    ТОЛЬКО .txt. Причин две, и обе не косметические.

    ПО СУЩЕСТВУ: искажения распознавания приезжают к нам ПЛОСКИМ ТЕКСТОМ.
    Пользователь вытащил договор из PDF — ни ранов Word, ни границ ячеек в
    результате нет; всё, что от таблицы осталось, это порядок строк (и на него
    заведён отдельный класс `column_reorder`). Приёмы корпуса v2, которым
    нужен .docx (`cell_split`, `format_split`), к этому классу порчи
    отношения не имеют.

    ПО ЗАПРЕТУ 7: офисный файл законен только в `tests/corpus/docs/`,
    `tests/corpus_v2/docs/` и `tests/fixtures/` (замок реальных документов,
    `.claude/hooks/guard.py:102`). Класть .docx шумового корпуса в чужой
    каталог или расширять список путей у замка — не задача этой сессии.
    """
    simple = [d for d in gold if d.get("structure_group") == "simple"
              and d["format"] == "txt"]
    return simple[:n]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=DEFAULT_DOCS)
    args = ap.parse_args(argv)

    classes = NL.load_noise_classes()
    gold = json.load(open(os.path.join(HERE, CL.GOLD_NAME), encoding="utf-8"))
    gold.sort(key=lambda d: d["doc_id"])
    base = pick_base(gold, args.docs)

    entries, counts = [], {}
    for i, g in enumerate(base):
        model = CL.load_model(HERE, g["doc_id"])
        clean = make_clean(model, g["doc_id"])
        dirty, c = make_dirty(model, g["doc_id"], classes, i)
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v
        for m in (clean, dirty):
            CL.render(m, NOISE_ROOT)
            entries.append(CL.gold_entry(m))

    entries.sort(key=lambda e: e["doc_id"])
    with open(GOLD_NOISE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)

    n_ent = sum(len(e["entities"]) for e in entries if e["doc_id"].startswith("nzd_"))
    n_noise = sum(1 for e in entries if e["doc_id"].startswith("nzd_")
                  for x in e["entities"] if x.get("noise"))
    print("ШУМОВОЙ КОРПУС: пар %d (документов %d) | классов искажения %d"
          % (len(base), len(entries), len(classes)))
    print("величин в грязных документах: %d, из них искажено: %d (%.1f%%)"
          % (n_ent, n_noise, 100.0 * n_noise / n_ent if n_ent else 0.0))
    print("--- искажённых вхождений по классам ---")
    ev = {c["id"]: c["evidence"] for c in classes}
    for c in classes:
        print("  %-16s %-14s %4d" % (c["id"], ev[c["id"]], counts.get(c["id"], 0)))
    missing = [c["id"] for c in classes if not counts.get(c["id"])]
    if missing:
        print("  НЕ ВСТРЕТИЛИСЬ НИ РАЗУ: %s" % ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
