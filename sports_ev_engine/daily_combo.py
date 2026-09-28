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

    PRE-LINEUP is never silently treated as FINAL.  Its model edge is shrunk
    toward the market before combo ranking.  This is a ranking haircut only;
    the original model probability/EV remains visible unchanged.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    rows = []
    for _, r in frame.iterrows():
        status = str(r.get("v3_decision_status") or "").upper()
        if "v3_candidate" in r.index and not _truth(r.get("v3_candidate")):
            continue
        if status not in {"ROBUST", "SENSITIVE"}:
            continue
        if str(r.get("adaptive_gate") or "OK").upper() not in {"", "OK"}:
            continue
        odds = _num(r.get("best_odds"))
        model = _num(r.get("model_win_prob"))
        market = _num(r.get("consensus_prob"), _num(r.get("break_even")))
        be = _num(r.get("break_even"))
        base_ev = _num(r.get("conservative_ev_roi"), _num(r.get("ev_roi")))
        if not (math.isfinite(odds) and odds > 1 and math.isfinite(model) and math.isfinite(market)):
            continue
        stage_factor, lineup_state, stage_label = _stage_factor(r)
        risk_factor = _risk_factor(r)
        # HIGH-risk picks remain visible in diagnostics elsewhere but are too
        # fragile to be called a daily best-combo candidate.
        if risk_factor <= 0.70:
            continue
        maturity = stage_factor * risk_factor
        adj_prob = market + maturity * (model - market)
        adj_prob = max(0.001, min(0.999, adj_prob))
        if not math.isfinite(base_ev):
            base_ev = odds * model - 1.0
        adj_ev = base_ev * maturity
        if adj_ev <= 0:
            continue
        robust_ratio = _num(r.get("robust_positive_ratio"), 0.5)
        p10 = _num(r.get("robust_ev_p10"), adj_ev)
        unc = max(0.0, _num(r.get("uncertainty_pp"), 7.0))
        # Transparent, survival-first ranking.  EV is capped so a longshot with
        # a huge theoretical edge cannot dominate a lower-risk daily combo.
        ev_component = min(max(adj_ev, 0.0), 0.20) / 0.20
        p10_component = min(max(p10, -0.10), 0.15)
        quality = (
            0.47 * adj_prob
            + 0.20 * ev_component
            + 0.18 * max(0.0, min(1.0, robust_ratio))
            + 0.10 * stage_factor
            + 0.05 * max(0.0, min(1.0, (p10_component + 0.10) / 0.25))
            - min(unc, 15.0) * 0.005
        )
        kickoff = r.get("kickoff_kst")
        if pd.isna(kickoff):
            kickoff = _kickoff_kst(r.get("commence_time"))
        event_id = str(r.get("event_id") or f"{r.get('home_team','')}__{r.get('away_team','')}__{r.get('commence_time','')}")
        rows.append({
            **r.to_dict(),
            "sport_label": _sport_label(r),
            "event_key": event_id,
            "game_label": f"{r.get('home_team','')} vs {r.get('away_team','')}",
            "pick_label": _pick_label(r),
            "kickoff_kst": kickoff,
            "lineup_state": lineup_state,
            "daily_stage": stage_label,
            "daily_stage_factor": stage_factor,
            "daily_adjusted_prob": adj_prob,
            "daily_adjusted_ev": adj_ev,
            "daily_quality_score": quality * 100.0,
            "daily_provisional": lineup_state != "확정",
            "daily_original_prob": model,
            "daily_original_ev": _num(r.get("ev_roi"), base_ev),
            "daily_be": be,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["daily_quality_score", "daily_adjusted_prob", "daily_adjusted_ev"], ascending=False).reset_index(drop=True)


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
    # The first 24 candidates are enough for a daily best-combo search and keep
    # 3-leg combinations computationally cheap on Streamlit Cloud.
    records = candidates.head(24).to_dict("records")
    corr_matrix = historical_correlations()
    out: dict[int, list[dict]] = {}
    for n in sizes:
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
            "판정": x.get("v3_decision_status", ""),
        })
    return pd.DataFrame(rows)
