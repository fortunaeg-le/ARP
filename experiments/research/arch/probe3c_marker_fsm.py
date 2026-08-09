# ARCH-PROBE, проба 3c: прототип маркерного конечного автомата вместо yargy.
# Вопрос: какую долю МАРКЕРНЫХ yargy-спанов воспроизводит дешёвая цепочка
# «маркер -> значение» на тех же окнах, и во сколько раз она быстрее.
# Городские безмаркерные спаны (Москва/Химки/РФ) не в счёте: их даёт LOC-слой.
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
C = ROOT / "tests" / "corpus"
CONFIG = str(ROOT / "entity_types.yaml")

from extractor import extract
import ner_detector

MARKERS = (
    "г|гор|город|обл|область|р-н|район|ул|улица|пр-кт|просп|проспект|пер|переулок|"
    "наб|набережная|бульвар|б-р|ш|шоссе|д|дом|к|корп|корпус|стр|строение|кв|квартира|"
    "офис|оф|пом|помещение|литер|литера|мкр|микрорайон|пос|посёлок|поселок|с|село|"
    "дер|деревня|респ|республика|край|тер|линия|проезд|пл|площадь|аллея|тупик|"
    "эт|этаж|каб|кабинет|комн|комната|вл|владение|уч|участок"
)
# элемент цепочки: маркер с необязательной точкой ИЛИ индекс ИЛИ номер дома
# ИЛИ слово с большой буквы (в т.ч. дефисное) ИЛИ число
_ELEM = re.compile(
    rf"(?:\b(?:{MARKERS})\b\.?)"                     # маркер
    rf"|(?:\b\d{{6}}\b)"                              # индекс
    rf"|(?:\b\d{{1,4}}\s?[а-яА-Я]?(?![\w])(?:/\d+)?)" # номер дома/кв
    rf"|(?:\b[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]*)*\b)" # СловоСБольшой (и дефисные)
    rf"|(?:\bим\b\.?)",
)
_MARK_ONLY = re.compile(rf"\b(?:{MARKERS})\b\.?$")
_IDX = re.compile(r"\b\d{6}\b")
_GLUE = re.compile(r"[ ,. ]*")


def fsm_spans(text: str) -> list[tuple[int, int]]:
    """Цепочки элементов, склеенных разделителями <=6 симв. из [ ,.NBSP];
    цепочка живёт, только если содержит маркер или индекс."""
    out = []
    elems = list(_ELEM.finditer(text))
    i = 0
    while i < len(elems):
        j = i
        while j + 1 < len(elems):
            gap = text[elems[j].end():elems[j + 1].start()]
            if len(gap) <= 6 and _GLUE.fullmatch(gap):
                j += 1
            else:
                break
        chunk = text[elems[i].start():elems[j].end()]
        if _MARK_ONLY.search(chunk) or _IDX.search(chunk) or re.search(
                rf"\b(?:{MARKERS})\b\.?", chunk):
            out.append((elems[i].start(), elems[j].end()))
        i = j + 1
    return out


def main():
    subset = json.load(open(C / "subset_iter.json", encoding="utf-8"))["doc_ids"][:12]
    docs = []
    for d in subset:
        p = C / "docs" / f"{d}.txt"
        if not p.exists():
            p = C / "docs" / f"{d}.docx"
        docs.append(extract(str(p)))

    # прогрев yargy
    ner_detector._addr_parse("г. Москва, ул. Тверская, д. 1")
    ner_detector._ADDR_PARSE_CACHE.clear()

    t_y = t_f = 0.0
    n_y_marker = 0
    exact = covered = missed = 0
    miss_examples = []
    over_chars = 0
    for doc in docs:
        for seg in doc.segments:
            text = seg.text
            t0 = time.perf_counter()
            yspans = ner_detector._addr_parse(text)
            t_y += time.perf_counter() - t0
            t0 = time.perf_counter()
            fspans = fsm_spans(text)
            t_f += time.perf_counter() - t0
            for s, e in yspans:
                st = text[s:e]
                if not (re.search(rf"\b(?:{MARKERS})\b\.?", st) or _IDX.search(st)):
                    continue  # безмаркерный (город) — зона LOC-слоя
                n_y_marker += 1
                hit = [f for f in fspans if f[0] <= s and f[1] >= e]
                part = [f for f in fspans if max(f[0], s) < min(f[1], e)]
                if any(f == (s, e) for f in fspans):
                    exact += 1
                elif hit:
                    covered += 1
                    over_chars += (hit[0][1] - hit[0][0]) - (e - s)
                else:
                    missed += 1
                    if len(miss_examples) < 12:
                        cov = sum(min(f[1], e) - max(f[0], s) for f in part)
                        miss_examples.append((st[:70], f"частично {cov}/{e-s}" if part else "мимо"))

    print(f"маркерных yargy-спанов: {n_y_marker}")
    print(f"  точное совпадение:  {exact} ({exact/n_y_marker:.1%})")
    print(f"  накрыт с запасом:   {covered} ({covered/n_y_marker:.1%}), лишних символов всего {over_chars}")
    print(f"  не накрыт целиком:  {missed} ({missed/n_y_marker:.1%})")
    print(f"время: yargy {t_y:.2f} c, автомат {t_f:.3f} c  (в {t_y/max(t_f,1e-9):.0f}× быстрее)")
    print("примеры ненакрытых:")
    for t, k in miss_examples:
        print(f"  [{k}] «{t}»")


if __name__ == "__main__":
    main()
