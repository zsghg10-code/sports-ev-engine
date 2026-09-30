"""Immutable prediction history, CLV tracking, settlement and evaluation.

v3.2 adds:
- optional Supabase mirror/recovery through :mod:`persistent_store`,
- append-only market observations for closing-line tracking,
- CLV fields attached at settlement,
- DB-aware reads for daily combo / validation.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .core.asian import settle_total_under, settle_total_over, settle_home_handicap
from . import persistent_store

MODEL_VERSION = "3.4.21"
DEFAULT_PREDICTIONS = "data/prediction_snapshots.jsonl"
DEFAULT_SETTLED = "data/settled_predictions.jsonl"
DEFAULT_MARKET_OBSERVATIONS = "data/market_observations.jsonl"
DEFAULT_REFRESH_EVENTS = "data/refresh_events.jsonl"

_KIND_BY_PATH = {
    DEFAULT_PREDICTIONS: ("prediction", "fingerprint"),
    DEFAULT_SETTLED: ("settled", "fingerprint"),
    DEFAULT_MARKET_OBSERVATIONS: ("market_observation", "observation_id"),
    DEFAULT_REFRESH_EVENTS: ("refresh_event", "refresh_id"),
}


def configure_persistence(url: str | None = None, key: str | None = None):
    persistent_store.configure(url, key)


def persistence_status():
    return persistent_store.status()


def _safe(v):
    if isinstance(v, (list, dict, tuple)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _local_append(path, rows):
    if not rows:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _local_load(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except Exception:
                pass
    return out


def _path_meta(path: str):
    # Exact defaults first, then basename fallback for tests/custom directories.
    if path in _KIND_BY_PATH:
        return _KIND_BY_PATH[path]
    name = Path(path).name
    if name == "prediction_snapshots.jsonl":
        return "prediction", "fingerprint"
    if name == "settled_predictions.jsonl":
        return "settled", "fingerprint"
    if name == "market_observations.jsonl":
        return "market_observation", "observation_id"
    if name == "refresh_events.jsonl":
        return "refresh_event", "refresh_id"
    return None, None


def _append_jsonl(path, rows, *, kind: str | None = None, id_field: str | None = None):
    _local_append(path, rows)
    k, i = _path_meta(path)
    kind = kind or k
    id_field = id_field or i
    if kind and id_field:
        persistent_store.mirror(kind, rows, id_field=id_field)


def _load(path, *, kind: str | None = None, id_field: str | None = None):
    local = _local_load(path)
    k, i = _path_meta(path)
    kind = kind or k
    id_field = id_field or i
    if not kind or not id_field or not persistent_store.enabled():
        return local
    remote = persistent_store.load(kind)
    return persistent_store.merge(local, remote, id_field=id_field)


def load_predictions(path=DEFAULT_PREDICTIONS):
    return _load(path, kind="prediction", id_field="fingerprint")


def load_settled(path=DEFAULT_SETTLED):
    return _load(path, kind="settled", id_field="fingerprint")


def load_market_observations(path=DEFAULT_MARKET_OBSERVATIONS):
    return _load(path, kind="market_observation", id_field="observation_id")


def _norm_point(v):
    try:
        if v is None or pd.isna(v):
            return None
    except Exception:
        if v is None:
            return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _market_identity(row: dict) -> tuple:
    return (
        str(row.get("event_id") or ""),
        str(row.get("sport_key") or ""),
        str(row.get("market") or ""),
        str(row.get("selection") or ""),
        _norm_point(row.get("point")),
    )


def record_market_frame(
    frame: pd.DataFrame,
    sport_key: str | None = None,
    *,
    source: str = "analysis",
    observed_at: str | None = None,
    path: str = DEFAULT_MARKET_OBSERVATIONS,
):
    """Append market observations used for CLV and movement tracking."""
    if frame is None or frame.empty:
        return 0
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    existing = load_market_observations(path)
    ids = {x.get("observation_id") for x in existing}
    rows = []
    keep = [
        "event_id", "commence_time", "home_team", "away_team", "market", "selection", "point",
        "best_book", "best_odds", "books", "consensus_prob", "sport_key",
    ]
    for _, r in frame.iterrows():
        d = {k: _safe(r.get(k)) for k in keep if k in frame.columns}
        d["sport_key"] = d.get("sport_key") or sport_key or ""
        d["observed_at"] = observed_at
        d["source"] = source
        raw = "|".join(str(x) for x in [
            *_market_identity(d),
            round(float(d.get("best_odds") or 0), 6),
            round(float(d.get("consensus_prob") or 0), 8),
            observed_at[:16],  # one immutable state per minute is enough for closing-line tracking
        ])
        oid = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        d["observation_id"] = oid
        if oid in ids:
            continue
        ids.add(oid)
        rows.append(d)
    _append_jsonl(path, rows, kind="market_observation", id_field="observation_id")
    return len(rows)


def latest_market_observation(
    snapshot: dict,
    *,
    before: Any | None = None,
    observations: list[dict] | None = None,
    path: str = DEFAULT_MARKET_OBSERVATIONS,
    allow_line_fallback: bool = False,
):
    obs = observations if observations is not None else load_market_observations(path)
    ident = _market_identity(snapshot)
    cutoff = pd.to_datetime(before or snapshot.get("commence_time"), utc=True, errors="coerce")
    matches = []
    fallback = []
    base=(ident[0],ident[1],ident[2],ident[3])
    for x in obs:
        xid=_market_identity(x)
        ts = pd.to_datetime(x.get("observed_at"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        if not pd.isna(cutoff) and ts > cutoff:
            continue
        if xid == ident:
            matches.append((ts, x))
        elif allow_line_fallback and xid[:4] == base:
            fallback.append((ts,x))
    use=(matches+fallback) if allow_line_fallback else matches
    if not use:
        return None
    use.sort(key=lambda z: z[0])
    return use[-1][1]


def _clv_fields(snapshot: dict, observations: list[dict]) -> dict:
    close = latest_market_observation(snapshot, observations=observations, allow_line_fallback=True)
    if close:
        entry_ts=pd.to_datetime(snapshot.get("recorded_at"),utc=True,errors="coerce")
        close_ts=pd.to_datetime(close.get("observed_at"),utc=True,errors="coerce")
        # The analysis-time observation itself is not a closing line. Require a
        # genuinely later observation so CLV is never fabricated as 0.0.
        if (not pd.isna(entry_ts)) and (not pd.isna(close_ts)) and close_ts <= entry_ts + pd.Timedelta(seconds=30):
            close=None
    if not close:
        return {
            "closing_odds": None, "closing_consensus_prob": None, "closing_point": None,
            "clv_odds_pct": None, "clv_implied_pp": None, "clv_market_prob_pp": None, "clv_line_points": None,
            "closing_observed_at": None,
        }
    try:
        entry_odds = float(snapshot.get("best_odds"))
        closing_odds = float(close.get("best_odds"))
    except (TypeError, ValueError):
        entry_odds = closing_odds = None
    try:
        entry_market = float(snapshot.get("consensus_prob"))
        closing_market = float(close.get("consensus_prob"))
    except (TypeError, ValueError):
        entry_market = closing_market = None
    entry_point=_norm_point(snapshot.get("point"));closing_point=_norm_point(close.get("point"))
    same_line=(entry_point==closing_point)
    line_clv=None
    if entry_point is not None and closing_point is not None and entry_point!=closing_point:
        market=str(snapshot.get("market") or "");sel=str(snapshot.get("selection") or "").lower()
        if market=="spreads":line_clv=entry_point-closing_point
        elif market=="totals":line_clv=(closing_point-entry_point) if "over" in sel else (entry_point-closing_point)
    return {
        "closing_odds": closing_odds,
        "closing_consensus_prob": closing_market,
        "closing_point": closing_point,
        # Price/probability CLV is only comparable on the same handicap/total line.
        "clv_odds_pct": (entry_odds / closing_odds - 1.0) if same_line and entry_odds and closing_odds else None,
        "clv_implied_pp": ((1.0 / closing_odds) - (1.0 / entry_odds)) * 100.0 if same_line and entry_odds and closing_odds else None,
        "clv_market_prob_pp": (closing_market - entry_market) * 100.0 if same_line and entry_market is not None and closing_market is not None else None,
        "clv_line_points": line_clv,
        "closing_observed_at": close.get("observed_at"),
    }


def record_frame(frame: pd.DataFrame, sport_key: str | None = None, sport_family: str = "", path=DEFAULT_PREDICTIONS):
    if frame is None or frame.empty:
        return 0
    existing = load_predictions(path)
    fingerprints = {x.get("fingerprint") for x in existing}
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    keep = [
        "event_id", "commence_time", "home_team", "away_team", "market", "selection", "point", "best_book", "best_odds", "books",
        "consensus_prob", "raw_independent_prob", "model_win_prob", "push_prob", "break_even", "edge_pp", "ev_roi", "point_ev_roi",
        "conservative_ev_roi", "uncertainty_pp", "grade", "sanity", "stage", "data_quality", "sport_key", "home_lambda", "away_lambda",
        "home_expected_runs", "away_expected_runs", "home_starter", "away_starter", "home_starter_expected_ip", "away_starter_expected_ip",
        "home_starter_recent_bb_pct", "away_starter_recent_bb_pct", "home_starter_recent_k_pct", "away_starter_recent_k_pct",
        "home_starter_recent_kbb_pct", "away_starter_recent_kbb_pct",
        "home_starter_vs_opponent_games", "away_starter_vs_opponent_games", "home_starter_vs_opponent_ip", "away_starter_vs_opponent_ip",
        "home_starter_vs_opponent_era", "away_starter_vs_opponent_era", "home_starter_vs_opponent_kbb_pct", "away_starter_vs_opponent_kbb_pct",
        "home_starter_vs_opponent_team", "away_starter_vs_opponent_team",
        "home_bullpen_pitches_last3", "away_bullpen_pitches_last3", "home_bullpen_relief_ip_last3", "away_bullpen_relief_ip_last3",
        "home_bullpen_exact", "away_bullpen_exact", "home_recent_runs_for", "home_recent_runs_against", "away_recent_runs_for", "away_recent_runs_against",
        "lineup_confirmed", "probable_lineup", "lineup_source", "lineup_status", "lineup_fallback_used", "home_probable_players", "away_probable_players", "fixture_id", "starter_confirmed", "reasoning_engine_id", "signal_coverage", "signal_summary", "missing_signals", "counter_case_risk",
        "counter_case_summary", "v3_decision_status", "robust_positive_ratio", "robust_ev_min", "robust_ev_p10", "robust_ev_max",
        "robust_prob_min", "robust_prob_max", "robust_scenario_count", "v3_candidate", "v3_parlay_eligible", "odds_region", "model_weight",
        "pre_adaptive_model_win_prob", "ensemble_prob_cond", "calibrated_prob_cond", "adaptive_delta_pp", "calibration_delta_pp",
        "calibration_n", "calibration_active", "calibration_reliability", "ensemble_disagreement_pp", "ensemble_gate", "ensemble_summary", "adaptive_gate",
        "ensemble_independent_prob", "ensemble_independent_weight", "ensemble_market_prob", "ensemble_market_weight",
        "ensemble_recent_form_prob", "ensemble_recent_form_weight", "ensemble_context_model_prob", "ensemble_context_model_weight",
        "recent_form_used", "starter_recent_used", "starter_vs_opponent_used", "velocity_used", "bullpen_used", "split_used", "weather_used",
        "plate_discipline_used", "statcast_quality_used", "statcast_fallback_used", "batted_ball_regression_used", "pitch_mix_used", "starter_workload_used",
        "bullpen_exact_used", "lineup_platoon_exact_used", "pitch_matchup_used", "availability_news_used", "lineup_change_used",
        "market_movement_used", "roof_used", "umpire_used", "travel_rest_used", "bvp_used", "bullpen_manager_used",
        "home_xg_for", "home_xg_against", "away_xg_for", "away_xg_against", "xg_source", "xg_fallback_used", "xg_fallback_attempted", "xg_fallback_source", "xg_sources_tried", "xg_partial", "xg_samples_home", "xg_samples_away", "xg_checked_at", "xg_collection_status", "xg_fallback_error", "xg_canonical_source", "xg_application_mode", "xg_blend_weight", "xg_target_home", "xg_target_away",
        "home_big_chances", "away_big_chances", "home_rest_days", "away_rest_days",
        "home_missing_players", "away_missing_players", "home_formation", "away_formation",
        "home_recent_gf", "home_recent_ga", "away_recent_gf", "away_recent_ga", "home_elo", "away_elo",
    ]
    for _, r in frame.iterrows():
        d = {k: _safe(r.get(k)) for k in keep if k in frame.columns}
        d["sport_key"] = d.get("sport_key") or sport_key or ""
        d["sport_family"] = sport_family
        d["model_version"] = MODEL_VERSION
        d["recorded_at"] = now
        d["analysis_checked_at"] = now
        d["lineup_checked_at"] = now if d.get("lineup_confirmed") else None
        d["statcast_checked_at"] = now if d.get("statcast_quality_used") else None
        d["weather_checked_at"] = now if d.get("weather_used") else None
        d["news_checked_at"] = now if d.get("availability_news_used") else None
        if d.get("xg_source") and not d.get("xg_checked_at"):
            d["xg_checked_at"] = now
        rec=pd.to_datetime(now,utc=True,errors="coerce")
        kick=pd.to_datetime(d.get("commence_time"),utc=True,errors="coerce")
        pregame=not pd.isna(rec) and not pd.isna(kick) and rec < kick
        d["paper_locked"]=True
        d["paper_eligible"]=bool(pregame)
        d["paper_lock_at"]=now
        d["paper_minutes_before"]=(kick-rec).total_seconds()/60 if pregame else None
        d["lookahead_guard"]="PASS" if pregame else "POST_KICKOFF_EXCLUDED"
        raw = "|".join(str(d.get(k, "")) for k in (
            "event_id", "sport_key", "market", "selection", "point", "best_odds", "model_win_prob", "v3_decision_status",
            "stage", "data_quality", "lineup_confirmed", "counter_case_risk", "robust_positive_ratio", "missing_signals", "model_version"
        ))
        fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        d["fingerprint"] = fp
        if fp in fingerprints:
            continue
        fingerprints.add(fp)
        rows.append(d)
    _append_jsonl(path, rows, kind="prediction", id_field="fingerprint")
    # Every analysis also becomes a market observation.  Background workers add
    # more observations so the final pre-kickoff one becomes the closing line.
    record_market_frame(frame, sport_key=sport_key, source="analysis", observed_at=now)
    return len(rows)



def record_refresh_event(row: dict, path: str = DEFAULT_REFRESH_EVENTS):
    d={k:_safe(v) for k,v in dict(row).items()}
    d.setdefault("recorded_at",datetime.now(timezone.utc).isoformat())
    raw="|".join(str(d.get(k,"")) for k in ("event_id","sport_key","reason","recorded_at"))
    d["refresh_id"]=d.get("refresh_id") or hashlib.sha256(raw.encode("utf-8")).hexdigest()
    existing=_load(path,kind="refresh_event",id_field="refresh_id")
    if d["refresh_id"] in {x.get("refresh_id") for x in existing}:return 0
    _append_jsonl(path,[d],kind="refresh_event",id_field="refresh_id")
    return 1

def refresh_events(path: str = DEFAULT_REFRESH_EVENTS):
    return _load(path,kind="refresh_event",id_field="refresh_id")

def _score_map(score_event):
    vals = {}
    for x in score_event.get("scores") or []:
        try:
            vals[str(x.get("name"))] = float(x.get("score"))
        except (TypeError, ValueError):
            pass
    return vals


def _settlement_for(snapshot, home_score, away_score):
    market = snapshot.get("market")
    sel = str(snapshot.get("selection", ""))
    point = snapshot.get("point")
    home = str(snapshot.get("home_team", ""))
    away = str(snapshot.get("away_team", ""))
    if market == "h2h":
        if sel == home:
            return (1, 0, 0) if home_score > away_score else (0, 0, 1)
        if sel == away:
            return (1, 0, 0) if away_score > home_score else (0, 0, 1)
        if "draw" in sel.lower():
            return (1, 0, 0) if home_score == away_score else (0, 0, 1)
    if point is None:
        return None
    try:
        point = float(point)
    except (TypeError, ValueError):
        return None
    if market == "totals":
        return settle_total_over(home_score + away_score, point) if "over" in sel.lower() else settle_total_under(home_score + away_score, point)
    if market == "spreads":
        if sel == home:
            return settle_home_handicap(home_score, away_score, point)
        if sel == away:
            return settle_home_handicap(away_score, home_score, point)
    return None


def settle_from_scores(sport_key, scores, prediction_path=DEFAULT_PREDICTIONS, settled_path=DEFAULT_SETTLED, market_observation_path=DEFAULT_MARKET_OBSERVATIONS):
    predictions = [x for x in load_predictions(prediction_path) if x.get("sport_key") == sport_key]
    settled = load_settled(settled_path)
    done = {x.get("fingerprint") for x in settled}
    score_by_id = {str(x.get("id")): x for x in scores or [] if x.get("completed") is True}
    observations = load_market_observations(market_observation_path)
    out = []
    for s in predictions:
        if s.get("fingerprint") in done:
            continue
        event = score_by_id.get(str(s.get("event_id")))
        if not event:
            continue
        sm = _score_map(event)
        home = s.get("home_team")
        away = s.get("away_team")
        if home not in sm or away not in sm:
            continue
        result = _settlement_for(s, sm[home], sm[away])
        if result is None:
            continue
        w, p, l = result
        odds = float(s.get("best_odds") or 0)
        realized = w * (odds - 1) - l
        row = {
            **s,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "home_score": sm[home], "away_score": sm[away],
            "settle_win": w, "settle_push": p, "settle_loss": l,
            "realized_roi": realized,
            **_clv_fields(s, observations),
        }
        out.append(row)
        done.add(s.get("fingerprint"))
    _append_jsonl(settled_path, out, kind="settled", id_field="fingerprint")
    return len(out)


def auto_settle(api, sport_key, prediction_path=DEFAULT_PREDICTIONS, settled_path=DEFAULT_SETTLED):
    try:
        scores, _ = api.scores(sport_key, days_from=3)
    except Exception as e:
        return {"settled": 0, "error": str(e)}
    return {"settled": settle_from_scores(sport_key, scores, prediction_path, settled_path), "error": ""}


def pending_sport_keys(prediction_path=DEFAULT_PREDICTIONS, settled_path=DEFAULT_SETTLED):
    predictions = load_predictions(prediction_path)
    done = {x.get("fingerprint") for x in load_settled(settled_path)}
    return sorted({x.get("sport_key") for x in predictions if x.get("fingerprint") not in done and x.get("sport_key")})


def evaluation(path=DEFAULT_SETTLED):
    rows = load_settled(path)
    if not rows:
        return {"n": 0, "roi_n": 0, "clv_n": 0, "groups": []}

    roi_rows = []
    calibration_rows = []
    for r in rows:
        try:
            roi = float(r.get("realized_roi"))
        except (TypeError, ValueError):
            continue
        rr = {**r, "roi": roi}
        roi_rows.append(rr)
        try:
            p = float(r.get("model_win_prob")); push = float(r.get("push_prob") or 0); y = float(r.get("settle_win"))
            settle_push = float(r.get("settle_push") or 0); settle_loss = float(r.get("settle_loss") or 0)
        except (TypeError, ValueError):
            continue
        if settle_push > 1e-12 or y not in (0.0, 1.0) or settle_loss not in (0.0, 1.0):
            continue
        resolved = max(1e-9, 1 - push)
        q = max(1e-9, min(1 - 1e-9, p / resolved))
        calibration_rows.append({**rr, "q": q, "y": y})

    def roi_metric(sub):
        return sum(x["roi"] for x in sub) / len(sub) if sub else float("nan")

    def clv_metric(sub):
        vals = []
        for x in sub:
            try:
                v = float(x.get("clv_market_prob_pp"))
                if math.isfinite(v):
                    vals.append(v)
            except (TypeError, ValueError):
                pass
        return {"clv_n": len(vals), "avg_clv_prob_pp": (sum(vals) / len(vals) if vals else float("nan"))}

    def calib_metric(sub):
        if not sub:
            return {"n": 0, "brier": float("nan"), "log_loss": float("nan"), "hit_rate": float("nan")}
        n = len(sub)
        b = sum((x["q"] - x["y"]) ** 2 for x in sub) / n
        ll = -sum(x["y"] * math.log(x["q"]) + (1 - x["y"]) * math.log(1 - x["q"]) for x in sub) / n
        hit = sum(x["y"] for x in sub) / n
        return {"n": n, "brier": b, "log_loss": ll, "hit_rate": hit}

    group_keys = {(x.get("sport_family", ""), x.get("market", ""), x.get("v3_decision_status", "")) for x in roi_rows}
    groups = []
    for key in sorted(group_keys):
        rs = [x for x in roi_rows if (x.get("sport_family", ""), x.get("market", ""), x.get("v3_decision_status", "")) == key]
        cs = [x for x in calibration_rows if (x.get("sport_family", ""), x.get("market", ""), x.get("v3_decision_status", "")) == key]
        groups.append({
            "sport_family": key[0], "market": key[1], "decision": key[2],
            **calib_metric(cs), "roi_n": len(rs), "roi": roi_metric(rs), **clv_metric(rs),
        })
    overall = {**calib_metric(calibration_rows), "roi_n": len(roi_rows), "roi": roi_metric(roi_rows), **clv_metric(roi_rows)}
    return {"n": len(calibration_rows), "roi_n": len(roi_rows), "clv_n": overall["clv_n"], "overall": overall, "groups": groups}
