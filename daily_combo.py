"""Cross-sport KST daily candidate pool and risk-adjusted combo builder.

The pool is built from immutable prediction snapshots already produced by each
analysis tab.  It deliberately keeps PRE-LINEUP candidates visible, but shrinks
model-vs-market disagreement when data maturity is low.  Once a lineup is
confirmed and that sport is re-analysed, the newer snapshot automatically wins
for that event/market/selection.
"""
from __future__ import annotations

import itertools
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from .prediction_store import load_predictions
from .correlation_engine import correlation_adjusted_hit, historical_correlations
from .model_drift import drift_rows

KST = ZoneInfo("Asia/Seoul")
DEFAULT_PREDICTIONS = "data/prediction_snapshots.jsonl"

SPORT_LABELS = {
    "soccer_club": "클럽축구",
    "soccer_national": "A매치",
    "baseball_kbo": "KBO",
    "baseball_npb": "NPB",
    "baseball_mlb": "MLB",
}


def _num(v: Any, default=float("nan")) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _truth(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    try:
        if pd.isna(v):
            return False
    except Exception:
        pass
    return bool(v) if v is not None else False


def _read_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def _kickoff_kst(v: Any):
    ts = pd.to_datetime(v, utc=True, errors="coerce")
    if pd.isna(ts):
        return pd.NaT
    return ts.tz_convert(KST)


def _identity(row: dict) -> tuple:
    point = row.get("point")
    try:
        point = round(float(point), 4)
    except (TypeError, ValueError):
        point = None
    return (
        str(row.get("event_id") or f"{row.get('home_team','')}__{row.get('away_team','')}__{row.get('commence_time','')}"),
        str(row.get("sport_key") or row.get("sport_family") or ""),
        str(row.get("market") or ""),
        str(row.get("selection") or ""),
        point,
    )


def latest_snapshots_for_kst_date(selected_date: date, path: str | Path = DEFAULT_PREDICTIONS) -> pd.DataFrame:
    """Latest immutable snapshot per pick for one KST calendar date."""
    rows = []
    for row in load_predictions(path):
        kst = _kickoff_kst(row.get("commence_time"))
        if pd.isna(kst) or kst.date() != selected_date:
            continue
        x = dict(row)
        x["kickoff_kst"] = kst
        rows.append(x)
    if not rows:
        return pd.DataFrame()

    # A later re-analysis after lineup/price movement supersedes the older view
    # in the daily screen, while the original snapshot stays immutable on disk.
    rows.sort(key=lambda r: str(r.get("recorded_at") or ""))
    latest: dict[tuple, dict] = {}
    for row in rows:
        latest[_identity(row)] = row
    return pd.DataFrame(list(latest.values()))


def _stage_factor(row: pd.Series) -> tuple[float, str, str]:
    stage = str(row.get("stage") or "").strip().upper()
    quality = str(row.get("data_quality") or "").strip().upper()
    lineup = _truth(row.get("lineup_confirmed"))

    if stage == "FINAL" or (quality == "HIGH" and lineup):
        return 1.00, "확정", "FINAL"
    if "PROBABLE" in stage:
        return 0.93, "예상 라인업", stage
    if "PRE" in stage:
        return 0.89, "미확정", stage
    if "LINEUP" in stage or lineup:
        return 0.98, "확정", stage or "LINEUP CONFIRMED"
    if "STARTER" in stage:
        return 0.95, "선발만 확정", stage
    if "PARTIAL" in stage:
        return 0.91, "미확정", stage
    if quality == "LOW":
        return 0.86, "미확정", stage or "LOW DATA"
    return 0.90, "미확정", stage or "BASIC DATA"


def _risk_factor(row: pd.Series) -> float:
    risk = str(row.get("counter_case_risk") or "").upper()
    if risk == "HIGH":
        return 0.70
    if risk == "MEDIUM":
        return 0.88
    return 1.0


def _pick_label(row: pd.Series) -> str:
    market = str(row.get("market") or "")
    sel = str(row.get("selection") or "")
    home = str(row.get("home_team") or "")
    away = str(row.get("away_team") or "")
    point = _num(row.get("point"))
    nsel = re.sub(r"\s+", " ", sel).strip().lower()
    nhome = re.sub(r"\s+", " ", home).strip().lower()
    naway = re.sub(r"\s+", " ", away).strip().lower()
    if market == "h2h":
        if nsel == nhome:
            return f"{home} 승"
        if nsel == naway:
            return f"{away} 승"
        if nsel in {"draw", "tie", "x"} or "draw" in nsel:
            return "무"
        return sel
    if market == "totals":
        side = "O" if "over" in nsel else "U" if "under" in nsel else sel
        return f"{side}{point:g}" if math.isfinite(point) else side
    if market == "spreads":
        base = home if nsel == nhome else away if nsel == naway else sel
        return f"{base} {point:+g}" if math.isfinite(point) else base
    return sel


def _sport_label(row: pd.Series) -> str:
    fam = str(row.get("sport_family") or "")
    if fam in SPORT_LABELS:
        return SPORT_LABELS[fam]
    skey = str(row.get("sport_key") or "")
    if skey == "baseball_mlb":
        return "MLB"
    if skey == "baseball_kbo":
        return "KBO"
    if skey == "baseball_npb":
        return "NPB"
    if skey.startswith("soccer_"):
        return "축구"
    return fam or skey or "기타"


def prepare_daily_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize all sports into one comparable candidate table.

    v3.4.1 separates *candidate visibility* from *combo eligibility*.
    A ROBUST/SENSITIVE pick with positive current EV remains visible even when
    a strict safety gate (HIGH counter-case risk, stale data, drift ALERT,
    adaptive review, etc.) blocks it from the actual accumulator.  This avoids
    the confusing state where an event shows 27/27 stress-positive in its own
    analysis but the daily tab reports zero candidates.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    try:
        _drift_map={(str(x.get("sport_family") or ""),str(x.get("market") or "")):str(x.get("status") or "") for x in drift_rows()}
    except Exception:
        _drift_map={}
    rows=[]
    for _,r in frame.iterrows():
        national = str(r.get('sport_family') or '') == 'soccer_national' or str(r.get('national_status')) in {'FINAL_BET','PROVISIONAL','COMBO_EXCLUDE','NO_BET'}
        if national:
            from .national_policy import finalize_national
            r=pd.Series(finalize_national(r))
        status=str(r.get("v3_decision_status") or "").upper()
        odds=_num(r.get("best_odds")); model=_num(r.get("model_win_prob"))
        market=_num(r.get("consensus_prob"),_num(r.get("break_even")))
        be=_num(r.get("break_even")); push=max(0.0,_num(r.get("push_prob"),0.0))
        raw_ev=_num(r.get("ev_roi"),_num(r.get("point_ev_roi")))
        if not (math.isfinite(odds) and odds>1 and math.isfinite(model) and math.isfinite(market)):
            continue
        if not math.isfinite(raw_ev):
            raw_ev=odds*model+push-1.0
        if raw_ev<=0:
            continue

        stage_factor,lineup_state,stage_label=_stage_factor(r)
        risk_factor=_risk_factor(r)
        drift=_drift_map.get((str(r.get("sport_family") or ""),str(r.get("market") or "")),"")
        reasons=[]
        # Visibility and actionability are intentionally separate.  Every
        # positive raw-EV row is shown; only rows passing the stricter v3
        # safety gates can become a single/actionable pick or accumulator leg.
        single_eligible=True
        combo_eligible=True

        if status not in {"ROBUST","SENSITIVE"}:
            single_eligible=False
            combo_eligible=False
            if status=="REVIEW": reasons.append("v3 REVIEW — 모델/시장 또는 데이터 추가 검토 필요")
            elif status=="PASS": reasons.append("v3 PASS — 기준 +EV라도 보수 검증 기준 미통과")
            elif status=="FRAGILE": reasons.append("v3 FRAGILE — 가정 변화에 취약")
            elif status=="DATA_HOLD": reasons.append("v3 DATA_HOLD — 핵심 데이터 부족")
            else: reasons.append(f"v3 판정 {status or '미확인'}")
        if "v3_candidate" in r.index and not _truth(r.get("v3_candidate")):
            single_eligible=False
            combo_eligible=False
            reasons.append("v3 보수 후보 게이트 미통과")
        adaptive=str(r.get("adaptive_gate") or "OK").upper()
        if adaptive not in {"","OK"}:
            single_eligible=False
            combo_eligible=False
            if adaptive=="MODEL_CONFLICT_REVIEW": reasons.append("앙상블 모델 충돌로 REVIEW")
            elif adaptive=="PASS_AFTER_CALIBRATION": reasons.append("보수/캘리브레이션 게이트에서 조합 제외")
            else: reasons.append(f"적응형 게이트 {adaptive}")
        if drift=="ALERT":
            single_eligible=False; combo_eligible=False; reasons.append("최근 모델 성능 drift ALERT")
        elif drift=="WATCH":
            risk_factor*=0.94; reasons.append("최근 모델 성능 drift WATCH")

        rec=pd.to_datetime(r.get("recorded_at"),utc=True,errors="coerce")
        kick=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        now=pd.Timestamp.now(tz="UTC")
        age=float("nan")
        if not pd.isna(rec) and not pd.isna(kick) and kick>now:
            age=(now-rec).total_seconds()/60
            if age>180:
                single_eligible=False; combo_eligible=False; reasons.append("분석 스냅샷 3시간 초과·재분석 필요")
            elif age>90:
                risk_factor*=0.90; reasons.append("분석 스냅샷 90분 초과")

        counter=str(r.get("counter_case_risk") or "").upper()
        if counter=="HIGH":
            single_eligible=False
            combo_eligible=False
            reasons.append("반증/데이터 위험 HIGH")

        if national and r.get("national_status") != "FINAL_BET":
            single_eligible=False; combo_eligible=False
            reasons.append(str(r.get("national_status_label")) + " · " + str(r.get("match_data_missing") or ""))

        maturity=stage_factor*risk_factor
        adj_prob=market+maturity*(model-market)
        adj_prob=max(0.001,min(max(0.001,1-push-0.001),adj_prob))
        adj_ev=odds*adj_prob+push-1.0
        if adj_ev<=0:
            single_eligible=False
            combo_eligible=False
            reasons.append("보수 확률 축소 후 EV≤0")

        robust_ratio=_num(r.get("robust_positive_ratio"),0.5)
        p10=_num(r.get("robust_ev_p10"),adj_ev)
        unc=max(0.0,_num(r.get("uncertainty_pp"),7.0))
        ev_component=min(max(adj_ev,0.0),0.20)/0.20
        p10_component=min(max(p10,-0.10),0.15)
        quality=(
            0.47*adj_prob
            +0.20*ev_component
            +0.18*max(0.0,min(1.0,robust_ratio))
            +0.10*stage_factor
            +0.05*max(0.0,min(1.0,(p10_component+0.10)/0.25))
            -min(unc,15.0)*0.005
        )
        # Positive-EV underdogs can remain valid *single* candidates while the
        # accumulator layer stays conservative and requires >=50% risk-adjusted
        # hit probability per leg.
        if single_eligible and adj_prob < 0.50:
            combo_eligible=False
            reasons.append("2폴 이상 보수확률 50% 미만 — 단일 후보만")

        kickoff=r.get("kickoff_kst")
        if pd.isna(kickoff): kickoff=_kickoff_kst(r.get("commence_time"))
        event_id=str(r.get("event_id") or f"{r.get('home_team','')}__{r.get('away_team','')}__{r.get('commence_time','')}")
        candidate_state=("최종 후보" if combo_eligible else "조합 제외") if national and r.get("national_status")=="FINAL_BET" else str(r.get("national_status_label")) if national else ("조합 가능" if combo_eligible else "단일 후보" if single_eligible else "검토 후보")
        reason=" · ".join(dict.fromkeys(reasons)) if reasons else "강건성·리스크 게이트 통과"
        rows.append({
            **r.to_dict(),
            "sport_label":_sport_label(r),
            "event_key":event_id,
            "game_label":f"{r.get('home_team','')} vs {r.get('away_team','')}",
            "pick_label":_pick_label(r),
            "kickoff_kst":kickoff,
            "lineup_state":lineup_state,
            "daily_stage":stage_label,
            "daily_stage_factor":stage_factor,
            "daily_adjusted_prob":adj_prob,
            "daily_adjusted_ev":adj_ev,
            "daily_quality_score":quality*100.0,
            "daily_provisional":lineup_state!="확정",
            "daily_original_prob":model,
            "daily_original_ev":raw_ev,
            "daily_be":be,
            "daily_drift_status":drift or "OK",
            "daily_single_eligible":bool(single_eligible),
            "daily_combo_eligible":bool(combo_eligible),
            "daily_candidate_state":candidate_state,
            "daily_gate_reason":reason,
            "daily_snapshot_age_min":age,
        })
    out=pd.DataFrame(rows)
    if out.empty:return out
    return out.sort_values(["daily_combo_eligible","daily_quality_score","daily_adjusted_prob","daily_adjusted_ev"],ascending=[False,False,False,False]).reset_index(drop=True)


def _combo_row(combo: Iterable[dict], corr_matrix=None) -> dict | None:
    legs = list(combo)
    if not legs:
        return None
    if len({x["event_key"] for x in legs}) != len(legs):
        return None
    # For multi-leg "best combo" mode, avoid sub-50% legs. Positive-EV
    # underdogs can still be the strongest single but are not forced into a parlay.
    if len(legs) >= 2 and any(float(x["daily_adjusted_prob"]) < 0.50 for x in legs):
        return None
    hit, corr_meta = correlation_adjusted_hit(legs, matrix=corr_matrix)
    odds = math.prod(float(x["best_odds"]) for x in legs)
    if len(legs) > 2:
        hit *= 0.985 ** (len(legs) - 2)
    # Slight portfolio haircut when every leg comes from the same sport family.
    if len(legs) >= 3 and len({str(x.get("sport_family") or "") for x in legs}) == 1:
        hit *= 0.99
    ev = hit * odds - 1.0
    if ev <= 0:
        return None
    avg_quality = sum(float(x["daily_quality_score"]) for x in legs) / len(legs)
    provisional = sum(1 for x in legs if x.get("daily_provisional"))
    # Survival probability is intentionally the largest component.  This avoids
    # choosing a large-odds, fragile accumulator merely because its modeled EV is high.
    ev_norm = min(ev, 0.40) / 0.40
    score = 0.66 * hit + 0.22 * ev_norm + 0.12 * (avg_quality / 100.0) - provisional * 0.01
    return {
        "legs": legs,
        "folder_count": len(legs),
        "combined_odds": odds,
        "estimated_hit_prob": hit,
        "estimated_ev": ev,
        "provisional_legs": provisional,
        "naive_hit_prob": corr_meta.get("naive", hit),
        "correlation_pairs_used": corr_meta.get("pairs_used", 0),
        "correlation_coverage": corr_meta.get("coverage", 0.0),
        "avg_pair_rho": corr_meta.get("avg_rho", 0.0),
        "score": score,
        "combo_label": " + ".join(f"[{x['sport_label']}] {x['pick_label']}" for x in legs),
    }


def best_combos(candidates: pd.DataFrame, sizes=(1, 2, 3), top_n=5) -> dict[int, list[dict]]:
    if candidates is None or candidates.empty:
        return {int(n): [] for n in sizes}
    corr_matrix = historical_correlations()
    out: dict[int, list[dict]] = {}
    for n in sizes:
        pool_frame=candidates.copy()
        eligibility_col="daily_single_eligible" if int(n)==1 else "daily_combo_eligible"
        if eligibility_col in pool_frame.columns:
            pool_frame=pool_frame[pool_frame[eligibility_col].fillna(False).astype(bool)].copy()
        if pool_frame.empty:
            out[int(n)]=[]
            continue
        # The first 24 eligible candidates are enough for a daily search and keep
        # 3-leg combinations computationally cheap on Streamlit Cloud.
        records = pool_frame.head(24).to_dict("records")
        rows = []
        for combo in itertools.combinations(records, int(n)):
            row = _combo_row(combo, corr_matrix=corr_matrix)
            if row is not None:
                rows.append(row)
        rows.sort(key=lambda x: (x["score"], x["estimated_hit_prob"], x["estimated_ev"]), reverse=True)
        out[int(n)] = rows[:top_n]
    return out


def combo_display_rows(combo: dict | None) -> pd.DataFrame:
    if not combo:
        return pd.DataFrame()
    rows = []
    for x in combo["legs"]:
        kickoff = x.get("kickoff_kst")
        kt = kickoff.strftime("%m/%d %H:%M") if hasattr(kickoff, "strftime") else str(kickoff or "-")
        rows.append({
            "종목": x.get("sport_label", ""),
            "경기시간(KST)": kt,
            "경기": x.get("game_label", ""),
            "픽": x.get("pick_label", ""),
            "배당": round(float(x.get("best_odds") or 0), 2),
            "모델확률": f"{float(x.get('daily_original_prob') or 0)*100:.1f}%",
            "조합용 보수확률": f"{float(x.get('daily_adjusted_prob') or 0)*100:.1f}%",
            "EV": f"{float(x.get('daily_original_ev') or 0)*100:+.1f}%",
            "라인업": x.get("lineup_state", ""),
            "판정": x.get("national_status_label", x.get("v3_decision_status", "")),
        })
    return pd.DataFrame(rows)
