"""NHL/NFL automatic analysis pipeline for Sports EV Engine.

v3.6.3 NHL integrity fix
- NHL early-season prior-season fallback with decay instead of blanket DATA_HOLD
- DATA_HOLD / PROVISIONAL / FINAL state separation
- explicit starting-goalie audit (never inferred from odds)
- shots-for AND shots-against context
- NHL h2h settlement scope separation: OT_INCLUDED_2WAY vs REGULATION_3WAY
- final probability/EV/robustness recomputation remains downstream of calibration
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

PRO_SPORTS_BUILD = "3.6.3-nhl-integrity"

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
        # 45 days kills the model at the season opener.  Use a decayed
        # prior-season fallback, while still making current-season games full weight.
        "lookback_days": 260,
        "recent_n": 10,
        "prior_season_weight": 0.42,
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
    return (
        len(aset & bset) >= 2
        or (len(aset) == 1 and next(iter(aset)) in bset)
        or (len(bset) == 1 and next(iter(bset)) in aset)
    )


def _nhl_season_start(ts: Any) -> pd.Timestamp:
    k = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(k):
        return pd.Timestamp("1970-07-01", tz="UTC")
    year = int(k.year) if int(k.month) >= 7 else int(k.year) - 1
    return pd.Timestamp(year=year, month=7, day=1, tz="UTC")


def _effective_games(row: "TeamRecent") -> float:
    v = _num(getattr(row, "effective_games", 0.0), 0.0)
    return v if v > 0 else float(getattr(row, "games", 0) or 0)


def _risk_from_cases(cases: list[dict]) -> str:
    rank = {"low": 1, "medium": 2, "high": 3}
    sev = max((rank.get(str(x.get("severity") or "").lower(), 0) for x in cases), default=0)
    return {0: "LOW", 1: "LOW", 2: "MEDIUM", 3: "HIGH"}[sev]


@dataclass
class TeamRecent:
    games: int = 0
    effective_games: float = 0.0
    current_season_games: int = 0
    prior_season_games: int = 0
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
    pk_pct: float = float("nan")
    faceoff_pct: float = float("nan")
    source: str = "ESPN public scoreboard/summary"


class ESPNProContext:
    """Fail-soft public context collector for NFL/NHL.

    Important: unavailable goalie/QB/injury data is never invented from odds.
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
            self._score_cache[key] = self._get(
                f"{self.BASE}/{path}/scoreboard", {"dates": key, "limit": 500}
            )
        return self._score_cache[key]

    def scoreboard_range(self, start: datetime, end: datetime) -> dict:
        """Chunk long NHL ranges so ESPN's 500-event cap cannot truncate a season."""
        if (end - start).days <= 65:
            return self.scoreboard(start, end)
        events: list[dict] = []
        seen: set[str] = set()
        cur = start
        while cur < end:
            nxt = min(end, cur + timedelta(days=60))
            board = self.scoreboard(cur, nxt)
            for event in board.get("events") or []:
                eid = str(event.get("id") or "")
                if eid and eid not in seen:
                    seen.add(eid)
                    events.append(event)
            cur = nxt + timedelta(days=1)
        return {"events": events}

    def summary(self, event_id: str) -> dict:
        eid = str(event_id or "")
        if not eid:
            return {}
        if eid not in self._summary_cache:
            path = self.cfg["espn_path"]
            try:
                self._summary_cache[eid] = self._get(
                    f"{self.BASE}/{path}/summary", {"event": eid}
                )
            except Exception:
                self._summary_cache[eid] = {}
        return self._summary_cache[eid]

    @staticmethod
    def _event_competitors(event: dict) -> list[dict]:
        comps = event.get("competitions") or []
        return (comps[0].get("competitors") or []) if comps else []

    @staticmethod
    def _team_name(c: dict) -> str:
        t = c.get("team") or {}
        return str(t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "")

    def _find_competitor(self, comps: list[dict], team_name: str) -> dict | None:
        return next((c for c in comps if _names_match(self._team_name(c), team_name)), None)

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
            try:
                a, b = (float(x) for x in s.split("-")[:2])
                return 100.0 * a / b if b else float("nan")
            except Exception:
                return float("nan")
        try:
            return float(re.sub(r"[^0-9.+-]", "", s))
        except ValueError:
            return float("nan")

    def recent(self, team_name: str, kickoff: Any, n: int | None = None) -> tuple[TeamRecent, list[str]]:
        cfg = self.cfg
        n = int(n or cfg["recent_n"])
        k = pd.to_datetime(kickoff, utc=True, errors="coerce")
        if pd.isna(k):
            return TeamRecent(), ["invalid kickoff"]
        end = k.to_pydatetime() - timedelta(hours=1)
        start = end - timedelta(days=int(cfg["lookback_days"]))
        try:
            board = self.scoreboard_range(start, end)
        except Exception as e:
            return TeamRecent(), [f"scoreboard unavailable: {type(e).__name__}"]

        season_start = _nhl_season_start(k) if self.sport_key == "icehockey_nhl" else None
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
                pf, pa = float(me.get("score")), float(opp.get("score"))
            except (TypeError, ValueError):
                continue
            ts = pd.to_datetime(event.get("date"), utc=True, errors="coerce")
            if pd.isna(ts) or ts >= k:
                continue
            current = True if season_start is None else bool(ts >= season_start)
            age_days = max(0.0, (k - ts).total_seconds() / 86400.0)
            if self.sport_key == "icehockey_nhl" and not current:
                base = float(cfg.get("prior_season_weight", 0.42))
                weight = base * math.exp(-max(0.0, age_days - 90.0) / 260.0)
            else:
                denom = 180.0 if self.sport_key == "icehockey_nhl" else 300.0
                weight = math.exp(-age_days / denom)
            matches.append((ts, pf, pa, str(event.get("id") or ""), current, max(.12, weight)))

        matches.sort(key=lambda x: x[0], reverse=True)
        matches = matches[:n]
        if not matches:
            return TeamRecent(), ["no completed recent games"]

        wsum = sum(x[5] for x in matches) or 1.0
        pf = sum(x[1] * x[5] for x in matches) / wsum
        pa = sum(x[2] * x[5] for x in matches) / wsum
        current_games = sum(1 for x in matches if x[4])
        prior_games = len(matches) - current_games
        effective_games = sum(x[5] for x in matches)
        rest = max(0.0, (k - matches[0][0]).total_seconds() / 86400.0)
        if self.sport_key == "icehockey_nhl" and not matches[0][4]:
            rest = float("nan")

        mine: dict[str, list[float]] = {}
        opps: dict[str, list[float]] = {}
        for _, _, _, eid, _, _ in matches[: min(len(matches), 5)]:
            smry = self.summary(eid)
            teams = ((smry.get("boxscore") or {}).get("teams") or [])
            for trow in teams:
                t = (trow.get("team") or {}).get("displayName") or (trow.get("team") or {}).get("name")
                target = mine if _names_match(t, team_name) else opps
                for stat in trow.get("statistics") or []:
                    name = str(stat.get("name") or stat.get("label") or "").strip().lower()
                    val = self._parse_stat(stat.get("displayValue", stat.get("value")))
                    if math.isfinite(val):
                        target.setdefault(name, []).append(val)

        def avg(rows: dict[str, list[float]], *names: str) -> float:
            vals: list[float] = []
            for name in names:
                vals.extend(rows.get(name.lower(), []))
            return sum(vals) / len(vals) if vals else float("nan")

        source = "ESPN public scoreboard/summary"
        if prior_games:
            source += f"; prior-season decay fallback {prior_games} games"
        return TeamRecent(
            games=len(matches), effective_games=effective_games,
            current_season_games=current_games, prior_season_games=prior_games,
            wins=sum(1 for x in matches if x[1] > x[2]),
            points_for=pf, points_against=pa, margin=pf-pa, rest_days=rest,
            shots_for=avg(mine, "shots", "shotsTotal", "shotsOnGoal"),
            shots_against=avg(opps, "shots", "shotsTotal", "shotsOnGoal"),
            turnovers=avg(mine, "turnovers", "totalTurnovers"),
            total_yards=avg(mine, "totalYards", "netTotalYards"),
            sacks_allowed=avg(mine, "sacksYardsLost", "sacks"),
            third_down_pct=avg(mine, "thirdDownEff", "thirdDownPct"),
            pp_pct=avg(mine, "powerPlayPct", "powerPlayPercentage"),
            pk_pct=avg(mine, "penaltyKillPct", "penaltyKillPercentage"),
            faceoff_pct=avg(mine, "faceoffPercent", "faceoffPct"),
            source=source,
        ), []

    def event_summary(self, home: str, away: str, kickoff: Any) -> tuple[dict, list[str]]:
        k = pd.to_datetime(kickoff, utc=True, errors="coerce")
        if pd.isna(k):
            return {}, ["invalid kickoff"]
        try:
            board = self.scoreboard(k.to_pydatetime()-timedelta(days=1), k.to_pydatetime()+timedelta(days=1))
        except Exception as e:
            return {}, [f"event scoreboard unavailable: {type(e).__name__}"]
        for event in board.get("events") or []:
            comps = self._event_competitors(event)
            if self._find_competitor(comps, home) and self._find_competitor(comps, away):
                return self.summary(str(event.get("id") or "")), []
        return {}, ["matching ESPN event not found"]

    def goalie_status(self, home: str, away: str, kickoff: Any) -> dict:
        """Only explicit starter markers count. No roster-order inference."""
        if self.sport_key != "icehockey_nhl":
            return {"available": False, "home_confirmed": False, "away_confirmed": False,
                    "home_goalie": "", "away_goalie": "", "source": ""}
        summary, errs = self.event_summary(home, away, kickoff)
        rows: list[dict] = []

        def walk(obj: Any, path: str = "", team_hint: str = ""):
            if isinstance(obj, dict):
                local_team = team_hint
                team_obj = obj.get("team")
                if isinstance(team_obj, dict):
                    local_team = str(team_obj.get("displayName") or team_obj.get("shortDisplayName") or team_obj.get("name") or local_team)
                athlete = obj.get("athlete") if isinstance(obj.get("athlete"), dict) else obj
                pos_obj = athlete.get("position") if isinstance(athlete, dict) else None
                pos = str(pos_obj.get("abbreviation") or pos_obj.get("name") or "") if isinstance(pos_obj, dict) else ""
                if not pos:
                    pos = str(obj.get("positionAbbreviation") or obj.get("positionCode") or "")
                name = str(athlete.get("displayName") or athlete.get("fullName") or "") if isinstance(athlete, dict) else ""
                explicit = bool(obj.get("starter") is True or obj.get("isStarter") is True or obj.get("starting") is True)
                explicit = explicit or ("startinggoal" in _norm_name(path).replace(" ", ""))
                if pos.upper() == "G" and name and explicit:
                    rows.append({"team": local_team, "name": name})
                for key, value in obj.items():
                    walk(value, f"{path}.{key}" if path else str(key), local_team)
            elif isinstance(obj, list):
                for i, value in enumerate(obj):
                    walk(value, f"{path}[{i}]", team_hint)

        if isinstance(summary, dict):
            walk(summary)

        def pick(team_name: str) -> str:
            for row in rows:
                if _names_match(row.get("team"), team_name):
                    return str(row.get("name") or "")
            return ""

        hg, ag = pick(home), pick(away)
        return {
            "available": bool(hg and ag),
            "home_confirmed": bool(hg), "away_confirmed": bool(ag),
            "home_goalie": hg, "away_goalie": ag, "errors": errs,
            "source": "ESPN event summary explicit starter" if (hg or ag) else "",
        }

    def availability(self, home: str, away: str, kickoff: Any) -> dict:
        summary, errs = self.event_summary(home, away, kickoff)
        injuries = summary.get("injuries") if isinstance(summary, dict) else None
        rows = []
        if isinstance(injuries, list):
            for group in injuries:
                team = ((group.get("team") or {}).get("displayName") or (group.get("team") or {}).get("name") or "")
                for item in group.get("injuries") or group.get("items") or []:
                    athlete = item.get("athlete") or {}
                    rows.append({
                        "team": team,
                        "status": str(item.get("status") or item.get("type") or "").lower(),
                        "position": str(((athlete.get("position") or {}).get("abbreviation") or "")).upper(),
                        "name": athlete.get("displayName"),
                    })

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
                    score += 2.8 if pos == "QB" else 1.3 if pos in {"OT","T","LT","RT","WR","CB","EDGE","DE"} else 0.7
                else:
                    score += 1.8 if pos == "G" else 1.0 if pos in {"C","D","RW","LW"} else 0.6
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


