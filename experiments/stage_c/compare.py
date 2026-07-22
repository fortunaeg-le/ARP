# -*- coding: utf-8 -*-
"""Сравнение двух sub_*.json (HEAD vs этап C) на ОДНОЙ подвыборке.
Печатает: per-type recall/exact/leak6/FP; поимённые ложные ADDRESS до/после;
ADDRESS exact-границы; round-trip. Запуск: compare.py sub_head.json sub_stagec.json"""
import os, sys, json, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "tests", "corpus")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "src")))


def load(p): return json.load(open(p, encoding="utf-8"))


def stats(res):
    out = collections.defaultdict(lambda: dict(n=0, found=0, exact=0, l6=0))
    for r in res:
        if r.get("outcome") != "processed": continue
        for e in r.get("entities", []):
            s = out[e["type"]]; s["n"] += 1
            if e["found"]:
                s["found"] += 1
                if e["exact"]: s["exact"] += 1
            if e["leak_v2"]["status"] != "none": s["l6"] += 1
    return out


def fp(res):
    c = collections.Counter()
    names = collections.defaultdict(list)
    for r in res:
        if r.get("outcome") != "processed": continue
        for x in r.get("false_positives", []):
            if x.get("on_negative"):
                c[x["gtype"]] += 1
            names[x["gtype"]].append((r["doc_id"], x["text"][:40], x.get("on_negative"), x.get("cross_type")))
    return c, names


def rtf(res):
    return sorted(r["doc_id"] for r in res if r.get("outcome") == "processed" and not r["roundtrip_ok"])


a = load(sys.argv[1]); b = load(sys.argv[2])
sa, sb = stats(a), stats(b)
print("%-10s | %-18s | %-18s | %-16s" % ("type", "recall H→C", "exact(f) H→C", "leak6 H→C"))
for t in ["ADDRESS", "PER", "ORG", "INN", "OGRN", "KPP", "ACCOUNT", "BIK", "PHONE", "EMAIL", "PASSPORT", "SNILS", "BIRTHDATE"]:
    x, y = sa.get(t, dict(n=0, found=0, exact=0, l6=0)), sb.get(t, dict(n=0, found=0, exact=0, l6=0))
    pc = lambda v, n: (100.0 * v / n) if n else 0.0
    print("%-10s | %6.1f→%6.1f%% | %6.1f→%6.1f%% | %5.1f→%5.1f%% (n=%d)" % (
        t, pc(x['found'], x['n']), pc(y['found'], y['n']),
        pc(x['exact'], x['found']), pc(y['exact'], y['found']),
        pc(x['l6'], x['n']), pc(y['l6'], y['n']), y['n']))

fa, na = fp(a); fb, nb = fp(b)
print("\nFP-on-neg by type H→C:")
for t in sorted(set(list(fa) + list(fb))):
    print("  %-10s %d → %d" % (t, fa[t], fb[t]))
print("  TOTAL %d → %d" % (sum(fa.values()), sum(fb.values())))

for typ in ("ADDRESS", "PER"):
    print("\n=== Ложные %s (все, cross+neg) HEAD (%d) ===" % (typ, len(na[typ])))
    for d, t, neg, cr in na[typ]: print("  H %-28s %-40r neg=%s cross=%s" % (d, t, neg, cr))
    print("--- Ложные %s (все) ЭТАП C (%d) ---" % (typ, len(nb[typ])))
    for d, t, neg, cr in nb[typ]: print("  C %-28s %-40r neg=%s cross=%s" % (d, t, neg, cr))

print("\nround-trip fails HEAD:", rtf(a))
print("round-trip fails STAGE-C:", rtf(b))
