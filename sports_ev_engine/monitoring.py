
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.providers.api_football import APIFootball
from sports_ev_engine.market import clean_odds, consensus
from sports_ev_engine.competition_form import build_competition_pool
from sports_ev_engine.auto_soccer import analyze_event


# 20K-plan optimized schedule requested by the user.
# h2h checks are cheap/frequent; full spreads+totals snapshots are sparse.
FULL_SNAPSHOT_THRESHOLDS_MIN = [360, 120, 60, 30, 15, 5]


def scheduled_interval_minutes(minutes_to_kickoff: float) -> int:
    """Main h2h polling cadence."""
    if minutes_to_kickoff > 24 * 60:
        return 120
    if minutes_to_kickoff > 6 * 60:
        return 60
    if minutes_to_kickoff > 2 * 60:
        return 30
    if minutes_to_kickoff > 30:
        return 15
    if minutes_to_kickoff > 0:
        return 5
    return 999999


def reanalysis_threshold_pp(minutes_to_kickoff: float) -> float:
    """No-vig probability movement needed to trigger a reanalysis."""
    if minutes_to_kickoff <= 30:
        return 1.0
    if minutes_to_kickoff <= 120:
        return 1.5
    return 2.0


def lineup_poll_interval_minutes(minutes_to_kickoff: float) -> int | None:
    # Only watch lineups in the last 90 minutes.
    if 0 < minutes_to_kickoff <= 90:
        return 15
    return None


def should_take_full_snapshot(minutes_to_kickoff: float, fired: list[int], tolerance_min: int = 4):
    for threshold in FULL_SNAPSHOT_THRESHOLDS_MIN:
        if threshold in fired:
            continue
        if 0 < minutes_to_kickoff <= threshold and minutes_to_kickoff >= threshold - tolerance_min:
            return threshold
    return None


def parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc)