def _line_prob_from_matrix(matrix, *, market: str, selection: str, point: float | None,
                           home: str, away: str, ot_share_home: float = .5,
                           h2h_scope: str = "OT_INCLUDED_2WAY"):
    sel, hname, aname = _norm_name(selection), _norm_name(home), _norm_name(away)
    if market == "h2h":
        reg_h = sum(p for h, a, p in matrix if h > a)
        reg_a = sum(p for h, a, p in matrix if a > h)
        tie = max(0.0, 1.0-reg_h-reg_a)
        if str(h2h_scope or "").upper() == "REGULATION_3WAY":
            if sel in {"draw", "tie", "x"}:
                return tie, 0.0
            if sel == hname:
                return reg_h, 0.0
            if sel == aname:
                return reg_a, 0.0
            return float("nan"), 0.0
        hp = reg_h + tie*ot_share_home
        ap = reg_a + tie*(1.0-ot_share_home)
        if sel == hname:
            return hp, 0.0
        if sel == aname:
            return ap, 0.0
        return float("nan"), 0.0

    if point is None or not math.isfinite(float(point)):
        return float("nan"), 0.0
    win = push = 0.0
    pnt = float(point)
    for hs, aw, prob in matrix:
        if market == "totals":
            target = hs + aw - pnt
            result = target if "over" in sel else -target
        elif market == "spreads":
            if sel == hname:
                result = hs-aw+pnt
            elif sel == aname:
                result = aw-hs+pnt
            else:
                continue
        else:
            continue
        if result > 1e-9:
            win += prob
        elif abs(result) <= 1e-9:
            push += prob
    return win, push


