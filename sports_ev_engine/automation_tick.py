"""Budget-aware automatic CLV, settlement and cross-sport review cycle.

Designed for two modes:
1) Streamlit session: runs once per session/cooldown, no manual button required.
2) GitHub Actions / cron: true unattended mode after the app is closed.

CLV collection uses two checkpoints (about 120m and 20m pregame) rather than
constant polling, which is much friendlier to small Odds API plans.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os

import pandas as pd

from .providers.the_odds_api import TheOddsAPI
from .market import clean_odds, consensus
from .prediction_store import (
    load_predictions, load_settled, load_market_observations,
    record_market_frame, auto_settle,
)
from .universal_postgame import analyze_settled_all
from .self_learning import learning_summary


CLV_CHECKPOINTS_MIN = (120, 20)
CHECKPOINT_WINDOW_MIN = 35
DEFAULT_RESERVE = 50


def _num(v, default=float("nan")):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _latest_event_predictions():
    rows = list(load_predictions())
    rows.sort(key=lambda r: str(r.get("recorded_at") or ""))
    latest = {}
    for r in rows:
        sk = str(r.get("sport_key") or "")
        eid = str(r.get("event_id") or "")
        if sk and eid:
            latest[(sk, eid)] = r
    return list(latest.values())


def _observation_near(observations, sport_key, event_id, target, tolerance_min=55):
    for r in observations:
        if str(r.get("sport_key") or "") != str(sport_key):
            continue
        if str(r.get("event_id") or "") != str(event_id):
            continue
        ts = pd.to_datetime(r.get("observed_at"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        if abs((ts - target).total_seconds()) <= tolerance_min * 60:
            return True
    return False


def collect_due_clv(api, now=None, reserve_credits=DEFAULT_RESERVE):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    observations = load_market_observations()
    events = _latest_event_predictions()
    due_by_sport = {}
    for r in events:
        kick = pd.to_datetime(r.get("commence_time"), utc=True, errors="coerce")
        if pd.isna(kick) or kick <= now:
            continue
        sk = str(r.get("sport_key") or "")
        eid = str(r.get("event_id") or "")
        for cp in CLV_CHECKPOINTS_MIN:
            target = kick - pd.Timedelta(minutes=cp)
            age = (now - target).total_seconds() / 60.0
            if not (0 <= age <= CHECKPOINT_WINDOW_MIN):
                continue
            if _observation_near(observations, sk, eid, target):
                continue
            due_by_sport.setdefault(sk, []).append((r, cp))
            break

    out = {"sports_checked": 0, "observations": 0, "events_due": sum(len(v) for v in due_by_sport.values()),
           "remaining": None, "errors": []}
    for sk, due in due_by_sport.items():
        try:
            region = str(due[-1][0].get("odds_region") or "eu")
            events_json, headers = api.odds(sk, region, "h2h,spreads,totals")
            rem = headers.get("x-requests-remaining")
            if rem is not None:
                try:
                    rem = int(rem)
                    out["remaining"] = rem
                    if rem <= int(reserve_credits):
                        out["errors"].append(f"{sk}: reserve {reserve_credits} 보호로 CLV 추가 조회 중단")
                        break
                except Exception:
                    pass
            raw = clean_odds(api.flatten(events_json))
            market = consensus(raw, min_books=1) if not raw.empty else pd.DataFrame()
            if market.empty:
                continue
            due_ids = {str(x[0].get("event_id") or "") for x in due}
            market = market[market["event_id"].astype(str).isin(due_ids)].copy()
            if market.empty:
                continue
            market["sport_key"] = sk
            out["observations"] += record_market_frame(
                market, sport_key=sk, source="auto_clv_checkpoint",
                observed_at=now.isoformat(),
            )
            out["sports_checked"] += 1
        except Exception as exc:
            out["errors"].append(f"CLV {sk}: {type(exc).__name__}: {exc}")
    return out


def _expected_finish_buffer_hours(family):
    fam = str(family or "")
    if fam == "football_nfl":
        return 4.0
    if fam.startswith("baseball"):
        return 4.0
    if fam == "hockey_nhl":
        return 3.0
    if fam.startswith("soccer"):
        return 2.75
    return 4.0


def due_settlement_sports(now=None):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    settled = {str(r.get("fingerprint") or "") for r in load_settled()}
    due = set()
    for r in load_predictions():
        fp = str(r.get("fingerprint") or "")
        if not fp or fp in settled:
            continue
        kick = pd.to_datetime(r.get("commence_time"), utc=True, errors="coerce")
        if pd.isna(kick):
            continue
        buffer_h = _expected_finish_buffer_hours(r.get("sport_family"))
        if kick + pd.Timedelta(hours=buffer_h) <= now:
            sk = str(r.get("sport_key") or "")
            if sk:
                due.add(sk)
    return sorted(due)


def settle_due(api, now=None):
    result = {"sports": {}, "settled": 0, "errors": []}
    for sk in due_settlement_sports(now):
        info = auto_settle(api, sk)
        result["sports"][sk] = info
        result["settled"] += int(info.get("settled") or 0)
        if info.get("error"):
            result["errors"].append(f"{sk}: {info['error']}")
    return result


def run_auto_cycle(odds_key, *, now=None, reserve_credits=DEFAULT_RESERVE, collect_clv=True):
    if not odds_key:
        return {"status": "NO_ODDS_KEY", "clv": {}, "settlement": {}, "review": {}, "learning": []}
    api = TheOddsAPI(odds_key)
    clv = collect_due_clv(api, now=now, reserve_credits=reserve_credits) if collect_clv else {"disabled": True}
    settlement = settle_due(api, now=now)
    review = analyze_settled_all()
    learn = learning_summary()
    return {
        "status": "OK",
        "checked_at": pd.Timestamp(now or datetime.now(timezone.utc)).isoformat(),
        "clv": clv,
        "settlement": settlement,
        "review": review,
        "learning": learn,
    }


def maybe_streamlit_cycle(st, odds_key, cooldown_minutes=20):
    if not odds_key:
        return {"status": "NO_ODDS_KEY"}
    now = pd.Timestamp.now(tz="UTC")
    last = st.session_state.get("_autolearn_last_run")
    if last is not None:
        last_ts = pd.to_datetime(last, utc=True, errors="coerce")
        if not pd.isna(last_ts) and (now - last_ts).total_seconds() < cooldown_minutes * 60:
            return st.session_state.get("_autolearn_last_result") or {"status": "COOLDOWN"}
    try:
        result = run_auto_cycle(
            odds_key, reserve_credits=int(os.getenv("AUTO_LEARN_RESERVE_CREDITS", str(DEFAULT_RESERVE)))
        )
    except Exception as exc:
        result = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    st.session_state["_autolearn_last_run"] = now.isoformat()
    st.session_state["_autolearn_last_result"] = result
    return result
