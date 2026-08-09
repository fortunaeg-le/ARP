# -*- coding: utf-8 -*-
"""Замер дерева на срезе: закрыто / правил / РЕГРЕССИИ прогоном / что осталось."""
import json, io, pathlib, sys
from collections import Counter
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import rdr

HERE = pathlib.Path(__file__).parent


def spans_overlap(a1, a2, b1, b2):
    return a1 < b2 and b1 < a2


def main():
    dump = json.load(open(HERE / "subset_dump.json", encoding="utf-8"))
    out = io.StringIO(); W = lambda *a: print(*a, file=out)

    closed = 0; partial = 0
    new_spans_total = 0
    reg_neg = []        # новая маска накрыла типизированный негатив
    reg_over = []       # новая маска вылезла за эталон (порча прозы)
    reg_wrongtype = []  # новая маска накрыла эталон ЧУЖОГО типа
    reg_on_ok = []      # новая маска легла на уже верно закрытую сущность и сдвинула её
    missed_examples = []
    per_type = Counter()

    for d in dump:
        G = d["text"]
        masks = [(m["s"], m["e"], m["t"]) for m in d["masks"]]
        gold = d["gold"]
        # сущности, которые конвейер НЕ закрыл ни одной маской своего типа
        def covered_by_own(g):
            return any(t == g["t"] and spans_overlap(s, e, g["s"], g["e"]) for s, e, t in masks)
        uncovered = [g for g in gold if not covered_by_own(g)]
        ok_gold = [g for g in gold if covered_by_own(g)]

        for c in rdr.candidates(G):
            v = rdr.apply_tree(c)
            if v is None:
                continue
            gtype, ln = v
            s, e = c["s"], c["s"] + ln
            if e <= s:
                continue
            # база уже права — слой молчит (правило метода: правим только ошибку)
            if any(t == gtype and spans_overlap(ms, me, s, e) for ms, me, t in masks):
                continue
            new_spans_total += 1
            per_type[gtype] += 1
            hit = None
            for g in gold:
                if spans_overlap(s, e, g["s"], g["e"]):
                    hit = g
                    break
            if hit is None:
                # никакой эталонной сущности здесь нет — это перебор (порча прозы)
                frag = G[s:e]
                neg = any(isinstance(n, dict) and n.get("text") and n["text"] in frag
                          for n in d["negatives"])
                (reg_neg if neg else reg_over).append((d["doc_id"], gtype, frag[:40]))
                continue
            if hit["t"] != gtype:
                reg_wrongtype.append((d["doc_id"], gtype, hit["t"], G[s:e][:40]))
                continue
            if hit in ok_gold:
                # эта сущность и так была закрыта верно; наш спан её только двигает
                if not (s <= hit["s"] and e >= hit["e"]):
                    reg_on_ok.append((d["doc_id"], gtype, G[s:e][:40]))
                continue
            # цель: непокрытая сущность своего типа
            if s <= hit["s"] and e >= hit["e"]:
                closed += 1
            else:
                partial += 1

        for g in uncovered:
            if len(missed_examples) < 40:
                missed_examples.append((d["doc_id"], g["t"], g["text"][:40]))

    n_uncov = sum(1 for d in dump for g in d["gold"]
                  if not any(m["t"] == g["t"] and spans_overlap(m["s"], m["e"], g["s"], g["e"])
                             for m in d["masks"]))
    W("# ДЕРЕВО ПРАВИЛ НА СРЕЗЕ (33 док.)")
    W("правил в дереве: %d (корневых %d, исключений %d)"
      % (rdr.n_rules(), len(rdr.TREE), rdr.n_rules() - len(rdr.TREE)))
    W("эталонных сущностей без маски своего типа (цель слоя): %d" % n_uncov)
    W("новых спанов слоя: %d  (%s)" % (new_spans_total, per_type.most_common()))
    W("ЗАКРЫТО целиком: %d   закрыто частично: %d" % (closed, partial))
    W()
    W("## РЕГРЕССИИ (прогон по всему срезу, не рассуждение)")
    W("  маска на типизированном негативе : %d" % len(reg_neg))
    W("  маска мимо любого эталона (проза) : %d" % len(reg_over))
    W("  маска чужого типа на эталоне      : %d" % len(reg_wrongtype))
    W("  сдвиг уже верной сущности         : %d" % len(reg_on_ok))
    W("  ВСЕГО регрессий: %d" % (len(reg_neg) + len(reg_over) + len(reg_wrongtype) + len(reg_on_ok)))
    for name, lst in (("негатив", reg_neg), ("проза", reg_over),
                      ("чужой тип", reg_wrongtype), ("сдвиг верной", reg_on_ok)):
        for x in lst[:12]:
            W("    [%s] %s" % (name, x))
    (HERE / "rdr_result.txt").write_text(out.getvalue(), encoding="utf-8")
    print("ok")


if __name__ == "__main__":
    main()
