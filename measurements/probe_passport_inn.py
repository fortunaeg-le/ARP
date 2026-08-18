# -*- coding: utf-8 -*-
"""Сличение до/после на конкретных документах: чем именно закрыт паспорт."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
base = {d["doc_id"]: d for d in json.load(open(os.path.join(ROOT,"tests","corpus","results_baseline.json"), encoding="utf-8"))}
cur  = {d["doc_id"]: d for d in json.load(open(os.path.join(ROOT,"tests","corpus","results_gate_current.json"), encoding="utf-8"))}
out = open(os.path.join(HERE,"probe_passport_inn.txt"),"w",encoding="utf-8")
def say(*a): out.write(" ".join(str(x) for x in a)+"\n")

def clean(s):
    for ch in ("⁠", "\xad", "​", "‍", " "):
        s = s.replace(ch, "")
    return s.replace("\xa0", " ")

for did, needle in [("lease_0003","174310"), ("loan_0009","537618"),
                    ("works_0011","088328"), ("services_0001","995117")]:
    say("\n################ %s  (значение …%s) ################" % (did, needle))
    for tag, dump in (("БЫЛО (results_baseline)", base), ("СТАЛО (results_gate_current)", cur)):
        d = dump[did]
        say("\n--- %s ---" % tag)
        say("  МАСКИ, накрывшие это значение:")
        for m in d["masks"]:
            if needle in clean(m["text"]):
                say("    маска %-10s token=%-14s scored=%s c_ok=%s эталон=%s  text=%r"
                    % (m["gtype"], m["token"], m["scored"], m["c_ok"], m["gold_type"], m["text"]))
        say("  ЭТАЛОННЫЕ СУЩНОСТИ с этим значением:")
        for g in d["entities"]:
            if needle in clean(g.get("text", "")):
                say("    gold %-10s found=%s exact=%s bnd=%s leak_v2=%s text=%r"
                    % (g.get("type"), g.get("found"), g.get("exact"), g.get("bnd"),
                       (g.get("leak_v2") or {}).get("status"), g.get("text")))
out.close()
print("ok")
