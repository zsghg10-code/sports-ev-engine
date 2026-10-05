"""NHL/NFL automatic analysis pipeline for Sports EV Engine.

Design goal: reproduce the same auditable flow used by the baseball and
international-football tabs without inventing unavailable data:
independent model -> measured context -> counter-case audit -> market prior ->
adaptive calibration -> stress scenarios -> EV/ROBUST decision -> immutable
pregame snapshot/settlement in the caller.

Public ESPN scoreboards/summaries are used only for factual recent-game and
box-score context. Missing injury/QB/goalie/special-team fields remain MISSING
and increase uncertainty; they are never back-filled from the betting market.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import NormalDist
from typing import Any
import math
import re
import unicodedata

import pandas as pd
import requests

from .adaptive_model import apply_adaptive_layer
from .core.ev import analyze_bet
from .reasoning_engine import (
    SignalLedger,
    build_counter_cases,
    decision_fields,
    final_probability_assessment,
)

PRO_SPORTS_BUILD = "3.5.0-nhl-nfl"

SPORTS = {
    "americanfootball_nfl": {
        "family": "football_nfl",
        "label": "NFL",
        "espn_path": "football/nfl",
        "lookback_days": 150,
        "recent_n": 6,
        "league_mean": 22.5,
        "margin_sd": 13.4,
        "total_sd": 14.2,
    },
    "icehockey_nhl": {
        "family": "hockey_nhl",
        "label": "NHL",
        "espn_path": "hockey/nhl",
        "lookback_days": 45,
        "recent_n": 8,
        "league_mean": 3.05,
    },
}


def _num(v: Any, default=float("nan")) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _norm_name(v: Any) -> str:
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    # Common market/provider suffixes that do not aid matching.
    for token in ("fc", "cf"):
        s = re.sub(rf"\b{token}\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _names_match(a: Any, b: Any) -> bool:
    aa, bb = _norm_name(a), _norm_name(b)
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    aset, bset = set(aa.split()), set(bb.split())
    # Full nickname/city names from ESPN vs market feeds usually share >=2 tokens.
    return len(aset & bset) >= 2 or (len(aset) == 1 and next(iter(aset)) in bset) or (len(bset) == 1 and next(iter(bset)) in aset)


@dataclass
class TeamRecent:
    games: int = 0
    wins: int = 0
    points_for: float = float("nan")
    points_against: float = float("nan")
    margin: float = float("nan")
    rest_days: float = float("nan")
    shots_for: float = float("nan")
    shots_against: float = float("nan")
    turnovers: float = float("nan")
    total_yards: float = float("nan")
    sacks_allowed: float = float("nan")
    third_down_pct: float = float("nan")
    pp_pct: float = float("nan")
    faceoff_pct: float = float("nan")
    source: str = "ESPN public scoreboard/summary"


class ESPNProContext:
    """Fail-soft public context collector for NFL/NHL.

    It intentionally does not infer unavailable injuries, starting goalies or
    starting quarterbacks from odds. Those fields stay missing and the model
    pays an uncertainty penalty.
    """

    BASE = "https://site.api.espn.com/apis/site/v2/sports"

    def __init__(self, sport_key: str, session: requests.Session | None = None, timeout: int = 15):
        if sport_key not in SPORTS:
            raise ValueError(f"unsupported pro sport: {sport_key}")
        self.sport_key = sport_key
        self.cfg = SPORTS[sport_key]
        self.session = session or requests.Session()
        self.timeout = timeout
        self._score_cache: dict[str, dict] = {}
        self._summary_cache: dict[str, dict] = {}

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    def scoreboard(self, start: datetime, end: datetime) -> dict:
        key = f"{start:%Y%m%d}-{end:%Y%m%d}"
        if key not in self._score_cache:
            path = self.cfg["espn_path"]
            url = f"{self.BASE}/{path}/scoreboard"
            self._score_cache[key] = self._get(url, {"dates": key, "limit": 500})
        return self._score_cache[key]

    def summary(self, event_id: str) -> dict:
        eid = str(event_id or "")
        if not eid:
            return {}
        if eid not in self._summary_cache:
            path = self.cfg["espn_path"]
            url = f"{self.BASE}/{path}/summary"
            try:
                self._summary_cache[eid] = self._get(url, {"event": eid})
            except Exception:
                self._summary_cache[eid] = {}
        return self._summary_cache[eid]

    @staticmethod
    def _event_competitors(event: dict) -> list[dict]:
        comps = event.get("competitions") or []
        if not comps:
            return []
        return comps[0].get("competitors") or []

    @staticmethod
    def _team_name(c: dict) -> str:
        t = c.get("team") or {}
        return str(t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "")

    def _find_competitor(self, comps: list[dict], team_name: str) -> dict | None:
        for c in comps:
            if _names_match(self._team_name(c), team_name):
                return c
        return None

    def recent(self, team_name: str, kickoff: Any, n: int | None = None) -> tuple[TeamRecent, list[str]]:
        cfg = self.cfg
        n = int(n or cfg["recent_n"])
        k = pd.to_datetime(kickoff, utc=True, errors="coerce")
        if pd.isna(k):
            return TeamRecent(), ["invalid kickoff"]
        end = k.to_pydatetime() - timedelta(hours=1)
        start = end - timedelta(days=int(cfg["lookback_days"]))
        try:
            board = self.scoreboard(start, end)
        except Exception as e:
            return TeamRecent(), [f"scoreboard unavailable: {type(e).__name__}"]

        matches = []
        for event in board.get("events") or []:
            status = ((event.get("status") or {}).get("type") or {})
            if not bool(status.get("completed")):
                continue
            comps = self._event_competitors(event)
            me = self._find_competitor(comps, team_name)
            if not me:
                continue
            opp = next((x for x in comps if x is not me), None)
            if not opp:
                continue
            try:
                pf = float(me.get("score")); pa = float(opp.get("score"))
            except (TypeError, ValueError):
                continue
            ts = pd.to_datetime(event.get("date"), utc=True, errors="coerce")
            if pd.isna(ts) or ts >= k:
                continue
            matches.append((ts, event, pf, pa, str(event.get("id") or "")))
        matches.sort(key=lambda x: x[0], reverse=True)
        matches = matches[:n]
        if not matches:
            return TeamRecent(), ["no completed recent games"]

        pf = sum(x[2] for x in matches) / len(matches)
        pa = sum(x[3] for x in matches) / len(matches)
        wins = sum(1 for x in matches if x[2] > x[3])
        rest = max(0.0, (k - matches[0][0]).total_seconds() / 86400.0)

        stat_rows: dict[str, list[float]] = {}
        for _, _, _, _, eid in matches[: min(len(matches), 5)]:
            s = self.summary(eid)
            box = s.get("boxscore") or {}
            teams = box.get("teams") or []
            for trow in teams:
                t = (trow.get("team") or {}).get("displayName") or (trow.get("team") or {}).get("name")
                if not _names_match(t, team_name):
                    continue
                for stat in trow.get("statistics") or []:
                    name = str(stat.get("name") or stat.get("label") or "").strip().lower()
                    raw = stat.get("displayValue", stat.get("value"))
                    val = self._parse_stat(raw)
                    if math.isfinite(val):
                        stat_rows.setdefault(name, []).append(val)

        def avg(*names: str) -> float:
            vals = []
            for name in names:
                vals.extend(stat_rows.get(name.lower(), []))
            return sum(vals) / len(vals) if vals else float("nan")

        return TeamRecent(
            games=len(matches), wins=wins,
            points_for=pf, points_against=pa, margin=pf-pa, rest_days=rest,
            shots_for=avg("shots", "shotsTotal"),
            shots_against=float("nan"),
            turnovers=avg("turnovers", "totalTurnovers"),
            total_yards=avg("totalYards", "netTotalYards"),
            sacks_allowed=avg("sacksYardsLost", "sacks"),
            third_down_pct=avg("thirdDownEff", "thirdDownPct"),
            pp_pct=avg("powerPlayPct", "powerPlayPercentage"),
            faceoff_pct=avg("faceoffPercent", "faceoffPct"),
        ), []

    @staticmethod
    def _parse_stat(v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v or "").strip()
        if not s:
            return float("nan")
        if "%" in s:
            try:
                return float(s.replace("%", ""))
            except ValueError:
                return float("nan")
        if "-" in s and all(part.strip().replace(".", "", 1).isdigit() for part in s.split("-")[:2]):
            # third-down style 5-12 -> conversion percentage.
            try:
                a, b = (float(x) for x in s.split("-")[:2])
                return 100.0 * a / b if b else float("nan")
            except Exception:
                return float("nan")
        try:
            return float(re.sub(r"[^0-9.+-]", "", s))
        except ValueError:
            return float("nan")

    def event_summary(self, home: str, away: str, kickoff: Any) -> tuple[dict, list[str]]:
        k = pd.to_datetime(kickoff, utc=True, errors="coerce")
        if pd.isna(k):
            return {}, ["invalid kickoff"]
        start = k.to_pydatetime() - timedelta(days=1)
        end = k.to_pydatetime() + timedelta(days=1)
        try:
            board = self.scoreboard(start, end)
        except Exception as e:
            return {}, [f"event scoreboard unavailable: {type(e).__name__}"]
        for event in board.get("events") or []:
            comps = self._event_competitors(event)
            if self._find_competitor(comps, home) and self._find_competitor(comps, away):
                return self.summary(str(event.get("id") or "")), []
        return {}, ["matching ESPN event not found"]

    def availability(self, home: str, away: str, kickoff: Any) -> dict:
        summary, errs = self.event_summary(home, away, kickoff)
        injuries = summary.get("injuries") if isinstance(summary, dict) else None
        rows = []
        if isinstance(injuries, list):
            for group in injuries:
                team = ((group.get("team") or {}).get("displayName") or (group.get("team") or {}).get("name") or "")
                for item in group.get("injuries") or group.get("items") or []:
                    athlete = item.get("athlete") or {}
                    status = str(item.get("status") or item.get("type") or "").lower()
                    pos = str(((athlete.get("position") or {}).get("abbreviation") or "")).upper()
                    rows.append({"team": team, "status": status, "position": pos, "name": athlete.get("displayName")})
        def weight(team: str) -> tuple[float, int]:
            score = 0.0; count = 0
            for x in rows:
                if not _names_match(x.get("team"), team):
                    continue
                st = str(x.get("status") or "").lower()
                if not any(k in st for k in ("out", "inactive", "doubtful", "injured", "ir")):
                    continue
                count += 1
                pos = x.get("position")
                if self.sport_key == "americanfootball_nfl":
                    score += 2.8 if pos == "QB" else 1.3 if pos in {"OT", "T", "LT", "RT", "WR", "CB", "EDGE", "DE"} else 0.7
                else:
                    score += 1.8 if pos in {"G"} else 1.0 if pos in {"C", "D", "RW", "LW"} else 0.6
            return min(score, 8.0), count
        hw, hn = weight(home); aw, an = weight(away)
        return {
            "available": bool(rows), "home_weight": hw, "away_weight": aw,
            "home_count": hn, "away_count": an, "errors": errs,
            "source": "ESPN event injury report" if rows else "",
        }


def _poisson_pmf(mu: float, k: int) -> float:
    return math.exp(-mu) * (mu ** k) / math.factorial(k)


def _nhl_matrix(home_mu: float, away_mu: float, cap: int = 11) -> list[tuple[int, int, float]]:
    rows = []
    for h in range(cap + 1):
        ph = _poisson_pmf(home_mu, h)
        for a in range(cap + 1):
            rows.append((h, a, ph * _poisson_pmf(away_mu, a)))
    total = sum(x[2] for x in rows) or 1.0
    return [(h, a, p / total) for h, a, p in rows]


def _line_prob_from_matrix(matrix, *, market: str, selection: str, point: float | None, home: str, away: str, ot_share_home: float = .5):
    sel = _norm_name(selection); hname = _norm_name(home); aname = _norm_name(away)
    win = push = 0.0
    if market == "h2h":
        reg_h = sum(p for h, a, p in matrix if h > a)
        reg_a = sum(p for h, a, p in matrix if a > h)
        tie = max(0.0, 1.0 - reg_h - reg_a)
        hp = reg_h + tie * ot_share_home
        ap = reg_a + tie * (1.0 - ot_share_home)
        if sel == hname:
            return hp, 0.0
        if sel == aname:
            return ap, 0.0
        return float("nan"), 0.0
    if point is None or not math.isfinite(float(point)):
        return float("nan"), 0.0
    pnt = float(point)
    for hs, aw, prob in matrix:
        if market == "totals":
            val = hs + aw
            target = val - pnt
            is_over = "over" in sel
            result = target if is_over else -target
        elif market == "spreads":
            if sel == hname:
                result = hs - aw + pnt
            elif sel == aname:
                result = aw - hs + pnt
            else:
                continue
        else:
            continue
        if result > 1e-9: win += prob
        elif abs(result) <= 1e-9: push += prob
    return win, push


def _normal_discrete_prob(mean: float, sd: float, threshold: float, side: str) -> tuple[float, float]:
    nd = NormalDist(mu=mean, sigma=max(sd, 1e-6))
    # Scores are discrete. Approximate exact integer push by a one-point continuity bucket.
    if abs(threshold - round(threshold)) < 1e-9:
        t = float(round(threshold))
        push = max(0.0, nd.cdf(t + .5) - nd.cdf(t - .5))
        if side == "over":
            win = 1.0 - nd.cdf(t + .5)
        else:
            win = nd.cdf(t - .5)
    else:
        push = 0.0
        if side == "over":
            win = 1.0 - nd.cdf(threshold)
        else:
            win = nd.cdf(threshold)
    return _clamp(win, 0, 1), _clamp(push, 0, 1)


def _shrink(value: float, mean: float, n: int, prior_games: float = 5.0) -> float:
    if not math.isfinite(value) or n <= 0:
        return mean
    w = n / (n + prior_games)
    return w * value + (1 - w) * mean


def _build_ledger(sport_key: str, home: TeamRecent, away: TeamRecent, availability: dict) -> SignalLedger:
    ledger = SignalLedger()
    min_games = min(home.games, away.games)
    ledger.add("recent_form", min_games >= 3,
               "home" if _num(home.margin, 0) > _num(away.margin, 0) else "away",
               _num(home.margin, 0) - _num(away.margin, 0), .72,
               "ESPN completed games", f"samples H/A {home.games}/{away.games}")
    ledger.add("rest_schedule", math.isfinite(home.rest_days) and math.isfinite(away.rest_days),
               "home" if _num(home.rest_days, 0) > _num(away.rest_days, 0) else "away",
               _clamp((_num(home.rest_days, 0) - _num(away.rest_days, 0)) * .4, -2.0, 2.0), .65,
               "ESPN schedule", f"rest H/A {home.rest_days:.1f}/{away.rest_days:.1f}" if math.isfinite(home.rest_days) and math.isfinite(away.rest_days) else "")
    ledger.add("injuries_availability", bool(availability.get("available")),
               "home" if availability.get("away_weight", 0) > availability.get("home_weight", 0) else "away",
               float(availability.get("away_weight", 0)) - float(availability.get("home_weight", 0)), .65,
               availability.get("source", ""), f"out/doubtful H/A {availability.get('home_count',0)}/{availability.get('away_count',0)}")
    if sport_key == "americanfootball_nfl":
        ledger.add("offensive_yards", math.isfinite(home.total_yards) and math.isfinite(away.total_yards),
                   "home" if _num(home.total_yards, 0) > _num(away.total_yards, 0) else "away", 0, .55, "ESPN boxscore")
        ledger.add("turnovers", math.isfinite(home.turnovers) and math.isfinite(away.turnovers),
                   "home" if _num(home.turnovers, 99) < _num(away.turnovers, 99) else "away", 0, .55, "ESPN boxscore")
        ledger.add("qb_status", False, note="public feed did not provide a verified current starter; no QB invented")
        ledger.add("ol_pass_protection", math.isfinite(home.sacks_allowed) and math.isfinite(away.sacks_allowed), source="ESPN boxscore")
    else:
        ledger.add("shot_share", math.isfinite(home.shots_for) and math.isfinite(away.shots_for),
                   "home" if _num(home.shots_for, 0) > _num(away.shots_for, 0) else "away", 0, .55, "ESPN boxscore")
        ledger.add("special_teams", math.isfinite(home.pp_pct) and math.isfinite(away.pp_pct),
                   "home" if _num(home.pp_pct, 0) > _num(away.pp_pct, 0) else "away", 0, .50, "ESPN boxscore")
        ledger.add("starting_goalie", False, note="verified starting goalie unavailable in current public feed; no goalie invented")
    return ledger


def _independent_event(sport_key: str, home_name: str, away_name: str, home: TeamRecent, away: TeamRecent, availability: dict):
    cfg = SPORTS[sport_key]
    if sport_key == "americanfootball_nfl":
        mean = cfg["league_mean"]
        h_for = _shrink(home.points_for, mean, home.games, 4.5)
        h_against = _shrink(home.points_against, mean, home.games, 4.5)
        a_for = _shrink(away.points_for, mean, away.games, 4.5)
        a_against = _shrink(away.points_against, mean, away.games, 4.5)
        hp = .54 * h_for + .46 * a_against + .9
        ap = .54 * a_for + .46 * h_against - .3
        # Injury report weights are intentionally small and capped.
        hp += _clamp((availability.get("away_weight", 0) - availability.get("home_weight", 0)) * .22, -1.8, 1.8)
        ap -= _clamp((availability.get("away_weight", 0) - availability.get("home_weight", 0)) * .22, -1.8, 1.8)
        if math.isfinite(home.rest_days) and math.isfinite(away.rest_days):
            rest = _clamp((home.rest_days - away.rest_days) * .18, -1.0, 1.0)
            hp += rest; ap -= rest
        hp = _clamp(hp, 10.0, 38.0); ap = _clamp(ap, 10.0, 38.0)
        return {"home_points": hp, "away_points": ap, "margin": hp-ap, "total": hp+ap}

    mean = cfg["league_mean"]
    h_for = _shrink(home.points_for, mean, home.games, 5.0)
    h_against = _shrink(home.points_against, mean, home.games, 5.0)
    a_for = _shrink(away.points_for, mean, away.games, 5.0)
    a_against = _shrink(away.points_against, mean, away.games, 5.0)
    hm = .55*h_for + .45*a_against + .08
    am = .55*a_for + .45*h_against - .03
    inj = _clamp((availability.get("away_weight", 0)-availability.get("home_weight",0))*.018, -.12, .12)
    hm += inj; am -= inj
    if math.isfinite(home.rest_days) and math.isfinite(away.rest_days):
        rest = _clamp((home.rest_days-away.rest_days)*.025, -.08, .08)
        hm += rest; am -= rest
    return {"home_goals": _clamp(hm, 1.7, 4.6), "away_goals": _clamp(am, 1.7, 4.6)}


def _raw_probability(sport_key: str, model: dict, row: pd.Series, home: str, away: str, stress_home: float = 1.0, stress_away: float = 1.0):
    market = str(row.get("market") or "")
    selection = str(row.get("selection") or "")
    point = _num(row.get("point"))
    point = point if math.isfinite(point) else None
    sel = _norm_name(selection)
    if sport_key == "americanfootball_nfl":
        hp = float(model["home_points"]) * stress_home
        ap = float(model["away_points"]) * stress_away
        margin = hp-ap; total = hp+ap
        if market == "h2h":
            p_home = NormalDist(mu=margin, sigma=SPORTS[sport_key]["margin_sd"]).cdf(0)  # P(margin noise <= 0)
            p_home = 1.0 - p_home
            return (p_home if sel == _norm_name(home) else 1-p_home), 0.0
        if market == "spreads" and point is not None:
            selected_margin = margin if sel == _norm_name(home) else -margin
            # win if selected score margin + handicap > 0 => selected margin > -point
            win, push = _normal_discrete_prob(selected_margin, SPORTS[sport_key]["margin_sd"], -point, "over")
            return win, push
        if market == "totals" and point is not None:
            side = "over" if "over" in sel else "under"
            return _normal_discrete_prob(total, SPORTS[sport_key]["total_sd"], point, side)
        return float("nan"), 0.0

    hm = float(model["home_goals"]) * stress_home
    am = float(model["away_goals"]) * stress_away
    matrix = _nhl_matrix(hm, am)
    ot_home = 1/(1+math.exp(-(hm-am)*.55))
    return _line_prob_from_matrix(matrix, market=market, selection=selection, point=point, home=home, away=away, ot_share_home=ot_home)


def analyze_pro_event(market_rows: pd.DataFrame, sport_key: str, context_provider: ESPNProContext | None = None, recent_n: int | None = None) -> pd.DataFrame:
    """Analyze one event's consensus market rows."""
    if market_rows is None or market_rows.empty:
        return pd.DataFrame()
    if sport_key not in SPORTS:
        raise ValueError(f"unsupported sport_key: {sport_key}")
    r0 = market_rows.iloc[0]
    home_name, away_name, kickoff = str(r0["home_team"]), str(r0["away_team"]), r0["commence_time"]
    provider = context_provider or ESPNProContext(sport_key)
    home, herr = provider.recent(home_name, kickoff, n=recent_n)
    away, aerr = provider.recent(away_name, kickoff, n=recent_n)
    availability = provider.availability(home_name, away_name, kickoff)
    ledger = _build_ledger(sport_key, home, away, availability)
    independent = _independent_event(sport_key, home_name, away_name, home, away, availability)

    min_games = min(home.games, away.games)
    missing_count = len(ledger.missing)
    base_unc = (5.2 if sport_key == "americanfootball_nfl" else 5.8) + max(0, 5-min_games)*.45 + missing_count*.35
    if sport_key == "icehockey_nhl" and "starting_goalie" in ledger.missing:
        base_unc += 1.0
    if sport_key == "americanfootball_nfl" and "qb_status" in ledger.missing:
        base_unc += .8
    base_unc = _clamp(base_unc, 4.5, 10.0)
    stage = "CONTEXT VERIFIED" if ledger.coverage >= .65 and min_games >= 4 else "CONTEXT PARTIAL"
    data_quality = "HIGH" if ledger.coverage >= .72 and min_games >= 5 else "MEDIUM" if min_games >= 3 else "LOW"

    out=[]
    for _, row in market_rows.iterrows():
        raw_win, raw_push = _raw_probability(sport_key, independent, row, home_name, away_name)
        market_prob = _num(row.get("consensus_prob"))
        if not (math.isfinite(raw_win) and math.isfinite(market_prob)):
            continue
        resolved=max(1e-9,1-raw_push); raw_cond=_clamp(raw_win/resolved, .001,.999)
        gap=(raw_cond-market_prob)*100
        model_weight = _clamp(.34 + .28*ledger.coverage + .025*min(min_games,6), .34, .64)
        sanity="OK"
        if abs(gap)>=14:
            model_weight*=.55; sanity="HIGH_DISAGREEMENT"
        elif abs(gap)>=9:
            model_weight*=.75; sanity="CHECK"
        final_cond=model_weight*raw_cond+(1-model_weight)*market_prob
        final_win=final_cond*resolved
        ev=analyze_bet(_num(row.get("best_odds")), final_win, raw_push, base_unc)

        scenarios=[]
        mults=(.94,1.0,1.06) if sport_key=="americanfootball_nfl" else (.92,1.0,1.08)
        for hm in mults:
            for am in mults:
                sw,sp=_raw_probability(sport_key, independent, row, home_name, away_name, hm, am)
                if not math.isfinite(sw): continue
                sr=max(1e-9,1-sp); sq=_clamp(sw/sr,.001,.999)
                fw=(model_weight*sq+(1-model_weight)*market_prob)*sr
                scenarios.append((fw,sp,f"scoring {hm:.2f}/{am:.2f}"))
        robust=final_probability_assessment(
            odds=_num(row.get("best_odds")), final_win_prob=final_win, final_push_prob=raw_push,
            uncertainty_pp=base_unc, scenario_probabilities=scenarios, data_ready=data_quality!="LOW",
            lineup_required=False, lineup_confirmed=True,
            forced_review_reason="independent/market disagreement >=14pp" if sanity=="HIGH_DISAGREEMENT" else None,
        )
        cases,risk=build_counter_cases(
            sample_matches=min_games, lineup_confirmed=None, sanity=sanity,
            uncertainty_pp=base_unc, signal_coverage=ledger.coverage, stage=None,
            international=False,
        )
        # Sport-specific failure routes that matter in direct manual analysis.
        if sport_key=="icehockey_nhl" and "starting_goalie" in ledger.missing:
            cases.append({"code":"GOALIE_UNCONFIRMED","severity":"medium","text":"starting goalie not verified; late goalie change can move side/total materially"})
            if risk=="LOW": risk="MEDIUM"
        if sport_key=="americanfootball_nfl" and "qb_status" in ledger.missing:
            cases.append({"code":"QB_STATUS_UNVERIFIED","severity":"medium","text":"current starting-QB status was not independently verified by the public feed"})
            if risk=="LOW": risk="MEDIUM"
        fields=decision_fields(ledger=ledger,counter_cases=cases,counter_risk=risk,robust=robust,legacy_eligible=ev.ev_roi>0)
        d=row.to_dict()
        d.update({
            "sport_key":sport_key, "sport_family":SPORTS[sport_key]["family"],
            "raw_independent_prob":raw_win,"raw_push_prob":raw_push,"model_win_prob":final_win,"push_prob":raw_push,
            "market_prob":market_prob,"model_weight":model_weight,"sanity":sanity,"uncertainty_pp":base_unc,
            "break_even":ev.break_even,"edge_pp":ev.edge_pp,"ev_roi":ev.ev_roi,"point_ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,"kelly_scaled":ev.kelly_scaled,"grade":ev.grade,
            "stage":stage,"data_quality":data_quality,"lineup_confirmed":False,"probable_lineup":False,
            "home_form_matches":home.games,"away_form_matches":away.games,
            "home_recent_points_for":home.points_for,"home_recent_points_against":home.points_against,
            "away_recent_points_for":away.points_for,"away_recent_points_against":away.points_against,
            "home_recent_margin":home.margin,"away_recent_margin":away.margin,
            "home_rest_days":home.rest_days,"away_rest_days":away.rest_days,
            "home_injury_weight":availability.get("home_weight",0.0),"away_injury_weight":availability.get("away_weight",0.0),
            "injury_available":availability.get("available",False),"availability_news_used":availability.get("available",False),
            "recent_form_used":min_games>=3,"travel_rest_used":math.isfinite(home.rest_days) and math.isfinite(away.rest_days),
            "home_expected_points":independent.get("home_points"),"away_expected_points":independent.get("away_points"),
            "home_expected_goals":independent.get("home_goals"),"away_expected_goals":independent.get("away_goals"),
            "match_data_verified":data_quality!="LOW",
            "match_data_status":data_quality,
            "match_data_missing":", ".join(ledger.missing),
            "model_validation_status":"성능 검증 대기" if not fields.get("v3_candidate") else "실시간 후보 · 사후 정산 대상",
            "data_checked_at":datetime.now(timezone.utc).isoformat(),
            **fields,
        })
        out.append(d)
    frame=pd.DataFrame(out)
    if frame.empty:
        return frame

    # Reuse the engine's generic adaptive calibration. NHL/NFL activate only
    # after sufficient resolved pregame samples, just like the other families.
    frame=apply_adaptive_layer(frame, SPORTS[sport_key]["family"])

    # Calibration can move displayed probabilities, therefore recompute EV and
    # stress/ROBUST status from the final probability shown to the user.
    final_rows=[]
    for _, row in frame.iterrows():
        p=_num(row.get("model_win_prob")); push=max(0.0,_num(row.get("push_prob"),0.0)); odds=_num(row.get("best_odds"))
        ev=analyze_bet(odds,p,push,_num(row.get("uncertainty_pp"),0.0))
        market_prob=_num(row.get("consensus_prob")); base_q=p/max(1e-9,1-push)
        scenarios=[]
        for delta in (-.045,-.025,0,.025,.045):
            q=_clamp(base_q+delta,.001,.999); scenarios.append((q*(1-push),push,f"final-prob {delta:+.3f}"))
        forced=None
        if str(row.get("adaptive_gate") or "") not in {"","OK"}:
            forced=str(row.get("adaptive_gate"))
        robust=final_probability_assessment(
            odds=odds,final_win_prob=p,final_push_prob=push,uncertainty_pp=_num(row.get("uncertainty_pp"),0),
            scenario_probabilities=scenarios,data_ready=str(row.get("data_quality"))!="LOW",
            lineup_required=False,lineup_confirmed=True,forced_review_reason=forced,
        )
        d=row.to_dict(); d.update({
            "break_even":ev.break_even,"edge_pp":ev.edge_pp,"ev_roi":ev.ev_roi,"point_ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,"kelly_scaled":ev.kelly_scaled,
            "final_decision_recomputed":True,
            "v3_decision_status":robust["robust_status"],
            "v3_candidate":robust["robust_status"] in {"ROBUST","SENSITIVE"} and ev.ev_roi>0,
            "v3_parlay_eligible":robust["robust_status"]=="ROBUST" and str(row.get("counter_case_risk"))!="HIGH" and ev.conservative_ev_roi>0,
            **robust,
        })
        final_rows.append(d)
    return pd.DataFrame(final_rows).sort_values(["v3_parlay_eligible","robust_positive_ratio","robust_ev_p10","conservative_ev_roi"],ascending=[False,False,False,False]).reset_index(drop=True)


