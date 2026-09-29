"""Rolling model-drift diagnostics on immutable paper-trade settlements."""
from __future__ import annotations
import math
from collections import defaultdict
import pandas as pd
from .prediction_store import load_settled


def _latest_unique(rows):
    latest={}
    for r in rows:
        if r.get("paper_eligible") is False: continue
        if float(r.get("settle_push") or 0)>0: continue
        try:p=round(float(r.get("point")),4)
        except (TypeError,ValueError):p=None
        k=(r.get("event_id"),r.get("sport_family"),r.get("market"),r.get("selection"),p)
        if k not in latest or str(r.get("recorded_at") or "")>str(latest[k].get("recorded_at") or ""): latest[k]=r
    return list(latest.values())


def _metrics(arr):
    b=[];roi=[];clv=[]
    for r in arr:
        try:
            push=float(r.get("push_prob") or 0); q=float(r.get("model_win_prob"))/max(1e-9,1-push); y=float(r.get("settle_win"));
            if y in (0,1): b.append((q-y)**2)
        except (TypeError,ValueError): pass
        try:roi.append(float(r.get("realized_roi")))
        except (TypeError,ValueError):pass
        try:
            x=float(r.get("clv_market_prob_pp"));
            if math.isfinite(x):clv.append(x)
        except (TypeError,ValueError):pass
    return {"n":len(arr),"brier":sum(b)/len(b) if b else None,"roi":sum(roi)/len(roi) if roi else None,"clv_pp":sum(clv)/len(clv) if clv else None}


def drift_rows(recent_n=60,min_total=100,settled_rows=None):
    groups=defaultdict(list)
    for r in _latest_unique(load_settled() if settled_rows is None else settled_rows): groups[(str(r.get("sport_family") or ""),str(r.get("market") or ""))].append(r)
    out=[]
    for (fam,mkt),arr in groups.items():
        arr=sorted(arr,key=lambda r:str(r.get("commence_time") or r.get("settled_at") or ""))
        if len(arr)<min_total:
            out.append({"sport_family":fam,"market":mkt,"n":len(arr),"status":"WAIT","reason":f"표본 {len(arr)} < {min_total}"});continue
        rn=min(recent_n,max(30,len(arr)//3)); recent=arr[-rn:]; base=arr[:-rn]
        if len(base)<40:
            out.append({"sport_family":fam,"market":mkt,"n":len(arr),"status":"WAIT","reason":"baseline < 40"});continue
        rm,bm=_metrics(recent),_metrics(base)
        bd=(rm["brier"]-bm["brier"]) if rm["brier"] is not None and bm["brier"] is not None else None
        cd=(rm["clv_pp"]-bm["clv_pp"]) if rm["clv_pp"] is not None and bm["clv_pp"] is not None else None
        status="ALERT" if (bd is not None and bd>=.025) or (cd is not None and cd<=-1.0) else "WATCH" if (bd is not None and bd>=.012) or (cd is not None and cd<=-.5) else "OK"
        reasons=[]
        if bd is not None:reasons.append(f"Brier Δ {bd:+.3f}")
        if cd is not None:reasons.append(f"CLV Δ {cd:+.2f}pp")
        out.append({"sport_family":fam,"market":mkt,"n":len(arr),"recent_n":rn,"baseline_n":len(base),"recent_brier":rm["brier"],"baseline_brier":bm["brier"],"brier_delta":bd,"recent_roi":rm["roi"],"baseline_roi":bm["roi"],"recent_clv_pp":rm["clv_pp"],"baseline_clv_pp":bm["clv_pp"],"clv_delta_pp":cd,"status":status,"reason":" / ".join(reasons)})
    return out
