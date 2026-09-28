"""Append-only prediction snapshots, automatic score settlement and evaluation."""
from __future__ import annotations

import hashlib, json, math, os
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

from .core.asian import settle_total_under, settle_total_over, settle_home_handicap

MODEL_VERSION="3.0.0"
DEFAULT_PREDICTIONS="data/prediction_snapshots.jsonl"
DEFAULT_SETTLED="data/settled_predictions.jsonl"


def _safe(v):
    if isinstance(v,(list,dict,tuple)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError,ValueError):
        pass
    if isinstance(v,pd.Timestamp):
        return v.isoformat()
    if hasattr(v,"item"):
        try:return v.item()
        except Exception:pass
    if isinstance(v,(str,int,float,bool)) or v is None:return v
    return str(v)


def _append_jsonl(path,rows):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("a",encoding="utf-8") as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n")


def _load(path):
    p=Path(path)
    if not p.exists():return []
    out=[]
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:out.append(json.loads(line))
            except Exception:pass
    return out


def record_frame(frame:pd.DataFrame, sport_key:str|None=None, sport_family:str="", path=DEFAULT_PREDICTIONS):
    if frame is None or frame.empty:return 0
    existing=_load(path)
    fingerprints={x.get("fingerprint") for x in existing}
    now=datetime.now(timezone.utc).isoformat()
    rows=[]
    keep=["event_id","commence_time","home_team","away_team","market","selection","point","best_book","best_odds","books",
          "consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even","edge_pp","ev_roi","point_ev_roi",
          "conservative_ev_roi","uncertainty_pp","grade","sanity","stage","data_quality","sport_key","home_lambda","away_lambda",
          "home_expected_runs","away_expected_runs","reasoning_engine_id","signal_coverage","missing_signals","counter_case_risk",
          "counter_case_summary","v3_decision_status","robust_positive_ratio","robust_ev_min","robust_ev_p10","robust_ev_max",
          "robust_prob_min","robust_prob_max","robust_scenario_count","v3_candidate","v3_parlay_eligible"]
    for _,r in frame.iterrows():
        d={k:_safe(r.get(k)) for k in keep if k in frame.columns}
        d["sport_key"]=d.get("sport_key") or sport_key or ""
        d["sport_family"]=sport_family
        d["model_version"]=MODEL_VERSION
        d["recorded_at"]=now
        raw="|".join(str(d.get(k,"")) for k in ("event_id","sport_key","market","selection","point","best_odds","model_win_prob","v3_decision_status"))
        # Same price/model state is stored once; a later changed price/probability creates a new immutable snapshot.
        fp=hashlib.sha256(raw.encode("utf-8")).hexdigest()
        d["fingerprint"]=fp
        if fp in fingerprints:continue
        fingerprints.add(fp);rows.append(d)
    _append_jsonl(path,rows)
    return len(rows)


def _score_map(score_event):
    vals={}
    for x in score_event.get("scores") or []:
        try:vals[str(x.get("name"))]=float(x.get("score"))
        except (TypeError,ValueError):pass
    return vals


def _settlement_for(snapshot,home_score,away_score):
    market=snapshot.get("market");sel=str(snapshot.get("selection",""));point=snapshot.get("point")
    home=str(snapshot.get("home_team",""));away=str(snapshot.get("away_team",""))
    if market=="h2h":
        if sel==home:return (1,0,0) if home_score>away_score else (0,0,1)
        if sel==away:return (1,0,0) if away_score>home_score else (0,0,1)
        if "draw" in sel.lower():return (1,0,0) if home_score==away_score else (0,0,1)
    if point is None:return None
    try:point=float(point)
    except (TypeError,ValueError):return None
    if market=="totals":
        return settle_total_over(home_score+away_score,point) if "over" in sel.lower() else settle_total_under(home_score+away_score,point)
    if market=="spreads":
        if sel==home:return settle_home_handicap(home_score,away_score,point)
        if sel==away:return settle_home_handicap(away_score,home_score,point)
    return None


