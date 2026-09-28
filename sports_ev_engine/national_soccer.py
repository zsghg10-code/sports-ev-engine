"""Senior national-team odds selection and cross-competition match history."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sports_ev_engine.models.soccer_auto import norm_name


PROVIDER_BUILD = "2.9.3"


def is_senior_international(sport, include_inactive=False):
    key = str(sport.get("key", "")).lower()
    title = " ".join(str(sport.get(k, "")) for k in ("title","description")).lower()
    if not key.startswith("soccer_") or (not include_inactive and not sport.get("active", True)):
        return False
    if any(s in key + " " + title for s in ("women", "womens", "u17", "u19", "u20", "u21", "u23", "youth", "club", "winner")):
        return False
    return bool(
        key.startswith("soccer_fifa_world_cup_qualifiers")
        or key in {"soccer_fifa_world_cup", "soccer_uefa_nations_league", "soccer_uefa_european_championship", "soccer_conmebol_copa_america", "soccer_afc_asian_cup", "soccer_caf_africa_cup_of_nations", "soccer_concacaf_gold_cup", "soccer_international_friendlies", "soccer_uefa_euro_qualification", "soccer_concacaf_nations_league"}
        or re.search(r"national teams|international friendlies|euro(?:pean championship)? qualif|world cup qualif|nations league|africa cup of nations|asian cup|gold cup|copa am[eé]rica|european championship", title)
    )


def _senior_team(results, name):
    wanted = norm_name(name)
    matches = [r.get("team", {}) for r in results if r.get("team", {}).get("national") is True]
    matches = [t for t in matches if not re.search(r"\b(u\s?\d{2}|under\s?\d{2}|women|womens|olympic)\b", t.get("name", ""), re.I)]
    exact = [t for t in matches if norm_name(t.get("name", "")) == wanted]
    if len(exact) != 1:
        raise ValueError(f"성인 국가대표팀 이름을 확정할 수 없습니다: {name}")
    return exact[0]


def build_national_event_pool(api, home, away, cutoff_iso, cache=None, recent_n=6):
    """Fetch each senior side's recent fixtures across friendlies, qualifiers and cups."""
    cache = cache if cache is not None else {}
    cutoff = datetime.fromisoformat(str(cutoff_iso).replace("Z", "+00:00")).timestamp()
    names = {}
    all_fixtures = {}
    counts = {}
    for name in (home, away):
        normalized = norm_name(name)
        if normalized not in cache:
            results = api.search_team(name)
            try:
                team = _senior_team(results, name)
            except ValueError:
                # Odds providers often use USA/South Korea while API-Football
                # indexes United States/Korea Republic.
                if normalized == str(name).lower().strip():
                    raise
                team = _senior_team(api.search_team(normalized), name)
            # A single team query spans all competitions; share it across events in this run.
            cache[normalized] = (team, api.team_recent_fixtures(team["id"], last=20, cutoff_iso=cutoff_iso))
        team, fixtures = cache[normalized]
        names[normalized] = team["name"]
        valid = []
        for fx in fixtures:
            f = fx.get("fixture", {})
            goals = fx.get("goals", {})
            ts = int(f.get("timestamp") or 0)
            if f.get("status", {}).get("short") not in {"FT", "AET", "PEN"} or not ts or ts >= cutoff:
                continue
            if goals.get("home") is None or goals.get("away") is None:
                continue
            # Omit matches older than three years; stale national sides change too much.
            if cutoff - ts > 3 * 366 * 86400:
                continue
            valid.append(fx)
        valid.sort(key=lambda fx: int(fx["fixture"]["timestamp"]), reverse=True)
        counts[name] = len(valid)
        for fx in valid[:max(10, int(recent_n) * 2)]:
            fid = fx.get("fixture", {}).get("id")
            all_fixtures[fid if fid is not None else (fx["fixture"]["timestamp"], fx["teams"]["home"]["name"])] = fx
    if min(counts.values()) < 3:
        raise ValueError(f"최근 3년 완료 A매치가 부족합니다: {counts}")
    return {"fixtures": list(all_fixtures.values()), "team_names": names,
            "sample_counts": counts, "international": True}
