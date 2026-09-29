"""Explain why a pick probability moved between immutable snapshots."""
from __future__ import annotations
import math
import pandas as pd
from .prediction_store import load_predictions


def _num(v,default=float("nan")):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except (TypeError,ValueError): return default


def _ident(r):
    try:p=round(float(r.get("point")),4)
    except (TypeError,ValueError):p=None
    return (str(r.get("event_id") or ""),str(r.get("sport_key") or ""),str(r.get("market") or ""),str(r.get("selection") or ""),p)


def change_logs(rows=None,limit=100):
    rows=list(rows if rows is not None else load_predictions())
    groups={}
    for r in rows: groups.setdefault(_ident(r),[]).append(r)
    out=[]
    for ident,arr in groups.items():
        arr=sorted(arr,key=lambda x:str(x.get("recorded_at") or ""))
        if len(arr)<2: continue
        old,new=arr[-2],arr[-1]
        op=_num(old.get("model_win_prob")); np=_num(new.get("model_win_prob"))
        if not (math.isfinite(op) and math.isfinite(np)): continue
        total=(np-op)*100
        if abs(total)<.05 and old.get("stage")==new.get("stage") and old.get("missing_signals")==new.get("missing_signals"): continue
        reasons=[]
        # Component deltas are an approximate audit decomposition using the
        # ensemble weights stored at each snapshot. They are not causal Shapley values.
        def comp(name,label):
            oa=_num(old.get(f"ensemble_{name}_prob")); na=_num(new.get(f"ensemble_{name}_prob"))
            ow=_num(old.get(f"ensemble_{name}_weight"),0.0); nw=_num(new.get(f"ensemble_{name}_weight"),0.0)
            if math.isfinite(oa) and math.isfinite(na):
                d=(na-oa)*100*((ow+nw)/2 if math.isfinite(ow) and math.isfinite(nw) else 1.0)
                if abs(d)>=.1: reasons.append(f"{label} {d:+.1f}%p")
        comp("independent","독립/정밀모델")
        comp("recent_form","최근폼")
        om=_num(old.get("consensus_prob")); nm=_num(new.get("consensus_prob"))
        if math.isfinite(om) and math.isfinite(nm) and abs(nm-om)>=.001:
            ow=_num(old.get("ensemble_market_weight"),1.0); nw=_num(new.get("ensemble_market_weight"),1.0)
            d=(nm-om)*100*((ow+nw)/2 if math.isfinite(ow) and math.isfinite(nw) else 1.0)
            reasons.append(f"시장 {'정방향' if (nm-om)*total>=0 else '역방향'} {d:+.1f}%p")
        if old.get("stage")!=new.get("stage"):
            reasons.append(f"데이터 단계 {old.get('stage') or '-'}→{new.get('stage') or '-'}")
        if bool(old.get("lineup_confirmed"))!=bool(new.get("lineup_confirmed")):
            reasons.append("라인업 확정" if new.get("lineup_confirmed") else "라인업 상태 변경")
        if old.get("home_starter")!=new.get("home_starter") or old.get("away_starter")!=new.get("away_starter"):
            reasons.append("예고/확정 선발 변경")
        oc=_num(old.get("signal_coverage")); nc=_num(new.get("signal_coverage"))
        if math.isfinite(oc) and math.isfinite(nc) and abs(nc-oc)>=.05:
            reasons.append(f"정밀신호 커버리지 {(nc-oc)*100:+.0f}%p")
        oca=_num(old.get("calibration_delta_pp"),0.0); nca=_num(new.get("calibration_delta_pp"),0.0)
        if abs(nca-oca)>=.1: reasons.append(f"자동 calibration {(nca-oca):+.1f}%p")
        dis=_num(new.get("ensemble_disagreement_pp"))
        if math.isfinite(dis) and dis>=9: reasons.append(f"모델간 충돌 {dis:.1f}%p")
        if not reasons: reasons=["독립모델/최근폼/정밀 컨텍스트 재계산"]
        out.append({
            "recorded_at":new.get("recorded_at"),"commence_time":new.get("commence_time"),
            "sport_family":new.get("sport_family"),"event_id":new.get("event_id"),"home_team":new.get("home_team"),"away_team":new.get("away_team"),
            "market":new.get("market"),"selection":new.get("selection"),"point":new.get("point"),
            "old_prob":op,"new_prob":np,"delta_pp":total,"reason":" / ".join(reasons),
            "old_stage":old.get("stage"),"new_stage":new.get("stage"),
        })
    out.sort(key=lambda x:str(x.get("recorded_at") or ""),reverse=True)
    return out[:limit]
