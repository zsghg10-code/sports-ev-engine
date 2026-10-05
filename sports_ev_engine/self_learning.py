"""Automatic learning status for Sports EV Engine v3.6.

The probability learning itself already exists in adaptive_model.py:
settled immutable pregame predictions feed a PAVA/isotonic calibration curve,
and later analyses automatically use that curve once enough samples exist.

v3.6's job is to make the data loop automatic and auditable. It does NOT let
postgame outcomes rewrite model code or feature weights on their own.
"""
from __future__ import annotations
import math
import pandas as pd

from . import adaptive_model
from .prediction_store import load_settled

_INSTALLED = False


def _num(v, default=float("nan")):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError,ValueError):
        return default


def _resolved_prob(win, push=0.0):
    w=_num(win); p=max(0.0,_num(push,0.0))
    if not math.isfinite(w): return float("nan")
    return max(1e-6,min(1-1e-6,w/max(1e-9,1-p)))


def _pregame(r):
    if r.get("paper_eligible") is False:return False
    rec=pd.to_datetime(r.get("recorded_at"),utc=True,errors="coerce")
    kick=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
    return not (not pd.isna(rec) and not pd.isna(kick) and rec>=kick)


def _latest_rows(rows,family,market):
    latest={}
    for r in rows or []:
        if str(r.get("sport_family") or "")!=str(family):continue
        if str(r.get("market") or "")!=str(market):continue
        if not _pregame(r):continue
        if _num(r.get("settle_push"),0.0)>1e-12:continue
        y=_num(r.get("settle_win"))
        if y not in (0.0,1.0):continue
        try:point=round(float(r.get("point")),4)
        except (TypeError,ValueError):point=None
        ident=(str(r.get("event_id") or ""),str(r.get("selection") or ""),point)
        if ident not in latest or str(r.get("recorded_at") or "")>str(latest[ident].get("recorded_at") or ""):
            latest[ident]=r
    return list(latest.values())


def _performance(rows,family,market):
    vals=[]
    for r in _latest_rows(rows,family,market):
        q=_resolved_prob(r.get("model_win_prob"),r.get("push_prob"))
        m=_num(r.get("consensus_prob"));y=_num(r.get("settle_win"))
        if not (math.isfinite(q) and math.isfinite(m) and y in (0.0,1.0)):continue
        clv=_num(r.get("clv_market_prob_pp"))
        vals.append((q,m,y,clv))
    if not vals:
        return {"n":0,"model_brier":float("nan"),"market_brier":float("nan"),"bias_pp":float("nan"),"avg_clv_pp":float("nan")}
    n=len(vals)
    mb=sum((q-y)**2 for q,m,y,c in vals)/n
    xb=sum((m-y)**2 for q,m,y,c in vals)/n
    bias=sum(y-q for q,m,y,c in vals)/n*100
    clv=[c for q,m,y,c in vals if math.isfinite(c)]
    return {"n":n,"model_brier":mb,"market_brier":xb,"bias_pp":bias,"avg_clv_pp":sum(clv)/len(clv) if clv else float("nan")}


def learning_summary(settled_rows=None):
    rows=settled_rows if settled_rows is not None else load_settled()
    families=sorted({str(r.get("sport_family") or "") for r in rows if r.get("sport_family")})
    out=[]
    for fam in families:
        markets=sorted({str(r.get("market") or "") for r in rows if str(r.get("sport_family") or "")==fam and r.get("market")})
        for market in markets:
            curve=adaptive_model.calibration_curve(rows,fam,market)
            perf=_performance(rows,fam,market)
            out.append({
                "종목":fam,
                "마켓":market,
                "학습표본":int(curve.get("n") or 0),
                "활성":bool(curve.get("active")),
                "신뢰도":round(float(curve.get("reliability") or 0)*100,1),
                "Calibration bias(%p)":None if not math.isfinite(perf["bias_pp"]) else round(perf["bias_pp"],2),
                "Model Brier":None if not math.isfinite(perf["model_brier"]) else round(perf["model_brier"],4),
                "Market Brier":None if not math.isfinite(perf["market_brier"]) else round(perf["market_brier"],4),
                "평균 CLV(%p)":None if not math.isfinite(perf["avg_clv_pp"]) else round(perf["avg_clv_pp"],2),
                "상태":str(curve.get("reason") or ""),
            })
    return out


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True
    adaptive_model.SELF_LEARNING_AUTOMATION_BUILD="3.6.0-autolearn"