def analyze_pro_board(odds_api, sport_key: str, *, region: str = "us", markets=("h2h","spreads","totals"), min_books: int = 3, selected_date=None, recent_n: int | None = None):
    """Fetch current board/odds and analyze every event for NFL or NHL.

    Returns (ranked, status_rows, raw_odds, consensus_frame, headers).
    """
    from .market import clean_odds, consensus
    if sport_key not in SPORTS:
        raise ValueError(f"unsupported sport: {sport_key}")
    events, headers = odds_api.odds(sport_key, regions=region, markets=",".join(markets))
    raw=clean_odds(odds_api.flatten(events))
    if raw.empty:
        return pd.DataFrame(), [], raw, pd.DataFrame(), headers
    if selected_date is not None:
        ts=pd.to_datetime(raw["commence_time"],utc=True,errors="coerce").dt.tz_convert("Asia/Seoul")
        raw=raw.loc[ts.dt.date==selected_date].copy()
    market=consensus(raw,min_books=min_books)
    if market.empty:
        return pd.DataFrame(), [], raw, market, headers
    provider=ESPNProContext(sport_key)
    rows=[]; status=[]
    for eid,g in market.groupby("event_id",sort=False):
        label=f"{g.iloc[0]['home_team']} vs {g.iloc[0]['away_team']}"
        try:
            analyzed=analyze_pro_event(g,sport_key,context_provider=provider,recent_n=recent_n)
            if not analyzed.empty:
                rows.append(analyzed)
                status.append({"경기":label,"상태":"분석 완료","데이터":str(analyzed.iloc[0].get("data_quality")),"MISSING":str(analyzed.iloc[0].get("missing_signals") or "없음")})
            else:
                status.append({"경기":label,"상태":"분석 결과 없음","데이터":"LOW","MISSING":"확률 산출 실패"})
        except Exception as e:
            status.append({"경기":label,"상태":"수집/분석 실패","데이터":"LOW","MISSING":f"{type(e).__name__}: {e}"})
    ranked=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()
    return ranked,status,raw,market,headers