def _normal_discrete_prob(mean: float, sd: float, threshold: float, side: str) -> tuple[float, float]:
    nd = NormalDist(mu=mean, sigma=max(sd, 1e-6))
    if abs(threshold-round(threshold)) < 1e-9:
        t = float(round(threshold))
        push = max(0.0, nd.cdf(t+.5)-nd.cdf(t-.5))
        win = 1.0-nd.cdf(t+.5) if side == "over" else nd.cdf(t-.5)
    else:
        push = 0.0
        win = 1.0-nd.cdf(threshold) if side == "over" else nd.cdf(threshold)
    return _clamp(win,0,1), _clamp(push,0,1)


def _shrink(value: float, mean: float, n: float, prior_games: float = 5.0) -> float:
    if not math.isfinite(value) or n <= 0:
        return mean
    w = n/(n+prior_games)
    return w*value + (1-w)*mean


def _build_ledger(sport_key: str, home: TeamRecent, away: TeamRecent,
                  availability: dict, goalies: dict | None = None) -> SignalLedger:
    ledger = SignalLedger(); goalies = goalies or {}
    effective = min(_effective_games(home), _effective_games(away))
    form_ready = effective >= (2.0 if sport_key == "icehockey_nhl" else 3.0)
    ledger.add("recent_form", form_ready,
               "home" if _num(home.margin,0) > _num(away.margin,0) else "away",
               _num(home.margin,0)-_num(away.margin,0), .72,
               "ESPN completed games", f"raw H/A {home.games}/{away.games}; effective {effective:.1f}")
    rest_ok = math.isfinite(home.rest_days) and math.isfinite(away.rest_days)
    ledger.add("rest_schedule", rest_ok,
               "home" if _num(home.rest_days,0) > _num(away.rest_days,0) else "away",
               _clamp((_num(home.rest_days,0)-_num(away.rest_days,0))*.4,-2,2), .65,
               "ESPN schedule", f"rest H/A {home.rest_days:.1f}/{away.rest_days:.1f}" if rest_ok else "")
    ledger.add("injuries_availability", bool(availability.get("available")),
               "home" if availability.get("away_weight",0) > availability.get("home_weight",0) else "away",
               float(availability.get("away_weight",0))-float(availability.get("home_weight",0)), .65,
               availability.get("source",""), f"out/doubtful H/A {availability.get('home_count',0)}/{availability.get('away_count',0)}")

    if sport_key == "americanfootball_nfl":
        ledger.add("offensive_yards", math.isfinite(home.total_yards) and math.isfinite(away.total_yards),
                   "home" if _num(home.total_yards,0) > _num(away.total_yards,0) else "away", 0, .55, "ESPN boxscore")
        ledger.add("turnovers", math.isfinite(home.turnovers) and math.isfinite(away.turnovers),
                   "home" if _num(home.turnovers,99) < _num(away.turnovers,99) else "away", 0, .55, "ESPN boxscore")
        ledger.add("qb_status", False, note="public feed did not provide a verified current starter; no QB invented")
        ledger.add("ol_pass_protection", math.isfinite(home.sacks_allowed) and math.isfinite(away.sacks_allowed), source="ESPN boxscore")
    else:
        shot_ok = all(math.isfinite(x) for x in (home.shots_for,home.shots_against,away.shots_for,away.shots_against))
        hsd = _num(home.shots_for,0)-_num(home.shots_against,0)
        asd = _num(away.shots_for,0)-_num(away.shots_against,0)
        ledger.add("shot_share", shot_ok, "home" if hsd > asd else "away", hsd-asd, .60,
                   "ESPN boxscore", f"shot diff H/A {hsd:+.1f}/{asd:+.1f}" if shot_ok else "")
        st_ok = all(math.isfinite(x) for x in (home.pp_pct, away.pp_pct))
        ledger.add("special_teams", st_ok,
                   "home" if _num(home.pp_pct,0) > _num(away.pp_pct,0) else "away",
                   _num(home.pp_pct,0)-_num(away.pp_pct,0), .55, "ESPN boxscore",
                   f"PP H/A {home.pp_pct:.1f}/{away.pp_pct:.1f}" if st_ok else "")
        goalie_ok = bool(goalies.get("home_confirmed") and goalies.get("away_confirmed"))
        ledger.add("starting_goalie", goalie_ok, "context", 0.0, .90, goalies.get("source",""),
                   f"H {goalies.get('home_goalie','')}; A {goalies.get('away_goalie','')}" if goalie_ok
                   else "explicit starting goalie not yet verified; provisional only")
    return ledger


