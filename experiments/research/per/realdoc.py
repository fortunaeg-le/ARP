# -*- coding: utf-8 -*-
"""realdoc.py — профиль механизмов на РЕАЛЬНОМ договоре владельца.

    venv/Scripts/python.exe experiments/research/per/realdoc.py <путь.docx>

ЗАПРЕТ 7 ДЕЙСТВУЕТ: ни одной строки документа в вывод не попадает — только
СЧЁТА. Никаких примеров, никаких фрагментов, никаких ключей.

Разметки у реального документа нет, поэтому «полнота» здесь не считается.
Считается ДВА независимых от разметки числа:

 1. ПРОФИЛЬ МЕХАНИЗМОВ САМОГО ТЕКСТА — сколько в нём вообще есть омоглифов,
    невидимых символов, переводов строки внутри слова, аномалий регистра.
    Это прямой ответ на «тот же профиль, что у корпуса, или другой»: корпус
    мутирован генератором нарочно, реальный документ — нет.

 2. ТЕНЕВОЙ ЭТАЛОН — места, которые словарь разбирает как запись человека
    (фамилия + имя/отчество/инициалы), и доля таких мест, накрытых маской PER.
    Это НЕ полнота: теневой эталон и пропускает (безъякорные фамилии), и
    выдумывает (однофамильцы улиц). Число читается как индикатор порядка,
    не как измерение.
"""
import io
import os
import re
import sys
import collections
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import anchor_registry as AR                   # noqa: E402
from extractor import extract                  # noqa: E402
from pipeline import run_detection             # noqa: E402
from tokenizer import resolve_for_masking, apply_masking, build_plain_text  # noqa: E402
import type_policy                             # noqa: E402
from causal import fix_homo, CYR_RE, LAT2CYR   # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def main():
    path = sys.argv[1]
    doc = extract(path)
    plain = build_plain_text(doc)
    ents = run_detection(doc, CONFIG)
    resolved = resolve_for_masking(doc, ents, CONFIG)
    anon, kept = apply_masking(doc, resolved, CONFIG, type_policy.MAXIMUM)

    out = io.open(os.path.join(HERE, "realdoc.txt"), "w", encoding="utf-8")
    w = out.write
    w("документ: %s (имя файла не печатается)\n" % os.path.basename(path)[:0])
    w("символов в тексте: %d, сегментов: %d\n\n" % (len(plain), len(doc.segments)))

    w("=== 1. ПРОФИЛЬ МЕХАНИЗМОВ САМОГО ТЕКСТА ===\n")
    invis = sum(1 for c in plain if unicodedata.category(c) == "Cf")
    homo_words = 0
    lat_in_cyr = 0
    for m in WORD.finditer(plain):
        wd = m.group(0)
        if CYR_RE.search(wd) and re.search(r"[A-Za-z]", wd):
            homo_words += 1
            lat_in_cyr += sum(1 for c in wd if c in LAT2CYR)
    words = list(WORD.finditer(plain))
    up = sum(1 for m in words if m.group(0).isupper() and len(m.group(0)) > 2)
    lo = sum(1 for m in words if m.group(0).islower())
    w("слов всего:                         %6d\n" % len(words))
    w("невидимых символов (Cf):            %6d\n" % invis)
    w("слов со смешением кир/лат:          %6d (символов подмены %d)\n"
      % (homo_words, lat_in_cyr))
    w("слов ЦЕЛИКОМ ВЕРХНИМ регистром:     %6d (%.2f%%)\n"
      % (up, 100.0 * up / len(words) if words else 0))
    w("слов целиком нижним:                %6d (%.2f%%)\n"
      % (lo, 100.0 * lo / len(words) if words else 0))
    w("переводов строки в тексте:          %6d\n" % plain.count("\n"))
    w("сегментов, оканчивающихся на слово без точки (кандидат на разрыв "
      "значения вёрсткой): %d\n\n"
      % sum(1 for s in doc.segments
            if s.text and s.text.rstrip() and s.text.rstrip()[-1].isalpha()))

    w("=== 2. ТЕНЕВОЙ ЭТАЛОН ЛЮДЕЙ (не разметка, индикатор порядка) ===\n")
    # места «фамилия + имя/отчество/инициал» по словарю, в plain-координатах
    shadow = []
    toks = words
    parts = [AR._nameparts(t.group(0)) for t in toks]
    i = 0
    while i < len(toks):
        if "surn" in parts[i] or "name" in parts[i] or "patr" in parts[i]:
            j = i
            while j + 1 < len(toks) and toks[j + 1].start() - toks[j].end() <= 3 \
                    and (parts[j + 1] or AR._is_initial(toks[j + 1].group(0))):
                j += 1
            roles = set()
            for k in range(i, j + 1):
                roles |= set(parts[k])
            init = any(AR._is_initial(toks[k].group(0)) for k in range(i, j + 1))
            if ("surn" in roles and (init or "name" in roles or "patr" in roles)) \
                    or ("name" in roles and "patr" in roles and j - i >= 2):
                shadow.append((toks[i].start(), toks[j].end(), j - i + 1))
            i = j + 1
        else:
            i += 1

    # маски PER в plain-координатах: считаем по сегментам
    per_spans = []
    off = 0
    seg_off = {}
    for s in doc.segments:
        seg_off[s.id] = off
        off += len(s.text) + 1
    for e in kept:
        if not str(e.entity_type).upper().startswith("PER"):
            continue
        b = seg_off.get(e.segment_id)
        if b is None:
            continue
        per_spans.append((b + e.start, b + e.end))

    covered = 0
    for a, b, _n in shadow:
        if any(not (b <= x or a >= y) for x, y in per_spans):
            covered += 1
    w("мест «человек» по словарю (теневой эталон): %d\n" % len(shadow))
    w("масок PER выставлено:                        %d\n" % len(per_spans))
    w("теневых мест, накрытых маской PER:           %d (%.1f%%)\n"
      % (covered, 100.0 * covered / len(shadow) if shadow else 0))
    w("теневых мест БЕЗ маски:                      %d\n\n"
      % (len(shadow) - covered))

    w("=== 3. МЕХАНИЗМЫ НЕПОКРЫТЫХ ТЕНЕВЫХ МЕСТ ===\n")
    mech = collections.Counter()
    for a, b, n in shadow:
        if any(not (b <= x or a >= y) for x, y in per_spans):
            continue
        frag = plain[a:b]
        marks = []
        if CYR_RE.search(frag) and re.search(r"[A-Za-z]", frag):
            marks.append("подменённые буквы")
        if any(unicodedata.category(c) == "Cf" for c in frag):
            marks.append("невидимые символы")
        if "\n" in frag:
            marks.append("разрыв вёрсткой")
        letters = [c for c in frag if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            marks.append("регистр: ВЕРХНИЙ")
        elif letters and all(c.islower() for c in letters):
            marks.append("регистр: нижний")
        if n <= 2:
            marks.append("облик: короткая запись (2 слова и меньше)")
        mech[tuple(marks) or ("НИ ОДНОГО ПРИЗНАКА",)] += 1
    for k, v in mech.most_common(15):
        w("%5d  %s\n" % (v, " + ".join(k)))
    out.close()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
