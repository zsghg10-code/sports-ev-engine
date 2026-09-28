"""User-facing FINAL Decision Layer helpers for Sports EV Engine v3.

Pure-data helpers only: Streamlit rendering stays in app.py.  The goal is to
turn the wide diagnostics dataframe into the compact per-match view used in
manual analysis: probability | BE | Edge | EV | uncertainty | decision,
followed by best side/total candidate, parlay gate, failure routes, data stage
and a transparent heuristic confidence score.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


def _num(v: Any, default=float("nan")) -> float:
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1","true","yes","y","confirmed","final"}
    try:
        if pd.isna(v):
            return False
    except Exception:
        pass
    return bool(v)


def event_key(row: pd.Series) -> str:
    eid=str(row.get("event_id") or "").strip()
    if eid and eid.lower() not in {"nan","none"}:
        return eid
    return f"{row.get('home_team','')}__{row.get('away_team','')}__{row.get('commence_time','')}"


def event_label(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "경기"
    r=frame.iloc[0]
    home=str(r.get("home_team", "홈"))
    away=str(r.get("away_team", "원정"))
    return f"{home} vs {away}"


def _option_label(row: pd.Series) -> str:
    market=str(row.get("market", ""))
    sel=str(row.get("selection", ""))
    home=str(row.get("home_team", ""))
    away=str(row.get("away_team", ""))
    point=_num(row.get("point"))
    nsel=re.sub(r"\s+", " ", sel).strip().lower()
    if market=="h2h":
        if nsel==re.sub(r"\s+", " ", home).strip().lower():
            return f"{home} 승"
        if nsel==re.sub(r"\s+", " ", away).strip().lower():
            return f"{away} 승"
        if "draw" in nsel or nsel in {"x","tie"}:
            return "무"
        return sel
    if market=="totals":
        side="O" if "over" in nsel else "U" if "under" in nsel else sel
        return f"{side}{point:g}" if math.isfinite(point) else side
    if market=="spreads":
        base=home if nsel==re.sub(r"\s+", " ", home).strip().lower() else away if nsel==re.sub(r"\s+", " ", away).strip().lower() else sel
        return f"{base} {point:+g}" if math.isfinite(point) else base
    return sel


def _uncertainty_label(row: pd.Series) -> str:
    risk=str(row.get("counter_case_risk", "")).upper()
    unc=_num(row.get("uncertainty_pp"), 0.0)
    if risk=="HIGH" or unc>=8.0:
        return "높음"
    if risk=="MEDIUM" or unc>=5.0:
        return "중간"
    return "낮음"


def _status(row: pd.Series) -> str:
    status=str(row.get("v3_decision_status") or row.get("robust_status") or row.get("selection_status") or row.get("grade") or "").strip().upper()
    return status or "미판정"


def _representative_totals(frame: pd.DataFrame) -> pd.DataFrame:
    t=frame[frame.get("market", pd.Series(index=frame.index,dtype=str)).eq("totals")].copy()
    if t.empty:
        return t
    t["_point"]=pd.to_numeric(t.get("point"),errors="coerce")
    # Prefer a two-sided line with the broadest book coverage; if tied, prefer
    # the line whose two no-vig probabilities are closest to 50/50.
    candidates=[]
    for point,g in t.dropna(subset=["_point"]).groupby("_point"):
        sides=set(str(x).lower() for x in g.get("selection",[]))
        two_sided=any("over" in x for x in sides) and any("under" in x for x in sides)
        books=pd.to_numeric(g.get("books"),errors="coerce").fillna(0).sum() if "books" in g else len(g)
        probs=pd.to_numeric(g["consensus_prob"],errors="coerce").dropna() if "consensus_prob" in g else pd.Series(dtype=float)
        balance=abs(float(probs.mean())-.5) if not probs.empty else 1.0
        candidates.append((0 if two_sided else 1,-float(books),balance,float(point)))
    if not candidates:
        return t.head(2)
    point=min(candidates)[3]
    out=t[t["_point"].eq(point)].copy()
    return out.drop(columns=["_point"],errors="ignore")


def compact_table(frame: pd.DataFrame, include_spread: bool=False) -> pd.DataFrame:
    """Return the compact FINAL table for one event."""
    if frame.empty:
        return pd.DataFrame(columns=["옵션","추정확률","BE","Edge","EV","불확실성","상태"])
    parts=[]
    h=frame[frame.get("market",pd.Series(index=frame.index,dtype=str)).eq("h2h")].copy()
    if not h.empty:
        # Stable display order: home, draw, away when possible.
        home=str(frame.iloc[0].get("home_team","")); away=str(frame.iloc[0].get("away_team",""))
        def ordv(r):
            lab=_option_label(r)
            if lab==f"{home} 승":return 0
            if lab=="무":return 1
            if lab==f"{away} 승":return 2
            return 3
        h["_ord"]=h.apply(ordv,axis=1)
        h=h.sort_values("_ord").drop(columns=["_ord"])
        parts.append(h)
    totals=_representative_totals(frame)
    if not totals.empty:
        totals=totals.copy()
        totals["_ord"]=totals.get("selection","").astype(str).str.lower().map(lambda x:0 if "over" in x else 1)
        totals=totals.sort_values("_ord").drop(columns=["_ord"])
        parts.append(totals)
    if include_spread:
        s=frame[frame.get("market",pd.Series(index=frame.index,dtype=str)).eq("spreads")].copy()
        if not s.empty:
            # keep only the best-EV spread on each side to avoid an unreadable card
            s=s.sort_values("ev_roi",ascending=False).head(2) if "ev_roi" in s else s.head(2)
            parts.append(s)
    chosen=pd.concat(parts,ignore_index=False) if parts else frame.head(8)
    rows=[]
    for _,r in chosen.iterrows():
        prob=_num(r.get("model_win_prob"))
        be=_num(r.get("break_even"))
        ev=_num(r.get("ev_roi"),_num(r.get("point_ev_roi")))
        edge=_num(r.get("edge_pp"))
        rows.append({
            "옵션":_option_label(r),
            "추정확률":f"{prob*100:.1f}%" if math.isfinite(prob) else "-",
            "BE":f"{be*100:.1f}%" if math.isfinite(be) else "-",
            "Edge":f"{edge:+.1f}%p" if math.isfinite(edge) else "-",
            "EV":f"{ev*100:+.1f}%" if math.isfinite(ev) else "-",
            "불확실성":_uncertainty_label(r),
            "상태":_status(r),
        })
    return pd.DataFrame(rows)


def _candidate_row(frame: pd.DataFrame, market: str):
    x=frame[frame.get("market",pd.Series(index=frame.index,dtype=str)).eq(market)].copy()
    if x.empty:return None
    if market=="totals":
        x=_representative_totals(x)
    x["_ev"]=pd.to_numeric(x.get("ev_roi",x.get("point_ev_roi")),errors="coerce")
    x["_p10"]=pd.to_numeric(x.get("robust_ev_p10"),errors="coerce")
    x["_eligible"]=x.get("v3_candidate",pd.Series(False,index=x.index)).fillna(False).astype(bool)
    x["_rank"]=x.get("v3_decision_status",pd.Series("",index=x.index)).map({"ROBUST":0,"SENSITIVE":1,"FRAGILE":2,"REVIEW":3,"PASS":4,"DATA_HOLD":5}).fillna(9)
    good=x[x["_eligible"] & x["_ev"].gt(0)]
    if good.empty:return None
    good=good.sort_values(["_rank","_p10","_ev"],ascending=[True,False,False])
    return good.iloc[0]


def _failure_text(row: pd.Series | None) -> str:
    if row is None:return "핵심 +EV 후보가 없어 별도 실패경로를 선정하지 않음"
    raw=str(row.get("counter_case_summary") or "").strip()
    if not raw:return "현재 자동 반증 엔진에서 중대한 추가 실패경로를 찾지 못함"
    repl=[
        (r"recent sample only (\d+) matches",r"최근 표본이 \1경기로 작음"),
        (r"recent sample (\d+) matches",r"최근 표본이 \1경기로 얇음"),
        (r"starting lineup not confirmed",r"선발 라인업 미확정·로테이션 위험"),
        (r"independent model strongly conflicts with market \(([^)]+)\)",r"독립모델과 시장의 강한 충돌(\1)"),
        (r"independent model/market gap requires monitoring",r"모델-시장 괴리 추가 확인 필요"),
        (r"model uncertainty ([0-9.]+)pp",r"모델 불확실성 \1%p"),
        (r"deep signal coverage ([0-9]+)%",r"xG·결장·선수영향 등 정밀 신호 확보율 \1%"),
        (r"pregame stage is ([A-Z _-]+)",r"사전 데이터 단계가 \1"),
        (r"advanced signal completeness ([0-9]+)%",r"고급 신호 완성도 \1%"),
        (r"national-team samples/venues/rotation are less stable",r"국가대표 특유의 표본·중립구장·로테이션 변동성"),
    ]
    txt=raw
    for pat,sub in repl:
        txt=re.sub(pat,sub,txt,flags=re.I)
    txt=txt.replace(" | "," + ")
    return txt


def data_status(frame: pd.DataFrame) -> str:
    if frame.empty:return "DATA HOLD"
    r=frame.iloc[0]
    stage=str(r.get("stage") or "").strip()
    if stage:
        return stage
    lineup=any(_bool(v) for v in frame.get("lineup_confirmed",pd.Series(False,index=frame.index)))
    attempted=any(_bool(v) for v in frame.get("deep_context_attempted",pd.Series(False,index=frame.index)))
    coverage=pd.to_numeric(frame.get("signal_coverage"),errors="coerce").max() if "signal_coverage" in frame else float("nan")
    if lineup:
        if attempted and math.isfinite(_num(coverage)) and _num(coverage)<.75:
            return "LINEUP CONFIRMED · CONTEXT PARTIAL"
        return "LINEUP CONFIRMED"
    return "PRE-LINEUP" if attempted else "BASIC DATA"


def confidence_score(frame: pd.DataFrame, focus: pd.Series | None=None) -> int:
    """Transparent heuristic confidence, not a calibrated win probability."""
    if frame.empty:return 0
    r=focus if focus is not None else frame.iloc[0]
    unc=_num(r.get("uncertainty_pp"),7.0)
    ratio=_num(r.get("robust_positive_ratio"),.5)
    cov=_num(r.get("signal_coverage"),.0)
    score=92.0-2.6*unc
    if _bool(r.get("lineup_confirmed")):score+=5
    elif _bool(r.get("deep_context_attempted")):score-=5
    score+=9.0*(ratio-.5)
    if _bool(r.get("deep_context_attempted")):
        score+=10.0*(cov-.5)
    risk=str(r.get("counter_case_risk","")).upper()
    score-=12 if risk=="HIGH" else 5 if risk=="MEDIUM" else 0
    sanity=str(r.get("sanity","")).upper()
    score-=15 if sanity in {"HIGH_DISAGREEMENT","OUTLIER_SHRUNK"} else 5 if sanity=="CHECK" else 0
    sample=min(_num(r.get("home_form_matches"),0),_num(r.get("away_form_matches"),0))
    if sample>=7:score+=3
    elif sample<4:score-=5
    return int(round(max(25,min(95,score))))


def event_summary(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"model_best":"없음","total_best":"없음","parlay":"없음","failure":"-","data_status":"DATA HOLD","confidence":0}
    side=_candidate_row(frame,"h2h")
    total=_candidate_row(frame,"totals")
    model_best=_option_label(side) if side is not None else "없음 (+EV/강건성 기준 미달)"
    total_best=_option_label(total) if total is not None else "없음 (+EV/강건성 기준 미달)"
    eligible=frame[frame.get("v3_parlay_eligible",pd.Series(False,index=frame.index)).fillna(False).astype(bool)].copy()
    if not eligible.empty:
        eligible["_p10"]=pd.to_numeric(eligible.get("robust_ev_p10"),errors="coerce")
        eligible["_ev"]=pd.to_numeric(eligible.get("ev_roi"),errors="coerce")
        p=eligible.sort_values(["_p10","_ev"],ascending=False).iloc[0]
        parlay=f"{_option_label(p)} 포함 / 같은 경기의 다른 선택지는 동일 경기 조합 제외"
        focus=p
    else:
        parlay="이 경기에서 자동 다폴 포함 후보 없음"
        focus=side if side is not None else total if total is not None else frame.sort_values("ev_roi",ascending=False).iloc[0] if "ev_roi" in frame else frame.iloc[0]
    return {
        "model_best":model_best,
        "total_best":total_best,
        "parlay":parlay,
        "failure":_failure_text(focus),
        "data_status":data_status(frame),
        "confidence":confidence_score(frame,focus),
        "focus_status":_status(focus),
        "focus_pick":_option_label(focus),
        "robust_positive_ratio":_num(focus.get("robust_positive_ratio"), float("nan")),
        "robust_scenario_count":int(_num(focus.get("robust_scenario_count"), 0) or 0),
        "robust_ev_p10":_num(focus.get("robust_ev_p10"), float("nan")),
        "missing_signals":str(focus.get("missing_signals") or "").strip(),
        "signal_summary":str(focus.get("signal_summary") or "").strip(),
    }


def split_events(frame: pd.DataFrame):
    if frame is None or frame.empty:return []
    tmp=frame.copy()
    tmp["_event_key"]=tmp.apply(event_key,axis=1)
    out=[]
    for key,g in tmp.groupby("_event_key",sort=False):
        out.append((str(key),event_label(g),g.drop(columns=["_event_key"])))
    return out