def _independent_event(sport_key: str, home_name: str, away_name: str,
                       home: TeamRecent, away: TeamRecent, availability: dict):
    cfg = SPORTS[sport_key]
    if sport_key == "americanfootball_nfl":
        mean = cfg["league_mean"]
        h_for = _shrink(home.points_for,mean,home.games,4.5)
        h_against = _shrink(home.points_against,mean,home.games,4.5)
        a_for = _shrink(away.points_for,mean,away.games,4.5)
        a_against = _shrink(away.points_against,mean,away.games,4.5)
        hp = .54*h_for+.46*a_against+.9
        ap = .54*a_for+.46*h_against-.3
        inj = _clamp((availability.get("away_weight",0)-availability.get("home_weight",0))*.22,-1.8,1.8)
        hp += inj; ap -= inj
        if math.isfinite(home.rest_days) and math.isfinite(away.rest_days):
            rest = _clamp((home.rest_days-away.rest_days)*.18,-1,1)
            hp += rest; ap -= rest
        return {"home_points":_clamp(hp,10,38),"away_points":_clamp(ap,10,38),"margin":hp-ap,"total":hp+ap}

    mean = cfg["league_mean"]
    hn, an = _effective_games(home), _effective_games(away)
    h_for = _shrink(home.points_for,mean,hn,5.0)
    h_against = _shrink(home.points_against,mean,hn,5.0)
    a_for = _shrink(away.points_for,mean,an,5.0)
    a_against = _shrink(away.points_against,mean,an,5.0)
    hm = .55*h_for+.45*a_against+.08
    am = .55*a_for+.45*h_against-.03
    inj = _clamp((availability.get("away_weight",0)-availability.get("home_weight",0))*.018,-.12,.12)
    hm += inj; am -= inj
    if math.isfinite(home.rest_days) and math.isfinite(away.rest_days):
        rest = _clamp((home.rest_days-away.rest_days)*.025,-.08,.08)
        hm += rest; am -= rest
    return {"home_goals":_clamp(hm,1.7,4.6),"away_goals":_clamp(am,1.7,4.6)}