def minutes_until(iso: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return (parse_ts(iso) - now).total_seconds() / 60.0


def market_key(row: dict[str, Any]) -> str:
    market = str(row.get("market"))
    point = row.get("point")
    if market == "h2h":
        return f'{row.get("event_id")}|h2h|{row.get("selection")}'
    return f'{row.get("event_id")}|{market}|{point}|{row.get("selection")}'


def event_key(row: dict[str, Any]) -> str:
    return str(row.get("event_id"))


def state_default():
    return {
        "version": 1,
        "events": {},
        "last_run": None,
        "credits_remaining": None,
        "estimated_credits_used": 0,
        "alerts_sent": 0,
    }


class JSONStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.path.exists():
            return state_default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            base = state_default()
            base.update(data)
            return base
        except Exception:
            return state_default()

    def save(self, data):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


@dataclass
class MonitorConfig:
    sport_key: str = "soccer_uefa_nations_league"
    competition_name: str = "UEFA Nations League"
    region: str = "eu"
    min_books: int = 3
    recent_n: int = 6
    state_path: str = "data/monitor_state.json"
    reserve_credits: int = 2000
    monthly_budget: int = 20000
    require_two_observations: bool = True
    strong_move_pp: float = 4.0
    line_move_trigger: float = 0.25


def hash_lineup(lineups: Any) -> str:
    raw = json.dumps(lineups, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_best_consensus(market_df: pd.DataFrame, event_id: str):
    g = market_df[market_df["event_id"] == event_id].copy()
    return g


def _prob_movement(prev: dict, current_row: dict) -> float | None:
    old = prev.get("consensus_prob")
    new = current_row.get("consensus_prob")
    if old is None or new is None:
        return None
    return (float(new) - float(old)) * 100.0


def _line_movement(prev: dict, current_row: dict) -> float | None:
    old = prev.get("point")
    new = current_row.get("point")
    if old is None or new is None:
        return None
    try:
        return float(new) - float(old)
    except Exception:
        return None


def format_alert(title: str, body_lines: list[str]) -> str:
    return title + "\n" + "\n".join(body_lines)


class MonitorEngine:
    def __init__(self, odds_key: str, football_key: str | None, config: MonitorConfig, notifier=None):
        self.odds = TheOddsAPI(odds_key)
        self.football = APIFootball(football_key) if football_key else None
        self.config = config
        self.store = JSONStateStore(config.state_path)
        self.notifier = notifier

    def _notify(self, text: str):
        if self.notifier:
            self.notifier(text)

    def _budget_ok(self, state):
        remaining = state.get("credits_remaining")
        if remaining is None:
            return True
        return int(remaining) > int(self.config.reserve_credits)

    def _full_snapshot_due_for_any_event(self, h2h_market: pd.DataFrame, state, now):
        for event_id, g in h2h_market.groupby("event_id"):
            kickoff = g.iloc[0]["commence_time"]
            mins = minutes_until(kickoff, now)
            e = state["events"].setdefault(str(event_id), {})
            fired = e.setdefault("full_snapshots_fired", [])
            threshold = should_take_full_snapshot(mins, fired)
            if threshold is not None:
                return True, str(event_id), threshold
        return False, None, None

    def _lineup_due(self, event_id, kickoff, event_state, now):
        mins = minutes_until(kickoff, now)
        interval = lineup_poll_interval_minutes(mins)
        if interval is None:
            return False
        last = event_state.get("last_lineup_check")
        if not last:
            return True
        elapsed = (now - parse_ts(last)).total_seconds() / 60.0
        return elapsed >= interval

    def _resolve_fixture_id(self, home: str, away: str, kickoff_iso: str):
        if not self.football:
            return None
        kickoff = parse_ts(kickoff_iso)
        # Search target date +/- 1 day to absorb timezone/provider differences.
        for delta in (-1, 0, 1):
            d = (kickoff + pd.Timedelta(days=delta)).date().isoformat()
            try:
                rows = self.football.fixtures_by_date(d)
            except Exception:
                continue
            home_n = home.lower().strip()
            away_n = away.lower().strip()
            for fx in rows:
                h = str(fx.get("teams",{}).get("home",{}).get("name","")).lower().strip()
                a = str(fx.get("teams",{}).get("away",{}).get("name","")).lower().strip()
                if (home_n in h or h in home_n) and (away_n in a or a in away_n):
                    return fx.get("fixture",{}).get("id")
        return None

    def _reanalyze_event(self, event_market: pd.DataFrame, pool):
        analyzed, meta = analyze_event(event_market, pool, recent_n=self.config.recent_n)
        if analyzed.empty:
            return None, meta
        analyzed = analyzed.sort_values(["conservative_ev_roi","edge_pp"], ascending=False)
        return analyzed, meta

    def tick(self, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        state = self.store.load()
        result = {
            "checked_at": now.isoformat(),
            "alerts": [],
            "reanalyzed_events": [],
            "lineup_changes": [],
            "price_triggers": [],
            "full_snapshot": False,
            "full_snapshot_threshold": None,
            "credits_remaining": state.get("credits_remaining"),
        }

        if not self._budget_ok(state):
            msg = format_alert(
                "⛔ Odds API 모니터링 일시중지",
                [
                    f"남은 credits: {state.get('credits_remaining')}",
                    f"비상 reserve: {self.config.reserve_credits}",
                    "수동 확인용 크레딧을 남기기 위해 자동 조회를 중지했습니다.",
                ],
            )
            self._notify(msg)
            result["alerts"].append(msg)
            return result

        # Frequent, cheap h2h check (1 credit with one region).
        events, headers = self.odds.odds(
            self.config.sport_key,
            self.config.region,
            "h2h",
        )
        raw_h2h = clean_odds(self.odds.flatten(events))
        market_h2h = consensus(raw_h2h, min_books=self.config.min_books)

        remain = headers.get("x-requests-remaining")
        if remain is not None:
            try:
                state["credits_remaining"] = int(remain)
            except Exception:
                state["credits_remaining"] = remain
        state["estimated_credits_used"] = int(state.get("estimated_credits_used",0)) + 1
        result["credits_remaining"] = state.get("credits_remaining")

        # Decide whether a 3-market full snapshot is due.
        full_due, due_event_id, threshold = self._full_snapshot_due_for_any_event(market_h2h, state, now)
        if full_due and self._budget_ok(state):
            events_full, headers_full = self.odds.odds(
                self.config.sport_key,
                self.config.region,
                "h2h,spreads,totals",
            )
            raw = clean_odds(self.odds.flatten(events_full))
            market = consensus(raw, min_books=self.config.min_books)
            state["estimated_credits_used"] += 3
            remain2 = headers_full.get("x-requests-remaining")
            if remain2 is not None:
                try:
                    state["credits_remaining"] = int(remain2)
                except Exception:
                    state["credits_remaining"] = remain2
            result["full_snapshot"] = True
            result["full_snapshot_threshold"] = threshold

            # Mark threshold fired for every event already inside that threshold.
            for event_id, g in market.groupby("event_id"):
                mins = minutes_until(g.iloc[0]["commence_time"], now)
                es = state["events"].setdefault(str(event_id), {})
                fired = es.setdefault("full_snapshots_fired", [])
                for t in FULL_SNAPSHOT_THRESHOLDS_MIN:
                    if 0 < mins <= t and t not in fired:
                        fired.append(t)
        else:
            market = market_h2h
            raw = raw_h2h

        # Detect price and line movement.
        triggered_events = set()
        current_markets = {}
        for _, r in market.iterrows():
            d = r.to_dict()
            # Preserve line point by reading from market_id when possible.
            mk = str(d.get("market_id",""))
            point = None
            if "|" in mk and d.get("market") != "h2h":
                try:
                    point = float(mk.split("|",1)[1])
                except Exception:
                    point = None
            d["point"] = point
            key = market_key(d)
            current_markets[key] = d

            eid = str(d["event_id"])
            es = state["events"].setdefault(eid, {})
            prev_markets = es.setdefault("markets", {})
            prev = prev_markets.get(key, {})

            mins = minutes_until(d["commence_time"], now)
            threshold_pp = reanalysis_threshold_pp(mins)

            prob_move = _prob_movement(prev, d)
            line_move = _line_movement(prev, d)
            is_price_trigger = prob_move is not None and abs(prob_move) >= threshold_pp
            is_strong = prob_move is not None and abs(prob_move) >= self.config.strong_move_pp
            is_line_trigger = line_move is not None and abs(line_move) >= self.config.line_move_trigger - 1e-9

            streak = int(prev.get("move_streak",0))
            if is_price_trigger:
                # same direction twice for ordinary moves
                old_direction = prev.get("move_direction")
                direction = 1 if prob_move > 0 else -1
                streak = streak + 1 if old_direction == direction else 1
            else:
                direction = None
                streak = 0

            fire_price = is_strong or (is_price_trigger and (not self.config.require_two_observations or streak >= 2))
            if fire_price or is_line_trigger:
                triggered_events.add(eid)
                result["price_triggers"].append({
                    "event_id": eid,
                    "selection": d.get("selection"),
                    "market": d.get("market"),
                    "prob_move_pp": prob_move,
                    "line_move": line_move,
                    "minutes_to_kickoff": mins,
                })

            prev_markets[key] = {
                "consensus_prob": d.get("consensus_prob"),
                "best_odds": d.get("best_odds"),
                "point": point,
                "observed_at": now.isoformat(),
                "move_streak": streak,
                "move_direction": direction,
            }
            es["home_team"] = d.get("home_team")
            es["away_team"] = d.get("away_team")
            es["commence_time"] = d.get("commence_time")

        # Lineup watch (does not use The Odds API credits).
        if self.football:
            for eid, es in list(state["events"].items()):
                kickoff = es.get("commence_time")
                if not kickoff:
                    continue
                if not self._lineup_due(eid, kickoff, es, now):
                    continue
                fixture_id = es.get("fixture_id")
                if not fixture_id:
                    fixture_id = self._resolve_fixture_id(
                        es.get("home_team",""),
                        es.get("away_team",""),
                        kickoff,
                    )
                    if fixture_id:
                        es["fixture_id"] = fixture_id
                if not fixture_id:
                    es["last_lineup_check"] = now.isoformat()
                    continue
                try:
                    lineups = self.football.lineups(int(fixture_id))
                    new_hash = hash_lineup(lineups)
                    old_hash = es.get("lineup_hash")
                    es["last_lineup_check"] = now.isoformat()
                    if lineups:
                        es["lineup_hash"] = new_hash
                    if old_hash and lineups and new_hash != old_hash:
                        triggered_events.add(eid)
                        result["lineup_changes"].append(eid)
                except Exception:
                    es["last_lineup_check"] = now.isoformat()

        # Reanalysis is expensive only when a trigger actually fires.
        pool = None
        if triggered_events and self.football:
            try:
                pool = build_competition_pool(
                    self.football,
                    self.config.competition_name,
                    datetime.now(timezone.utc).year,
                )
            except Exception:
                pool = None

        for eid in triggered_events:
            event_market = market[market["event_id"].astype(str) == str(eid)].copy()
            if event_market.empty:
                continue

            if pool is not None:
                analyzed, meta = self._reanalyze_event(event_market, pool)
            else:
                analyzed, meta = None, {"status":"no_model_data"}

            es = state["events"].setdefault(eid, {})
            old_grade = es.get("best_grade")
            old_pick = es.get("best_pick")
            old_edge = es.get("best_edge_pp")

            if analyzed is not None and not analyzed.empty:
                best = analyzed.iloc[0]
                new_grade = str(best.get("grade"))
                new_pick = str(best.get("display_pick"))
                new_edge = float(best.get("edge_pp"))
                new_ev = float(best.get("conservative_ev_roi"))
                es["best_grade"] = new_grade
                es["best_pick"] = new_pick
                es["best_edge_pp"] = new_edge
                es["best_conservative_ev"] = new_ev
                es["last_reanalysis"] = now.isoformat()
                result["reanalyzed_events"].append(eid)

                grade_changed = old_grade is not None and old_grade != new_grade
                new_value = new_grade in {"A","B"} and old_grade not in {"A","B"}
                degraded = old_grade in {"A","B","C"} and new_grade in {"PASS","REVIEW"}
                lineup_changed = eid in result["lineup_changes"]

                if grade_changed or new_value or degraded or lineup_changed:
                    title = "🚨 자동 재분석"
                    if lineup_changed:
                        title = "👥 라인업 변경 → 자동 재분석"
                    lines = [
                        f"{es.get('home_team')} - {es.get('away_team')}",
                        f"픽: {new_pick}",
                        f"등급: {old_grade or '-'} → {new_grade}",
                        f"Edge: {old_edge if old_edge is not None else '-'} → {new_edge:.1f}%p",
                        f"불확실성 반영 EV: {new_ev*100:+.1f}%",
                    ]
                    text = format_alert(title, lines)
                    self._notify(text)
                    result["alerts"].append(text)
                    state["alerts_sent"] = int(state.get("alerts_sent",0)) + 1

            # Price-only alert if trigger occurred but model reanalysis unavailable.
            if analyzed is None:
                triggers = [x for x in result["price_triggers"] if x["event_id"] == eid]
                if triggers:
                    t = triggers[0]
                    text = format_alert(
                        "⚠️ 의미 있는 배당 이동",
                        [
                            f"{es.get('home_team')} - {es.get('away_team')}",
                            f"{t.get('selection')} / {t.get('market')}",
                            f"무마진 확률 변화: {t.get('prob_move_pp'):+.1f}%p" if t.get("prob_move_pp") is not None else "확률 변화: -",
                            f"라인 변화: {t.get('line_move'):+.2f}" if t.get("line_move") is not None else "라인 변화: -",
                        ],
                    )
                    self._notify(text)
                    result["alerts"].append(text)

        state["last_run"] = now.isoformat()
        self.store.save(state)
        return result


def recommended_sleep_seconds(state: dict, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    intervals = []
    for es in state.get("events",{}).values():
        kickoff = es.get("commence_time")
        if not kickoff:
            continue
        mins = minutes_until(kickoff, now)
        if mins > 0:
            intervals.append(scheduled_interval_minutes(mins))
    if not intervals:
        return 3600
    return max(60, min(intervals) * 60)