def settle_from_scores(sport_key,scores,prediction_path=DEFAULT_PREDICTIONS,settled_path=DEFAULT_SETTLED):
    predictions=[x for x in _load(prediction_path) if x.get("sport_key")==sport_key]
    settled=_load(settled_path); done={x.get("fingerprint") for x in settled}
    score_by_id={str(x.get("id")):x for x in scores or [] if x.get("completed") is True}
    out=[]
    for s in predictions:
        if s.get("fingerprint") in done:continue
        event=score_by_id.get(str(s.get("event_id")))
        if not event:continue
        sm=_score_map(event);home=s.get("home_team");away=s.get("away_team")
        if home not in sm or away not in sm:continue
        result=_settlement_for(s,sm[home],sm[away])
        if result is None:continue
        w,p,l=result;odds=float(s.get("best_odds") or 0)
        realized=w*(odds-1)-l
        row={**s,"settled_at":datetime.now(timezone.utc).isoformat(),"home_score":sm[home],"away_score":sm[away],
             "settle_win":w,"settle_push":p,"settle_loss":l,"realized_roi":realized}
        out.append(row);done.add(s.get("fingerprint"))
    _append_jsonl(settled_path,out)
    return len(out)


def auto_settle(api,sport_key,prediction_path=DEFAULT_PREDICTIONS,settled_path=DEFAULT_SETTLED):
    try:scores,_=api.scores(sport_key,days_from=3)
    except Exception as e:return {"settled":0,"error":str(e)}
    return {"settled":settle_from_scores(sport_key,scores,prediction_path,settled_path),"error":""}


def pending_sport_keys(prediction_path=DEFAULT_PREDICTIONS,settled_path=DEFAULT_SETTLED):
    predictions=_load(prediction_path);done={x.get("fingerprint") for x in _load(settled_path)}
    return sorted({x.get("sport_key") for x in predictions if x.get("fingerprint") not in done and x.get("sport_key")})

def evaluation(path=DEFAULT_SETTLED):
    rows=_load(path)
    if not rows:return {"n":0,"roi_n":0,"groups":[]}

    # Realized ROI uses every settled snapshot, including full/half/quarter pushes.
    roi_rows=[]
    # Calibration metrics use only binary-resolved observations. Asian partial pushes are
    # excluded from Brier/log-loss because they are not Bernoulli outcomes.
    calibration_rows=[]
    for r in rows:
        try:
            roi=float(r.get("realized_roi"))
        except (TypeError,ValueError):
            continue
        rr={**r,"roi":roi}
        roi_rows.append(rr)
        try:
            p=float(r.get("model_win_prob"));push=float(r.get("push_prob") or 0);y=float(r.get("settle_win"))
            settle_push=float(r.get("settle_push") or 0)
            settle_loss=float(r.get("settle_loss") or 0)
        except (TypeError,ValueError):
            continue
        # A clean binary result is exactly win or loss with no partial push.
        if settle_push>1e-12 or y not in (0.0,1.0) or settle_loss not in (0.0,1.0):
            continue
        resolved=max(1e-9,1-push)
        q=max(1e-9,min(1-1e-9,p/resolved))
        calibration_rows.append({**rr,"q":q,"y":y})

    def roi_metric(sub):
        return sum(x["roi"] for x in sub)/len(sub) if sub else float("nan")

    def calib_metric(sub):
        if not sub:return {"n":0,"brier":float("nan"),"log_loss":float("nan"),"hit_rate":float("nan")}
        n=len(sub)
        b=sum((x["q"]-x["y"])**2 for x in sub)/n
        ll=-sum(x["y"]*math.log(x["q"])+(1-x["y"])*math.log(1-x["q"]) for x in sub)/n
        hit=sum(x["y"] for x in sub)/n
        return {"n":n,"brier":b,"log_loss":ll,"hit_rate":hit}

    group_keys={(x.get("sport_family",""),x.get("market",""),x.get("v3_decision_status","")) for x in roi_rows}
    groups=[]
    for key in sorted(group_keys):
        rs=[x for x in roi_rows if (x.get("sport_family",""),x.get("market",""),x.get("v3_decision_status",""))==key]
        cs=[x for x in calibration_rows if (x.get("sport_family",""),x.get("market",""),x.get("v3_decision_status",""))==key]
        groups.append({"sport_family":key[0],"market":key[1],"decision":key[2],
                       **calib_metric(cs),"roi_n":len(rs),"roi":roi_metric(rs)})
    overall={**calib_metric(calibration_rows),"roi_n":len(roi_rows),"roi":roi_metric(roi_rows)}
    return {"n":len(calibration_rows),"roi_n":len(roi_rows),"overall":overall,"groups":groups}

