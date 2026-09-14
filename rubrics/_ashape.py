import json, glob
def load(p):
    try: return json.load(open(p))
    except Exception: return {}
def rewards(run):
    r={}
    for res in glob.glob(f"{run}/**/result.json", recursive=True):
        for ev in ((load(res).get("stats") or {}).get("evals") or {}).values():
            for v,names in ev.get("reward_stats",{}).get("reward",{}).items():
                for n in names: r[n]=float(v)
    return r
def trial_checks(adir):
    out={}
    def T(d):
        if isinstance(d,dict):
            if "results" in d: return d["results"]
            if "trials" in d: return d["trials"]
            if "checks" in d: return [d]
        return []
    for aj in glob.glob(f"{adir}/**/analysis.json",recursive=True)+glob.glob(f"{adir}/analyze-*.json"):
        for t in T(load(aj)):
            n=t.get("trial_name")
            if n: out[n]={k:(v or {}) for k,v in (t.get("checks") or {}).items()}
    return out
