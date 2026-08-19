# -*- coding: utf-8 -*-
"""ЭТАП INSTR-SEAM, доказательство УЗОСТИ починки прибора линии «г» (masking B).

Гоняет корпус v1 ОДИН раз на НЕИЗМЕННОМ src/ и считает КАЖДУЮ маску ДВАЖДЫ:
старым прибором (`seam=None` — прежнее поведение слово в слово) и новым
(`seam=` интервалы немаскируемого шва). Один прогон = одна и та же детекция,
поэтому любое расхождение — прибор и только прибор.

Печатает и складывает в JSON:
  * число масок и число ОЦЕНЁННЫХ масок обоими приборами (обязано совпасть);
  * все расхождения ПОИМЁННО: документ, тип, текст эталона, и КАЖДЫЙ зазор,
    который новый прибор перешагнул, — с его точным содержимым;
  * проверку узости по каждому зазору: состоит ли он ТОЛЬКО из SEAM_CHARS,
    лежит ли ВНЕ прочитанных сегментов, лежит ли МЕЖДУ двумя масками;
  * масштаб выбора «шов по всему пакету, а не только по телу» — сколько
    прощённых зазоров лежит вне [body_start, body_end).

Прибор гейта НЕ подменяется: обе оценки идут через ту же mc_check_bc.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))

import measure_lib as ML          # noqa: E402
import run_measurement as RM      # noqa: E402

CTX = {"doc_id": None, "G": None, "body": (0, 0), "segs": None, "offs": None}
DIFFS = []
TALLY = {"n_masks": 0, "n_scored_old": 0, "n_scored_new": 0,
         "b_ok_old": 0, "b_ok_new": 0, "flip_to_ok": 0, "flip_to_bad": 0}

_orig_bc = ML.mc_check_bc
_orig_pt1 = ML.pt1_text
_orig_body = ML.body_offset
_orig_seam = ML.seam_spans
_orig_proc = RM.process_doc


def _pt1(path):
    t = _orig_pt1(path)
    CTX["G"] = t
    return t


def _body(path, text):
    b = _orig_body(path, text)
    CTX["body"] = (b, len(text))
    return b


def _seam_spans(segments, offsets, full_text, body_start, body_end):
    out = _orig_seam(segments, offsets, full_text, body_start, body_end)
    if body_start == 0 and body_end == len(full_text):
        CTX["segs"] = segments
        CTX["offs"] = offsets
    else:
        CTX["body"] = (body_start, body_end)
    return out


def _proc(d):
    CTX["doc_id"] = d["doc_id"]
    return _orig_proc(d)


def _seg_cover(pos):
    """Принадлежит ли позиция PT-1 хоть одному ЛОКАЛИЗОВАННОМУ сегменту."""
    segs, offs = CTX["segs"], CTX["offs"]
    if segs is None:
        return None
    for seg in segs:
        base = offs.get(seg.id)
        if base is None or not seg.text:
            continue
        if base <= pos < base + len(seg.text):
            return True
    return False


def _gaps(gs, ge, covering):
    """Зазоры внутри [gs,ge) между масками covering + флаг «между двумя масками»."""
    out = []
    cur = gs
    for s, e in sorted(covering):
        if s > cur:
            out.append({"start": cur, "end": min(s, ge),
                        "between_two_masks": cur > gs})
        cur = max(cur, e)
        if cur >= ge:
            break
    if cur < ge:
        out.append({"start": cur, "end": ge, "between_two_masks": False})
    return [g for g in out if g["end"] > g["start"]]


def _bc(mask, golds, all_masks, seam=None):
    new = _orig_bc(mask, golds, all_masks, seam=seam)
    old = _orig_bc(mask, golds, all_masks, seam=None)
    TALLY["n_masks"] += 1
    TALLY["n_scored_old"] += int(bool(old["scored"]))
    TALLY["n_scored_new"] += int(bool(new["scored"]))
    if old["scored"]:
        TALLY["b_ok_old"] += int(bool(old["b_ok"]))
    if new["scored"]:
        TALLY["b_ok_new"] += int(bool(new["b_ok"]))
    if old["b_ok"] != new["b_ok"] or old["scored"] != new["scored"] or \
       old["c_ok"] != new["c_ok"] or old["gold_type"] != new["gold_type"]:
        if new["b_ok"] and not old["b_ok"]:
            TALLY["flip_to_ok"] += 1
        else:
            TALLY["flip_to_bad"] += 1
        ms, me = mask["start"], mask["end"]
        ov = [g for g in golds if ML._iv_overlap(ms, me, g["start"], g["end"])]
        g = max(ov, key=lambda g: min(me, g["end"]) - max(ms, g["start"]))
        gs, ge = g["start"], g["end"]
        covering = [(m["start"], m["end"]) for m in all_masks
                    if ML._iv_overlap(m["start"], m["end"], gs, ge)]
        G = CTX["G"] or ""
        bs, be = CTX["body"]
        gaps = []
        for gap in _gaps(gs, ge, covering):
            txt = G[gap["start"]:gap["end"]]
            gaps.append({
                "text": txt,
                "len": len(txt),
                "only_seam_chars": bool(txt) and all(ch in ML.SEAM_CHARS for ch in txt),
                "inside_read_segment": any(_seg_cover(i) for i in
                                           range(gap["start"], gap["end"])),
                "between_two_masks": gap["between_two_masks"],
                "in_body": gap["start"] >= bs and gap["end"] <= be,
            })
        DIFFS.append({
            "doc_id": CTX["doc_id"], "mask_type": mask["gtype"],
            "gold_type": g["type"], "gold_text": g["text"],
            "old_b_ok": old["b_ok"], "new_b_ok": new["b_ok"],
            "old_scored": old["scored"], "new_scored": new["scored"],
            "old_c_ok": old["c_ok"], "new_c_ok": new["c_ok"],
            "gaps": gaps,
        })
    return new


def main():
    # workers=1 ОБЯЗАТЕЛЕН: подмены живут в памяти ЭТОГО процесса и до воркеров
    # Pool не доезжают — с воркерами проба молча вернула бы «расхождений нет».
    ML.mc_check_bc = _bc
    ML.pt1_text = _pt1
    ML.body_offset = _body
    ML.seam_spans = _seam_spans
    RM.process_doc = _proc
    gold = RM.load_gold()
    print("документов: %d (однопоточно, двойной счёт каждой маски)" % len(gold))
    RM.run_all(gold, verbose=True, workers=1)

    print("\n=== СЧЁТ ===")
    print("  масок всего:              %d" % TALLY["n_masks"])
    print("  оценено (scored) старым:  %d" % TALLY["n_scored_old"])
    print("  оценено (scored) новым:   %d" % TALLY["n_scored_new"])
    print("  b_ok старым:              %d  (%.2f%%)" % (
        TALLY["b_ok_old"], 100.0 * TALLY["b_ok_old"] / TALLY["n_scored_old"]))
    print("  b_ok новым:               %d  (%.2f%%)" % (
        TALLY["b_ok_new"], 100.0 * TALLY["b_ok_new"] / TALLY["n_scored_new"]))
    print("  расхождений записей:      %d  (в «верно» %d, в «брак» %d)" % (
        len(DIFFS), TALLY["flip_to_ok"], TALLY["flip_to_bad"]))

    bad = []
    tolerated = 0
    out_of_body = 0
    for r in DIFFS:
        if not r["new_b_ok"] or r["old_b_ok"]:
            bad.append(("направление не «брак->верно»", r))
        if r["old_scored"] != r["new_scored"] or r["old_c_ok"] != r["new_c_ok"]:
            bad.append(("тронуто не только B", r))
        for gap in r["gaps"]:
            if not (gap["only_seam_chars"] and gap["between_two_masks"]
                    and not gap["inside_read_segment"]):
                bad.append(("зазор не шов: %r" % gap["text"], r))
            else:
                tolerated += 1
                if not gap["in_body"]:
                    out_of_body += 1

    print("\n=== УЗОСТЬ ===")
    print("  прощённых зазоров:        %d" % tolerated)
    print("  из них ВНЕ тела:          %d  (цена выбора «шов по всему пакету»)" % out_of_body)
    print("  нарушений узости:         %d" % len(bad))
    for why, r in bad[:20]:
        print("   !! %s | %s | %s %r" % (why, r["doc_id"], r["gold_type"],
                                         r["gold_text"]))

    from collections import Counter
    by_type = Counter(r["gold_type"] for r in DIFFS)
    print("\n=== РАСХОЖДЕНИЯ ПО ТИПУ ЭТАЛОНА ===")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print("  %-16s %d" % (t, n))
    gap_texts = Counter(gap["text"] for r in DIFFS for gap in r["gaps"])
    print("\n=== СОДЕРЖИМОЕ ПРОЩЁННЫХ ЗАЗОРОВ (дословно) ===")
    for t, n in sorted(gap_texts.items(), key=lambda kv: -kv[1]):
        print("  %-10r %d" % (t, n))

    with open(os.path.join(HERE, "instr_seam_narrowness.json"), "w",
              encoding="utf-8") as f:
        json.dump({"tally": TALLY, "diffs": DIFFS}, f, ensure_ascii=False, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