def _raw_probability(sport_key: str, model: dict, row: pd.Series, home: str, away: str,
                     stress_home: float = 1.0, stress_away: float = 1.0):
    market = str(row.get("market") or "")
    selection = str(row.get("selection") or "")
    point = _num(row.get("point")); point = point if math.isfinite(point) else None
    sel = _norm_name(selection)
    if sport_key == "americanfootball_nfl":
        hp, ap = float(model["home_points"])*stress_home, float(model["away_points"])*stress_away
        margin, total = hp-ap, hp+ap
        if market == "h2h":
            p_home = 1.0-NormalDist(mu=margin,sigma=SPORTS[sport_key]["margin_sd"]).cdf(0)
            return (p_home if sel == _norm_name(home) else 1-p_home), 0.0
        if market == "spreads" and point is not None:
            sm = margin if sel == _norm_name(home) else -margin
            return _normal_discrete_prob(sm,SPORTS[sport_key]["margin_sd"],-point,"over")
        if market == "totals" and point is not None:
            return _normal_discrete_prob(total,SPORTS[sport_key]["total_sd"],point,"over" if "over" in sel else "under")
        return float("nan"), 0.0

    hm, am = float(model["home_goals"])*stress_home, float(model["away_goals"])*stress_away
    matrix = _nhl_matrix(hm,am)
    ot_home = 1/(1+math.exp(-(hm-am)*.55))
    return _line_prob_from_matrix(
        matrix, market=market, selection=selection, point=point, home=home, away=away,
        ot_share_home=ot_home, h2h_scope=str(row.get("nhl_h2h_scope") or "OT_INCLUDED_2WAY")
    )


