# -*- coding: utf-8 -*-
"""R-RDR часть 3: сколько остатка объясняется падежом (морфологией), числом.

Метод: берём ТОЛЬКО чистый текст (trick=None) — там нет вёрстки, регистра и
омоглифов, значит разница между именительным и косвенным падежом и есть вклад
морфологии. Падеж определяем pymorphy2 по первому словарному слову значения.
Избыток = (доля ошибок у косвенных − доля ошибок у именительных) × N косвенных.
"""
import json, io, pathlib, re
from collections import Counter, defaultdict
from natasha import MorphVocab

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).parent
# ВАЖНО: прямой pymorphy2 0.9.1 НЕ поднимается на Python 3.11 этого venv
# (inspect.getargspec удалён). Тот же словарь доступен через natasha.MorphVocab,
# она отдаёт OpencorporaTag — им и пользуемся.
MV = MorphVocab()
WORD = re.compile(r"[А-Яа-яЁё]{3,}")

def case_of(text):
    """Падеж значения по первому склоняемому слову.

    Разбор неоднозначен, поэтому косвенным считаем ТОЛЬКО слово, у которого ни
    один вариант не даёт единственное число именительного падежа (консервативно:
    сомнение трактуется в пользу именительного, вклад морфологии не завышается).
    """
    for w in WORD.findall(text)[:3]:
        cases = set()
        for form in MV(w):
            tag = form[1]
            c = getattr(tag, "case", None)
            if c:
                cases.add((c, getattr(tag, "number", None)))
        if not cases:
            continue
        if any(c == "nomn" and n != "plur" for c, n in cases):
            return "nomn"
        return "obl"
    return "?"

def err_class(e):
    b = e.get("bnd") or {}
    d = b.get("direction")
    if d == "not_found" or not e.get("found"):
        return "не найдено"
    if d in ("shorter", "both"):
        return "недобор"
    if d == "longer":
        return "перебор"
    return "ok"

def main():
    d = json.load(open(ROOT / "tests/corpus/results_gate_current.json", encoding="utf-8"))
    out = io.StringIO(); W = lambda *a: print(*a, file=out)
    W("# Вклад падежа в остаток — только чистый текст (trick=None), 324 док.")
    W()
    grand_exc = 0.0
    for t in ("PER", "ORG", "ADDRESS"):
        rows = defaultdict(lambda: Counter())
        samples = defaultdict(list)
        for doc in d:
            for e in doc["entities"]:
                if e["type"] != t or e.get("trick") is not None:
                    continue
                c = case_of(e["text"])
                k = err_class(e)
                rows[c][k] += 1
                rows[c]["N"] += 1
                if k != "ok" and len(samples[c]) < 8:
                    samples[c].append((k, e["text"][:50]))
        W("## %s" % t)
        W("  %-6s %6s %7s %8s | %s" % ("падеж", "N", "ошибок", "доля", "разбивка"))
        rates = {}
        for c in ("nomn", "obl", "?"):
            r = rows[c]
            if not r["N"]:
                continue
            bad = r["N"] - r["ok"]
            rates[c] = (r["N"], bad, bad / r["N"])
            W("  %-6s %6d %7d %7.1f %% | %s" % (c, r["N"], bad, 100.0 * bad / r["N"],
                ", ".join("%s %d" % kv for kv in r.most_common() if kv[0] not in ("N", "ok"))))
        if "nomn" in rates and "obl" in rates:
            n_obl, bad_obl, r_obl = rates["obl"]
            _, _, r_nom = rates["nomn"]
            exc = (r_obl - r_nom) * n_obl
            grand_exc += max(exc, 0.0)
            W("  ИЗБЫТОК косвенных над именительным: (%.3f − %.3f) × %d = %+.0f сущностей"
              % (r_obl, r_nom, n_obl, exc))
        for c in ("nomn", "obl"):
            if samples[c]:
                W("  примеры ошибок (%s): %s" % (c, "; ".join("%s «%s»" % s for s in samples[c][:5])))
        W()
    W("ИТОГО избыток, объяснимый падежом (чистый текст, три типа): %.0f сущностей" % grand_exc)
    ents = [e for doc in d for e in doc["entities"]]
    bad = sum(1 for e in ents if err_class(e) != "ok" or e.get("leaked"))
    W("Остаток ошибок всего: %d. Доля морфологии: %.1f %%." % (bad, 100.0 * grand_exc / bad))
    (HERE / "morph_share.txt").write_text(out.getvalue(), encoding="utf-8")
    print("ok")

if __name__ == "__main__":
    main()
