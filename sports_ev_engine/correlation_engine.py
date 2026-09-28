"""Historical, correlation-aware parlay probability adjustments."""
from __future__ import annotations
import math
from collections import defaultdict
from zoneinfo import ZoneInfo
import pandas as pd

KST=ZoneInfo("Asia/Seoul")


def _num(v,default=float("nan")):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except (TypeError,ValueError): return default


def _cat(x): return f"{x.get('sport_family','')}|{x.get('market','')}"


def historical_correlations(settled_rows=None,min_days=20):
    if settled_rows is None:
        try:
            from .prediction_store import load_settled
            settled_rows=load_settled()
        except Exception: settled_rows=[]
    # Only candidate snapshots; dedupe pick identity to the last settled snapshot.
    latest={}
    for r in settled_rows or []:
        if r.get("paper_eligible") is False: continue
        if r.get("paper_eligible") is None:
            rec=pd.to_datetime(r.get("recorded_at"),utc=True,errors="coerce"); kick=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
            if not pd.isna(rec) and not pd.isna(kick) and rec>=kick: continue
        if not bool(r.get("v3_candidate")): continue
        if _num(r.get("settle_push"),0)>0: continue
        y=_num(r.get("settle_win"))
        if y not in (0.0,1.0): continue
        ts=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        if pd.isna(ts): continue
        day=str(ts.tz_convert(KST).date())
        ident=(r.get("event_id"),r.get("market"),r.get("selection"),r.get("point"),_cat(r))
        rec=(str(r.get("recorded_at") or ""),day,y)
        if ident not in latest or rec[0]>latest[ident][0]: latest[ident]=rec
    byday=defaultdict(lambda:defaultdict(list))
    for ident,(_,day,y) in latest.items(): byday[day][ident[-1]].append(y)
    cats=sorted({c for d in byday.values() for c in d})
    matrix={}
    for i,a in enumerate(cats):
        for b in cats[i:]:
            pairs=[]
            for d in byday.values():
                if a in d and b in d:
                    pairs.append((sum(d[a])/len(d[a]),sum(d[b])/len(d[b])))
            n=len(pairs); rho=0.0
            if n>=min_days:
                xa=[x for x,_ in pairs]; xb=[y for _,y in pairs]
                ma=sum(xa)/n; mb=sum(xb)/n
                va=sum((x-ma)**2 for x in xa); vb=sum((y-mb)**2 for y in xb)
                if va>1e-9 and vb>1e-9:
                    rho=sum((x-ma)*(y-mb) for x,y in pairs)/math.sqrt(va*vb)
                    rho=max(-.15,min(.20,rho))
            matrix[(a,b)]={"rho":rho,"n":n}; matrix[(b,a)]={"rho":rho,"n":n}
    return matrix


def correlation_adjusted_hit(legs, settled_rows=None, matrix=None):
    legs=list(legs)
    ps=[max(1e-6,min(1-1e-6,_num(x.get("daily_adjusted_prob"),_num(x.get("model_win_prob"),.5)))) for x in legs]
    naive=math.prod(ps)
    if len(legs)<2: return naive,{"naive":naive,"adjusted":naive,"pairs_used":0,"avg_rho":0.0,"coverage":1.0}
    mat=matrix if matrix is not None else historical_correlations(settled_rows)
    ratios=[]; rhos=[]; used=0
    for i in range(len(legs)):
        for j in range(i+1,len(legs)):
            p,q=ps[i],ps[j]; info=mat.get((_cat(legs[i]),_cat(legs[j])),{"rho":0.0,"n":0})
            rho=float(info.get("rho") or 0.0)
            if int(info.get("n") or 0)>=20: used+=1; rhos.append(rho)
            joint=p*q+rho*math.sqrt(p*(1-p)*q*(1-q))
            joint=max(max(0,p+q-1),min(min(p,q),joint))
            ratios.append(max(.88,min(1.12,joint/max(1e-9,p*q))))
    exponent=1/max(1,len(legs)-1)
    factor=math.prod(r**exponent for r in ratios) if ratios else 1.0
    adjusted=max(1e-9,min(0.999999,naive*factor))
    total_pairs=len(legs)*(len(legs)-1)//2
    return adjusted,{"naive":naive,"adjusted":adjusted,"pairs_used":used,"avg_rho":sum(rhos)/len(rhos) if rhos else 0.0,"coverage":used/max(1,total_pairs)}