def analyze_pro_event(market_rows: pd.DataFrame, sport_key: str,
                      context_provider: ESPNProContext | None = None,
                      recent_n: int | None = None) -> pd.DataFrame:
    if market_rows is None or market_rows.empty:
        return pd.DataFrame()
    if sport_key not in SPORTS:
        raise ValueError(f"unsupported sport_key: {sport_key}")
    market_rows = market_rows.copy()
    r0 = market_rows.iloc[0]
    home_name, away_name, kickoff = str(r0["home_team"]), str(r0["away_team"]), r0["commence_time"]
    provider = context_provider or ESPNProContext(sport_key)

    if sport_key == "icehockey_nhl":
        h2h = market_rows.loc[market_rows["market"].astype(str).eq("h2h"),"selection"].astype(str)
        has_draw = h2h.map(lambda x: _norm_name(x) in {"draw","tie","x"}).any()
        market_rows["nhl_h2h_scope"] = ""
        market_rows.loc[market_rows["market"].astype(str).eq("h2h"),"nhl_h2h_scope"] = (
            "REGULATION_3WAY" if has_draw else "OT_INCLUDED_2WAY"
        )

    home, _ = provider.recent(home_name,kickoff,n=recent_n)
    away, _ = provider.recent(away_name,kickoff,n=recent_n)
    availability = provider.availability(home_name,away_name,kickoff)
    goalies = {"available":False,"home_confirmed":False,"away_confirmed":False,"home_goalie":"","away_goalie":"","source":""}
    if sport_key == "icehockey_nhl" and hasattr(provider,"goalie_status"):
        try:
            goalies = provider.goalie_status(home_name,away_name,kickoff) or goalies
        except Exception as e:
            goalies["errors"] = [f"goalie lookup failed: {type(e).__name__}"]

    ledger = _build_ledger(sport_key,home,away,availability,goalies)
    independent = _independent_event(sport_key,home_name,away_name,home,away,availability)
    min_games = min(home.games,away.games)
    effective_games = min(_effective_games(home),_effective_games(away))
    missing_count = len(ledger.missing)

    if sport_key == "icehockey_nhl":
        core_ready = effective_games >= 1.5 and all(math.isfinite(_num(x)) for x in (
            home.points_for,home.points_against,away.points_for,away.points_against))
        prior_fallback = bool(home.prior_season_games or away.prior_season_games)
        base_unc = 5.3 + max(0.0,4.0-effective_games)*.55 + missing_count*.25
        if prior_fallback:
            base_unc += .45
        if "starting_goalie" in ledger.missing:
            base_unc += .80
        base_unc = _clamp(base_unc,4.8,9.5)
        goalie_final = bool(goalies.get("home_confirmed") and goalies.get("away_confirmed"))
        if not core_ready:
            stage, data_quality = "DATA_HOLD", "LOW"
        elif goalie_final and ledger.coverage >= .50 and effective_games >= 3.0:
            stage = "FINAL"
            data_quality = "HIGH" if ledger.coverage >= .70 and effective_games >= 5.0 else "MEDIUM"
        else:
            stage, data_quality = "PROVISIONAL", "MEDIUM"
    else:
        core_ready = min_games >= 3
        base_unc = 5.2 + max(0,5-min_games)*.45 + missing_count*.35
        if "qb_status" in ledger.missing:
            base_unc += .8
        base_unc = _clamp(base_unc,4.5,10.0)
        stage = "CONTEXT VERIFIED" if ledger.coverage >= .65 and min_games >= 4 else "CONTEXT PARTIAL"
        data_quality = "HIGH" if ledger.coverage >= .72 and min_games >= 5 else "MEDIUM" if min_games >= 3 else "LOW"

    out=[]
    for _,row in market_rows.iterrows():
        raw_win, raw_push = _raw_probability(sport_key,independent,row,home_name,away_name)
        market_prob = _num(row.get("consensus_prob"))
        if not (math.isfinite(raw_win) and math.isfinite(market_prob)):
            continue
        resolved = max(1e-9,1-raw_push)
        raw_cond = _clamp(raw_win/resolved,.001,.999)
        gap = (raw_cond-market_prob)*100
        sample_strength = effective_games if sport_key == "icehockey_nhl" else min_games
        model_weight = _clamp(.34+.28*ledger.coverage+.025*min(sample_strength,6),.34,.64)
        sanity = "OK"
        if abs(gap) >= 14:
            model_weight *= .55; sanity = "HIGH_DISAGREEMENT"
        elif abs(gap) >= 9:
            model_weight *= .75; sanity = "CHECK"
        final_cond = model_weight*raw_cond+(1-model_weight)*market_prob
        final_win = final_cond*resolved
        ev = analyze_bet(_num(row.get("best_odds")),final_win,raw_push,base_unc)

        scenarios=[]
        mults=(.94,1.0,1.06) if sport_key=="americanfootball_nfl" else (.92,1.0,1.08)
        for hm in mults:
            for am in mults:
                sw,sp=_raw_probability(sport_key,independent,row,home_name,away_name,hm,am)
                if not math.isfinite(sw):
                    continue
                sr=max(1e-9,1-sp); sq=_clamp(sw/sr,.001,.999)
                scenarios.append(((model_weight*sq+(1-model_weight)*market_prob)*sr,sp,f"scoring {hm:.2f}/{am:.2f}"))
        robust=final_probability_assessment(
            odds=_num(row.get("best_odds")),final_win_prob=final_win,final_push_prob=raw_push,
            uncertainty_pp=base_unc,scenario_probabilities=scenarios,data_ready=core_ready,
            lineup_required=False,lineup_confirmed=True,
            forced_review_reason="independent/market disagreement >=14pp" if sanity=="HIGH_DISAGREEMENT" else None)

        if sport_key == "icehockey_nhl":
            cases,_=build_counter_cases(sample_matches=None,lineup_confirmed=None,sanity=sanity,
                                        uncertainty_pp=base_unc,signal_coverage=None,stage=None,international=False)
            if effective_games < 1.5:
                cases.append({"code":"NHL_SAMPLE_MISSING","severity":"high","text":f"effective NHL sample {effective_games:.1f} games"})
            elif effective_games < 3.0:
                cases.append({"code":"NHL_EARLY_SAMPLE","severity":"medium","text":f"effective NHL sample {effective_games:.1f}; prior-season fallback may be active"})
            if ledger.coverage < .25:
                cases.append({"code":"NHL_CONTEXT_THIN","severity":"high","text":f"NHL context coverage {ledger.coverage*100:.0f}%"})
            elif ledger.coverage < .50:
                cases.append({"code":"NHL_CONTEXT_PARTIAL","severity":"medium","text":f"NHL context coverage {ledger.coverage*100:.0f}%"})
            if home.prior_season_games or away.prior_season_games:
                cases.append({"code":"PRIOR_SEASON_FALLBACK","severity":"medium","text":f"decayed prior-season games used H/A {home.prior_season_games}/{away.prior_season_games}"})
            if "starting_goalie" in ledger.missing:
                cases.append({"code":"GOALIE_UNCONFIRMED","severity":"medium","text":"starting goalie not explicitly verified; candidate remains provisional"})
            risk=_risk_from_cases(cases)
        else:
            cases,risk=build_counter_cases(sample_matches=min_games,lineup_confirmed=None,sanity=sanity,
                                           uncertainty_pp=base_unc,signal_coverage=ledger.coverage,stage=None,international=False)
            if "qb_status" in ledger.missing:
                cases.append({"code":"QB_STATUS_UNVERIFIED","severity":"medium","text":"current starting-QB status was not independently verified by the public feed"})
                risk=_risk_from_cases(cases)

        fields=decision_fields(ledger=ledger,counter_cases=cases,counter_risk=risk,
                               robust=robust,legacy_eligible=ev.ev_roi>0)
        if sport_key == "icehockey_nhl" and stage == "PROVISIONAL" and fields.get("v3_decision_status") != "DATA_HOLD":
            fields["v3_decision_status"] = "PROVISIONAL"
            fields["v3_parlay_eligible"] = False

        d=row.to_dict()
        d.update({
            "sport_key":sport_key,"sport_family":SPORTS[sport_key]["family"],
            "raw_independent_prob":raw_win,"raw_push_prob":raw_push,"model_win_prob":final_win,"push_prob":raw_push,
            "market_prob":market_prob,"model_weight":model_weight,"sanity":sanity,"uncertainty_pp":base_unc,
            "break_even":ev.break_even,"edge_pp":ev.edge_pp,"ev_roi":ev.ev_roi,"point_ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,"kelly_scaled":ev.kelly_scaled,"grade":ev.grade,
            "stage":stage,"data_quality":data_quality,"lineup_confirmed":False,"probable_lineup":False,
            "home_form_matches":home.games,"away_form_matches":away.games,
            "home_effective_games":_effective_games(home),"away_effective_games":_effective_games(away),
            "home_current_season_games":home.current_season_games,"away_current_season_games":away.current_season_games,
            "home_prior_season_games":home.prior_season_games,"away_prior_season_games":away.prior_season_games,
            "home_recent_points_for":home.points_for,"home_recent_points_against":home.points_against,
            "away_recent_points_for":away.points_for,"away_recent_points_against":away.points_against,
            "home_recent_margin":home.margin,"away_recent_margin":away.margin,
            "home_shots_for":home.shots_for,"home_shots_against":home.shots_against,
            "away_shots_for":away.shots_for,"away_shots_against":away.shots_against,
            "home_rest_days":home.rest_days,"away_rest_days":away.rest_days,
            "home_injury_weight":availability.get("home_weight",0.0),"away_injury_weight":availability.get("away_weight",0.0),
            "injury_available":availability.get("available",False),"availability_news_used":availability.get("available",False),
            "home_goalie":goalies.get("home_goalie",""),"away_goalie":goalies.get("away_goalie",""),
            "home_goalie_confirmed":bool(goalies.get("home_confirmed")),"away_goalie_confirmed":bool(goalies.get("away_confirmed")),
            "goalie_source":goalies.get("source",""),"recent_form_used":core_ready,
            "travel_rest_used":math.isfinite(home.rest_days) and math.isfinite(away.rest_days),
            "home_expected_points":independent.get("home_points"),"away_expected_points":independent.get("away_points"),
            "home_expected_goals":independent.get("home_goals"),"away_expected_goals":independent.get("away_goals"),
            "match_data_verified":core_ready,"match_data_status":stage if sport_key=="icehockey_nhl" else data_quality,
            "match_data_missing":", ".join(ledger.missing),
            "model_validation_status":("잠정 후보 · 선발 골리/시즌초 컨텍스트 대기"
                                       if sport_key=="icehockey_nhl" and stage=="PROVISIONAL" and fields.get("v3_candidate")
                                       else "성능 검증 대기" if not fields.get("v3_candidate") else "실시간 후보 · 사후 정산 대상"),
            "data_checked_at":datetime.now(timezone.utc).isoformat(),
            **fields,
        })
        out.append(d)

    frame=pd.DataFrame(out)
    if frame.empty:
        return frame
    frame=apply_adaptive_layer(frame,SPORTS[sport_key]["family"])

    final_rows=[]
    for _,row in frame.iterrows():
        p=_num(row.get("model_win_prob")); push=max(0.0,_num(row.get("push_prob"),0.0)); odds=_num(row.get("best_odds"))
        ev=analyze_bet(odds,p,push,_num(row.get("uncertainty_pp"),0.0))
        base_q=p/max(1e-9,1-push)
        scenarios=[(_clamp(base_q+d,.001,.999)*(1-push),push,f"final-prob {d:+.3f}") for d in (-.045,-.025,0,.025,.045)]
        forced=None
        if str(row.get("adaptive_gate") or "") not in {"","OK"}:
            forced=str(row.get("adaptive_gate"))
        data_ready=(str(row.get("stage"))!="DATA_HOLD") if sport_key=="icehockey_nhl" else (str(row.get("data_quality"))!="LOW")
        robust=final_probability_assessment(
            odds=odds,final_win_prob=p,final_push_prob=push,uncertainty_pp=_num(row.get("uncertainty_pp"),0),
            scenario_probabilities=scenarios,data_ready=data_ready,lineup_required=False,lineup_confirmed=True,
            forced_review_reason=forced)
        raw_status=robust["robust_status"]
        display_status=("PROVISIONAL" if sport_key=="icehockey_nhl" and str(row.get("stage"))=="PROVISIONAL" and raw_status!="DATA_HOLD" else raw_status)
        candidate=raw_status in {"ROBUST","SENSITIVE"} and ev.ev_roi>0 and data_ready
        if sport_key=="icehockey_nhl":
            parlay=(str(row.get("stage"))=="FINAL" and raw_status=="ROBUST" and str(row.get("counter_case_risk"))!="HIGH" and ev.conservative_ev_roi>0)
        else:
            parlay=(raw_status=="ROBUST" and str(row.get("counter_case_risk"))!="HIGH" and ev.conservative_ev_roi>0)
        d=row.to_dict()
        d.update({
            "break_even":ev.break_even,"edge_pp":ev.edge_pp,"ev_roi":ev.ev_roi,"point_ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,"kelly_scaled":ev.kelly_scaled,
            "final_decision_recomputed":True,"v3_candidate":candidate,"v3_parlay_eligible":parlay,
            **robust,
        })
        d["v3_decision_status"]=display_status
        final_rows.append(d)

    return pd.DataFrame(final_rows).sort_values(
        ["v3_parlay_eligible","v3_candidate","robust_positive_ratio","robust_ev_p10","conservative_ev_roi"],
        ascending=[False,False,False,False,False]
    ).reset_index(drop=True)


