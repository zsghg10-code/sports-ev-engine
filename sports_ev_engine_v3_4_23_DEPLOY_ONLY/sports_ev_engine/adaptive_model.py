"""Adaptive calibration + multi-model ensemble layer.

v3.4.22 keeps the structural/independent model auditable and adds a dedicated
MLB totals calibration policy.  Calibration learns only from resolved,
pregame, de-duplicated append-only settlements; it never replaces the raw
independent probability with the market probability.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict

import pandas as pd

from .core.ev import analyze_bet

# Generic calibration remains for backwards compatibility. MLB totals uses a
# stricter, explicitly named policy because totals calibration was the audited
# failure mode in v3.4.21.
GENERIC_CALIBRATION_MIN_N = 40
MLB_TOTALS_CALIBRATION_MIN_N = 60
MLB_TOTALS_CALIBRATION_MAX_MOVE_PP = 4.0
MLB_TOTALS_DIVERGENCE_REVIEW_PP = 10.0
MLB_TOTALS_DIVERGENCE_EVIDENCE_N = 80
MLB_TOTALS_DIVERGENCE_EVIDENCE_RELIABILITY = 0.15

_SETTLED_CACHE={"at":0.0,"rows":[]}

def _settled_cached(ttl=45.0):
    now=time.monotonic()
    if now-_SETTLED_CACHE["at"]<=ttl and _SETTLED_CACHE["rows"]:
        return _SETTLED_CACHE["rows"]
    try:
        from .prediction_store import load_settled
        rows=load_settled()
    except Exception:
        rows=[]
    _SETTLED_CACHE["at"]=now;_SETTLED_CACHE["rows"]=rows
    return rows


def _num(v, default=float("nan")):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError,ValueError):
        return default


def _bool(v):
    if isinstance(v,str):
        return v.strip().lower() in {"1","true","yes","y"}
    try:
        if pd.isna(v): return False
    except Exception: pass
    return bool(v) if v is not None else False


def _resolved_prob(win, push=0.0):
    w=_num(win); p=max(0.0,_num(push,0.0))
    if not math.isfinite(w): return float("nan")
    return max(1e-6,min(1-1e-6,w/max(1e-9,1-p)))


def _pava(points):
    """Weighted isotonic regression for (x, y, weight) sorted by x."""
    blocks=[]
    for x,y,w in points:
        blocks.append([float(x),float(x),float(y),float(w)])
        while len(blocks)>=2 and blocks[-2][2] > blocks[-1][2]:
            b=blocks.pop(); a=blocks.pop()
            wt=a[3]+b[3]
            yy=(a[2]*a[3]+b[2]*b[3])/max(wt,1e-9)
            blocks.append([a[0],b[1],yy,wt])
    return [((lo+hi)/2,y,w) for lo,hi,y,w in blocks]


def _calibration_source_probability(r:dict, sport_family:str, market:str):
    """Return the historical probability that calibration is intended to map.

    New MLB totals snapshots persist the actual pre-calibration ensemble
    probability. Older snapshots fall back to the best pre-adaptive field so
    history remains usable without pretending old rows contain new features.
    """
    fields=("pre_calibration_prob_cond","ensemble_prob_cond") if (
        str(sport_family)=="baseball_mlb" and str(market)=="totals"
    ) else ()
    for field in (*fields,"pre_adaptive_model_win_prob","model_win_prob"):
        if field not in r: continue
        if field.endswith("_cond"):
            q=_num(r.get(field))
        else:
            q=_resolved_prob(r.get(field),r.get("push_prob"))
        if math.isfinite(q): return q
    return float("nan")


def calibration_curve(settled_rows:list[dict], sport_family:str, market:str, *, min_n:int|None=None):
    if min_n is None:
        min_n = MLB_TOTALS_CALIBRATION_MIN_N if (
            str(sport_family)=="baseball_mlb" and str(market)=="totals"
        ) else GENERIC_CALIBRATION_MIN_N
    # One resolved observation per actual pick/event/line. Repeated smart-refresh
    # snapshots must not make one game count 5-10 times in calibration.
    latest={}
    for r in settled_rows or []:
        # Strict paper-trade guard: post-kickoff snapshots never train calibration.
        if r.get("paper_eligible") is False: continue
        rec=pd.to_datetime(r.get("recorded_at"),utc=True,errors="coerce")
        kick=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        if r.get("paper_eligible") is None and not pd.isna(rec) and not pd.isna(kick) and rec>=kick: continue
        # If timestamps prove it was post-kickoff, exclude even a malformed legacy
        # paper_eligible flag. This is intentionally stricter than trusting metadata.
        if not pd.isna(rec) and not pd.isna(kick) and rec>=kick: continue
        if str(r.get("sport_family") or "") != str(sport_family or ""): continue
        if str(r.get("market") or "") != str(market or ""): continue
        try: point=round(float(r.get("point")),4)
        except (TypeError,ValueError): point=None
        # Totals Over/Under on the same line are complementary views of one
        # game outcome, not two independent calibration observations.
        ident=(str(r.get("event_id") or ""), ("TOTAL_LINE" if str(market)=="totals" else str(r.get("selection") or "")), point)
        if ident not in latest or str(r.get("recorded_at") or "")>str(latest[ident].get("recorded_at") or ""):
            latest[ident]=r
    vals=[]
    for r in latest.values():
        if _num(r.get("settle_push"),0.0) > 1e-12: continue
        y=_num(r.get("settle_win")); q=_calibration_source_probability(r,sport_family,market)
        if y not in (0.0,1.0) or not math.isfinite(q): continue
        vals.append((q,y))
    n=len(vals)
    if n < int(min_n):
        return {"active":False,"n":n,"points":[],"reliability":0.0,"reason":f"표본 {n} < {min_n}"}

    bins=defaultdict(list)
    for q,y in vals:
        bins[min(9,max(0,int(q*10)))].append((q,y))
    pts=[]
    # Strong shrinkage: empirical calibration is evidence, not a license to chase
    # recent outcomes. MLB totals uses the same prior but a larger activation N.
    prior_strength=18.0
    for idx in sorted(bins):
        arr=bins[idx]
        if len(arr)<4: continue
        mean_q=sum(x[0] for x in arr)/len(arr)
        wins=sum(x[1] for x in arr)
        empirical=(wins + prior_strength*mean_q)/(len(arr)+prior_strength)
        pts.append((mean_q,empirical,float(len(arr))))
    if len(pts)<3:
        return {"active":False,"n":n,"points":[],"reliability":0.0,"reason":"usable calibration bins < 3"}
    iso=_pava(sorted(pts))
    # Starts mild and only becomes material after hundreds of resolved samples.
    if str(sport_family)=="baseball_mlb" and str(market)=="totals":
        reliability=max(0.10,min(0.70,0.10+0.60*(n-int(min_n))/360.0))
    else:
        reliability=max(0.12,min(0.75,0.12+0.63*(n-int(min_n))/320.0))
    return {"active":True,"n":n,"points":iso,"reliability":reliability,"reason":"ok"}


def _interp(points, q):
    if not points: return q
    p=sorted(points)
    if q<=p[0][0]: return p[0][1]
    if q>=p[-1][0]: return p[-1][1]
    for (x1,y1,_),(x2,y2,_) in zip(p,p[1:]):
        if x1<=q<=x2:
            t=(q-x1)/max(1e-9,x2-x1)
            return y1+t*(y2-y1)
    return q


def calibrate_probability(q, curve, *, max_move_pp=4.0):
    q=max(1e-6,min(1-1e-6,float(q)))
    if not curve or not curve.get("active"):
        return q,0.0
    target=max(1e-6,min(1-1e-6,_interp(curve.get("points") or [],q)))
    rel=float(curve.get("reliability") or 0.0)
    out=q+rel*(target-q)
    cap=float(max_move_pp)/100.0
    out=max(q-cap,min(q+cap,out))
    return max(1e-6,min(1-1e-6,out)),(out-q)*100


def _recent_form_proxy(r:pd.Series, family:str):
    market=str(r.get("market") or "")
    sel=str(r.get("selection") or "").lower()
    home=str(r.get("home_team") or "").lower(); away=str(r.get("away_team") or "").lower()
    point=_num(r.get("point"))
    if family.startswith("baseball"):
        hrf=_num(r.get("home_recent_rf")); hra=_num(r.get("home_recent_ra")); arf=_num(r.get("away_recent_rf")); ara=_num(r.get("away_recent_ra"))
        if not all(math.isfinite(x) for x in (hrf,hra,arf,ara)): return None
        he=(hrf+ara)/2; ae=(arf+hra)/2; diff=he-ae; total=he+ae
        scale=1.65
    elif family.startswith("soccer"):
        hrf=_num(r.get("home_recent_gf")); hra=_num(r.get("home_recent_ga")); arf=_num(r.get("away_recent_gf")); ara=_num(r.get("away_recent_ga"))
        if not all(math.isfinite(x) for x in (hrf,hra,arf,ara)): return None
        he=(hrf+ara)/2; ae=(arf+hra)/2; diff=he-ae; total=he+ae
        scale=.85
    else:
        return None
    if market=="h2h":
        if "draw" in sel or sel in {"x","tie"}: return None
        hp=1/(1+math.exp(-diff/max(scale,1e-6)))
        return hp if sel==home else 1-hp if sel==away else None
    if market=="totals" and math.isfinite(point):
        over=1/(1+math.exp(-(total-point)/(0.95 if family.startswith("baseball") else .55)))
        return over if "over" in sel else 1-over if "under" in sel else None
    if market=="spreads" and math.isfinite(point):
        selected_diff=diff if sel==home else -diff if sel==away else None
        if selected_diff is None:return None
        return 1/(1+math.exp(-((selected_diff+point)/max(scale,1e-6))))
    return None


def ensemble_components(r:pd.Series, family:str):
    push=max(0.0,_num(r.get("push_prob"),0.0))
    independent=_resolved_prob(r.get("raw_independent_prob"),r.get("raw_push_prob",push))
    legacy=_resolved_prob(r.get("model_win_prob"),push)
    market=_num(r.get("consensus_prob"),_num(r.get("market_prob")))
    form=_recent_form_proxy(r,family)
    comps=[]
    market_name=str(r.get("market") or "")
    # MLB totals: recent scoring is already represented in the structural run
    # model (recent10 + advanced recent-form factors). Adding the same games again
    # as an ensemble vote was a real double-weight path in v3.4.21. Keep the
    # signal auditable, but suppress that duplicate vote for MLB totals.
    if family=="baseball_mlb" and market_name=="totals":
        if math.isfinite(independent): comps.append(("independent",independent,0.68))
        if math.isfinite(market): comps.append(("market",market,0.32))
    else:
        if math.isfinite(independent): comps.append(("independent",independent,0.58))
        if math.isfinite(market): comps.append(("market",market,0.27))
        if form is not None and math.isfinite(form): comps.append(("recent_form",float(form),0.15))
    if not comps and math.isfinite(legacy): comps=[("context_model",legacy,1.0)]
    sw=sum(w for _,_,w in comps) or 1.0
    q=sum(p*w for _,p,w in comps)/sw
    return q,comps


def _is_mlb_totals(sport_family,market):
    return str(sport_family)=="baseball_mlb" and str(market)=="totals"


def _market_divergence_evidence(curve):
    return bool(curve.get("active")) and int(curve.get("n") or 0)>=MLB_TOTALS_DIVERGENCE_EVIDENCE_N and float(curve.get("reliability") or 0)>=MLB_TOTALS_DIVERGENCE_EVIDENCE_RELIABILITY


def apply_adaptive_layer(frame:pd.DataFrame, sport_family:str, settled_rows:list[dict]|None=None):
    if frame is None or frame.empty: return frame
    if settled_rows is None: settled_rows=_settled_cached()
    curves={}; out=[]
    for _,r0 in frame.iterrows():
        r=r0.copy(); market=str(r.get("market") or "")
        push=max(0.0,_num(r.get("push_prob"),0.0)); resolved=max(1e-9,1-push)
        old_win=_num(r.get("model_win_prob")); old_q=_resolved_prob(old_win,push)
        if not math.isfinite(old_q): out.append(r.to_dict()); continue
        ens_q,comps=ensemble_components(r,sport_family)
        ens_q=max(old_q-.035,min(old_q+.035,ens_q))
        key=(sport_family,market)
        if key not in curves: curves[key]=calibration_curve(settled_rows,sport_family,market)
        curve=curves[key]
        max_move=MLB_TOTALS_CALIBRATION_MAX_MOVE_PP if _is_mlb_totals(sport_family,market) else 4.0
        cal_q,cal_delta=calibrate_probability(ens_q,curve,max_move_pp=max_move)
        final_q=max(old_q-.05,min(old_q+.05,cal_q))
        final_win=final_q*resolved
        unc=_num(r.get("uncertainty_pp"),0.0)
        ev=analyze_bet(_num(r.get("best_odds")),final_win,push,unc)
        probs=[p for _,p,_ in comps]
        disagree=(max(probs)-min(probs))*100 if len(probs)>=2 else 0.0
        gate="REVIEW" if disagree>=14 else "CHECK" if disagree>=9 else "OK"
        raw_q=_resolved_prob(r.get("raw_independent_prob"),r.get("raw_push_prob",push))
        market_q=_num(r.get("consensus_prob"),_num(r.get("market_prob")))
        raw_market_gap=(raw_q-market_q)*100 if math.isfinite(raw_q) and math.isfinite(market_q) else float("nan")
        evidence=_market_divergence_evidence(curve)
        divergence_gate=_is_mlb_totals(sport_family,market) and math.isfinite(raw_market_gap) and abs(raw_market_gap)>=MLB_TOTALS_DIVERGENCE_REVIEW_PP and not evidence

        r["pre_adaptive_model_win_prob"]=old_win
        r["pre_calibration_prob_cond"]=ens_q
        r["pre_calibration_win_prob"]=ens_q*resolved
        r["ensemble_prob_cond"]=ens_q
        r["calibrated_prob_cond"]=final_q
        r["adaptive_delta_pp"]=(final_q-old_q)*100
        r["calibration_delta_pp"]=cal_delta
        r["calibration_n"]=int(curve.get("n") or 0)
        r["calibration_active"]=bool(curve.get("active"))
        r["calibration_reliability"]=float(curve.get("reliability") or 0.0)
        r["calibration_reason"]=str(curve.get("reason") or "")
        r["ensemble_disagreement_pp"]=disagree
        r["ensemble_gate"]=gate
        r["ensemble_summary"]=" | ".join(f"{n}:{p*100:.1f}%" for n,p,_ in comps)
        r["recent_form_ensemble_suppressed"]=bool(_is_mlb_totals(sport_family,market))
        r["raw_market_gap_final_audit_pp"]=raw_market_gap
        r["mlb_totals_divergence_threshold_pp"]=MLB_TOTALS_DIVERGENCE_REVIEW_PP if _is_mlb_totals(sport_family,market) else None
        r["mlb_totals_calibration_evidence_sufficient"]=evidence if _is_mlb_totals(sport_family,market) else None
        r["mlb_totals_market_disagreement_gate"]=bool(divergence_gate)
        for n,p,w in comps:
            r[f"ensemble_{n}_prob"]=p; r[f"ensemble_{n}_weight"]=w/sum(x[2] for x in comps)
        r["model_win_prob"]=final_win; r["model_lose_prob"]=max(0.0,1-final_win-push)
        r["break_even"]=ev.break_even; r["edge_pp"]=ev.edge_pp; r["ev_roi"]=ev.ev_roi; r["point_ev_roi"]=ev.ev_roi
        r["conservative_ev_roi"]=ev.conservative_ev_roi; r["kelly_scaled"]=ev.kelly_scaled
        # Final v3 status is intentionally NOT decided here. v3.4.22 recomputes
        # robustness after calibration + coherence normalization in the caller.
        r["adaptive_gate"]="MLB_TOTALS_MARKET_GAP_REVIEW" if divergence_gate else ("MODEL_CONFLICT_REVIEW" if gate=="REVIEW" else "OK")
        out.append(r.to_dict())
    result=pd.DataFrame(out,index=frame.index)

    # Preserve probability coherence for mutually exclusive market sides.
    if not result.empty and "event_id" in result.columns and "market" in result.columns:
        def _coherence_key(row):
            m=str(row.get("market") or ""); pt=_num(row.get("point"))
            if m=="h2h": line=None
            elif m=="spreads" and math.isfinite(pt): line=abs(pt)
            else: line=pt if math.isfinite(pt) else None
            return (str(row.get("event_id") or ""),m,line)
        groups={}
        for i,row in result.iterrows(): groups.setdefault(_coherence_key(row),[]).append(i)
        for _,idx in groups.items():
            if len(idx)<2: continue
            cond=[]
            for i in idx:
                psh=max(0.0,_num(result.at[i,"push_prob"],0.0)); q=_resolved_prob(result.at[i,"model_win_prob"],psh)
                if math.isfinite(q): cond.append((i,q,psh))
            if len(cond)<2: continue
            total=sum(q for _,q,_ in cond)
            if total<=0: continue
            for i,q,psh in cond:
                nq=q/total; nw=nq*(1-psh)
                old_q=_resolved_prob(result.at[i,"pre_adaptive_model_win_prob"],psh)
                result.at[i,"calibrated_prob_cond"]=nq
                result.at[i,"model_win_prob"]=nw
                result.at[i,"model_lose_prob"]=max(0.0,1-nw-psh)
                result.at[i,"adaptive_delta_pp"]=(nq-old_q)*100 if math.isfinite(old_q) else result.at[i,"adaptive_delta_pp"]
                result.at[i,"coherence_normalized"]=True
                ev=analyze_bet(_num(result.at[i,"best_odds"]),nw,psh,_num(result.at[i,"uncertainty_pp"],0.0))
                result.at[i,"break_even"]=ev.break_even; result.at[i,"edge_pp"]=ev.edge_pp
                result.at[i,"ev_roi"]=ev.ev_roi; result.at[i,"point_ev_roi"]=ev.ev_roi
                result.at[i,"conservative_ev_roi"]=ev.conservative_ev_roi; result.at[i,"kelly_scaled"]=ev.kelly_scaled
    return result
