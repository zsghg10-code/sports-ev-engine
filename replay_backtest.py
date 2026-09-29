"""No-lookahead replay/backtest from immutable pregame snapshots.

This replays what the engine actually knew/stored before kickoff. It does not
pretend to reconstruct provider data that was never archived.
"""
from __future__ import annotations
import math
from datetime import date
from zoneinfo import ZoneInfo
import pandas as pd
from .prediction_store import load_predictions, load_settled
from .daily_combo import prepare_daily_candidates, best_combos

KST=ZoneInfo("Asia/Seoul")


def _ident(r):
    try:p=round(float(r.get("point")),4)
    except (TypeError,ValueError):p=None
    return (str(r.get("event_id") or ""),str(r.get("sport_key") or ""),str(r.get("market") or ""),str(r.get("selection") or ""),p)


def replay_snapshots(selected_date:date, minutes_before:int=0, model_version:str|None=None):
    rows=[]
    for r in load_predictions():
        if r.get("paper_eligible") is False: continue
        ko=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        rec=pd.to_datetime(r.get("recorded_at"),utc=True,errors="coerce")
        if pd.isna(ko) or pd.isna(rec) or ko.tz_convert(KST).date()!=selected_date: continue
        if model_version and str(r.get("model_version"))!=str(model_version): continue
        cutoff=ko-pd.Timedelta(minutes=int(minutes_before))
        if rec>cutoff: continue
        x=dict(r);x["kickoff_kst"]=ko.tz_convert(KST);rows.append(x)
    latest={}
    for r in sorted(rows,key=lambda x:str(x.get("recorded_at") or "")):latest[_ident(r)]=r
    return pd.DataFrame(list(latest.values())) if latest else pd.DataFrame()


def _settled_map():
    return {r.get("fingerprint"):r for r in load_settled() if r.get("fingerprint")}


def replay_day(selected_date:date,minutes_before:int=0,model_version:str|None=None):
    snaps=replay_snapshots(selected_date,minutes_before,model_version)
    cand=prepare_daily_candidates(snaps)
    sm=_settled_map(); settled=[]
    for _,r in cand.iterrows():
        s=sm.get(r.get("fingerprint"))
        if s: settled.append((r.to_dict(),s))
    single_roi=sum(float(s.get("realized_roi") or 0) for _,s in settled)/len(settled) if settled else float("nan")
    combos=best_combos(cand,sizes=(2,),top_n=1) if not cand.empty else {2:[]}
    combo=(combos.get(2) or [None])[0]; combo_roi=None
    if combo:
        ret=1.0; complete=True
        for leg in combo["legs"]:
            s=sm.get(leg.get("fingerprint"))
            if not s: complete=False;break
            w=float(s.get("settle_win") or 0);p=float(s.get("settle_push") or 0);od=float(s.get("best_odds") or leg.get("best_odds") or 0)
            ret*=w*od+p
        if complete: combo_roi=ret-1
    return {"snapshots":snaps,"candidates":cand,"settled_n":len(settled),"single_roi":single_roi,"best_two":combo,"best_two_realized_roi":combo_roi}


def version_backtest(start_date:date,end_date:date):
    settled=load_settled(); latest={}
    for r in settled:
        if r.get("paper_eligible") is False: continue
        ts=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        if pd.isna(ts):continue
        d=ts.tz_convert(KST).date()
        if d<start_date or d>end_date:continue
        if not bool(r.get("v3_candidate")):continue
        ver=str(r.get("model_version") or "unknown")
        try: point=round(float(r.get("point")),4)
        except (TypeError,ValueError): point=None
        ident=(ver,r.get("event_id"),r.get("market"),r.get("selection"),point)
        if ident not in latest or str(r.get("recorded_at") or "")>str(latest[ident].get("recorded_at") or ""):
            latest[ident]=r
    groups={}
    for r in latest.values():
        ver=str(r.get("model_version") or "unknown")
        groups.setdefault(ver,[]).append(r)
    out=[]
    for ver,arr in groups.items():
        roi=[float(x.get("realized_roi") or 0) for x in arr]
        b=[]
        for x in arr:
            if float(x.get("settle_push") or 0)>0:continue
            try:q=float(x.get("model_win_prob"))/max(1e-9,1-float(x.get("push_prob") or 0));y=float(x.get("settle_win"))
            except (TypeError,ValueError):continue
            if y in (0,1):b.append((q-y)**2)
        out.append({"model_version":ver,"n":len(arr),"roi":sum(roi)/len(roi) if roi else float("nan"),"brier":sum(b)/len(b) if b else float("nan")})
    return sorted(out,key=lambda x:x["model_version"])