def analyze_pro_board(odds_api, sport_key: str, *, region: str = "us",
                      markets=("h2h","spreads","totals"), min_books: int = 3,
                      selected_date=None, recent_n: int | None = None):
    from .market import clean_odds, consensus
    if sport_key not in SPORTS:
        raise ValueError(f"unsupported sport: {sport_key}")
    events, headers = odds_api.odds(sport_key,regions=region,markets=",".join(markets))
    raw=clean_odds(odds_api.flatten(events))
    if raw.empty:
        return pd.DataFrame(),[],raw,pd.DataFrame(),headers
    if selected_date is not None:
        ts=pd.to_datetime(raw["commence_time"],utc=True,errors="coerce").dt.tz_convert("Asia/Seoul")
        raw=raw.loc[ts.dt.date==selected_date].copy()
    market=consensus(raw,min_books=min_books)
    if market.empty:
        return pd.DataFrame(),[],raw,market,headers
    provider=ESPNProContext(sport_key)
    rows=[]; status=[]
    for _,g in market.groupby("event_id",sort=False):
        label=f"{g.iloc[0]['home_team']} vs {g.iloc[0]['away_team']}"
        try:
            analyzed=analyze_pro_event(g,sport_key,context_provider=provider,recent_n=recent_n)
            if not analyzed.empty:
                rows.append(analyzed)
                first=analyzed.iloc[0]
                status.append({
                    "경기":label,"상태":str(first.get("stage") or "분석 완료"),
                    "데이터":str(first.get("data_quality")),
                    "골리":f"{first.get('home_goalie','') or '미확정'} / {first.get('away_goalie','') or '미확정'}" if sport_key=="icehockey_nhl" else "-",
                    "MISSING":str(first.get("missing_signals") or "없음"),
                })
            else:
                status.append({"경기":label,"상태":"분석 결과 없음","데이터":"LOW","골리":"-","MISSING":"확률 산출 실패"})
        except Exception as e:
            status.append({"경기":label,"상태":"수집/분석 실패","데이터":"LOW","골리":"-","MISSING":f"{type(e).__name__}: {e}"})
    ranked=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()
    return ranked,status,raw,market,headers
