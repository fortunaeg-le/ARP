# -*- coding: utf-8 -*-
import os, sys, json, collections
HERE=os.path.dirname(os.path.abspath(__file__))
CORPUS=os.path.abspath(os.path.join(HERE,"..","..","tests","corpus"))
sys.path.insert(0,os.path.abspath(os.path.join(HERE,"..","..","src")))
sys.path.insert(0,CORPUS)
import measure_lib as ML

def load(p): return json.load(open(p,encoding='utf-8'))

def per_metrics(res):
    """recall, exact-border (среди found), leak_v2>=6, для типа PER."""
    n=found=exact=full=leak6=0
    for r in res:
        if r.get("outcome")!="processed": continue
        for e in r.get("entities",[]):
            if e["type"]!="PER": continue
            n+=1
            if e["found"]:
                found+=1
                if e["exact"]: exact+=1
                if e["full"]: full+=1
                if e["leak_v2"]["status"]!="none": leak6 += 1 if e["leak_v2"].get("status") else 0
            if e["leak_v2"]["status"]!="none": leak6_all=1
    return n,found,exact,full

def full_stats(res):
    out={}
    for r in res:
        if r.get("outcome")!="processed": continue
        for e in r.get("entities",[]):
            t=e["type"]; s=out.setdefault(t,dict(n=0,found=0,exact=0,leak6=0,leak8=0))
            s["n"]+=1
            if e["found"]:
                s["found"]+=1
                if e["exact"]: s["exact"]+=1
            st=e["leak_v2"]["status"]
            v2=e["leak_v2"]
            # leak thresholds mirror harness: partial/full count; >=8 stricter via fragments? use status + soft/strict flags if present
            if st!="none":
                s["leak6"]+=1
                if v2.get("strong") or v2.get("status")=="full" or v2.get("hard"): pass
            # approximate leak8 by 'status'=='full' OR flag
    return out

def leak_counts(res):
    """точный подсчёт leak_v2>=6 и >=8 как в gate: по полям soft/strict если есть, иначе status."""
    out=collections.defaultdict(lambda: dict(n=0,l6=0,l8=0,found=0,exact=0,masks=0))
    for r in res:
        if r.get("outcome")!="processed": continue
        for e in r.get("entities",[]):
            t=e["type"]; s=out[t]; s["n"]+=1
            if e["found"]:
                s["found"]+=1
                if e["exact"]: s["exact"]+=1
            v2=e["leak_v2"]
            # harness stores status none/partial/full; thresholds encoded via 'soft'/'strict' bool if present
            if "leak6" in v2: 
                s["l6"]+=1 if v2["leak6"] else 0; s["l8"]+=1 if v2.get("leak8") else 0
            else:
                if v2["status"]!="none": s["l6"]+=1
                if v2["status"]=="full": s["l8"]+=1
    return out

def fp_by_type(res):
    fp=collections.Counter()
    for r in res:
        if r.get("outcome")!="processed": continue
        for x in r.get("false_positives",[]):
            if x.get("on_negative"): fp[x["gtype"]]+=1
    return fp

def false_per_names(res):
    names=[]
    for r in res:
        if r.get("outcome")!="processed": continue
        for x in r.get("false_positives",[]):
            if x["gtype"]=="PER":
                names.append((r["doc_id"], x["text"], x.get("on_negative"), x.get("cross_type")))
    return names

def masking(res):
    recs=[]
    for r in res:
        if r.get("outcome")!="processed": continue
        recs+=r.get("masks",[])
    return ML.mc_aggregate(recs)["total"]

a=load(sys.argv[1]); b=load(sys.argv[2])  # a=head(A'), b=stageB
print("crashes A':", sum(1 for r in a if r.get('outcome')=='crashed'),
      " B:", sum(1 for r in b if r.get('outcome')=='crashed'))
la=leak_counts(a); lb=leak_counts(b)
print("\n%-10s | %14s | %14s | %14s"%("type","recall A'→B","exact A'→B","leak6 A'→B"))
for t in ["PER","ORG","ADDRESS","INN","OGRN","KPP","ACCOUNT","BIK","PHONE","EMAIL","PASSPORT","SNILS","BIRTHDATE"]:
    sa=la.get(t,dict(n=0,found=0,exact=0,l6=0)); sb=lb.get(t,dict(n=0,found=0,exact=0,l6=0))
    def pct(x,n): return (100.0*x/n) if n else 0.0
    print("%-10s | %5.1f→%5.1f%% | %5.1f→%5.1f%% | %5.1f→%5.1f%% (n=%d)"%(
        t, pct(sa['found'],sa['n']),pct(sb['found'],sb['n']),
        pct(sa['exact'],sa['found']),pct(sb['exact'],sb['found']),
        pct(sa['l6'],sa['n']),pct(sb['l6'],sb['n']), sb['n']))
fa=fp_by_type(a); fb=fp_by_type(b)
print("\nFP-on-neg by type A'→B:")
for t in sorted(set(list(fa)+list(fb))):
    print("  %-10s %d → %d"%(t,fa[t],fb[t]))
print("  TOTAL %d → %d"%(sum(fa.values()),sum(fb.values())))
print("\nЛожные PER (on-negative) в B:")
fpn=false_per_names(b)
for d,txt,why,cross in fpn: print("  ",d,repr(txt),"neg=",why,"cross=",cross)
print("  ИТОГО ложных PER:",len(fpn))
ma=masking(a); mb=masking(b)
print("\nmasking_correctness A' → B:")
for k in ["a","b","c"]:
    print("  %s: %.2f%% → %.2f%%"%(k.upper(), 100*ma[k+"_ok"]/ma["n"] if ma["n"] else 0, 100*mb[k+"_ok"]/mb["n"] if mb["n"] else 0))
