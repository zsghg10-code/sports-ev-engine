"""Dynamic senior international fixture/odds discovery.

The module deliberately separates fixture discovery from price availability:
- API-Football /fixtures?date finds senior national-team fixtures across competitions.
- API-Football /odds is used only as a fallback when The Odds API has no matching event.
- Missing prices stay missing; fixtures are never assigned invented odds.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from sports_ev_engine.models.soccer_auto import norm_name

PROVIDER_BUILD = "3.0.0"

_REJECT = re.compile(
    r"\b(women|women's|womens|female|u[- ]?\d{2}|under[- ]?\d{2}|youth|olympic|reserve|club world cup|clubs?|champions league|europa league|conference league|libertadores|sudamericana)\b",
    re.I,
)
_INCLUDE = re.compile(
    r"(?:international\s+friend|friendlies|friendly|nations\s+league|nations\s+cup|cup\s+of\s+nations|africa\s+cup|african\s+nations|asian\s+cup|gold\s+cup|gulf\s+cup|arab\s+cup|copa\s+am[eé]rica|world\s+cup|euro(?:pean)?(?:\s+championship)?|qualif(?:ier|ication)|afcon|concacaf|conmebol|uefa\s+nations|eaff|saff|waff|aff\s+championship|cosafa|cecafa|ofc\s+nations)",
    re.I,
)


def looks_senior_international_competition(name: str, country: str = "") -> bool:
    text = f"{name or ''} {country or ''}".strip()
    if not text or _REJECT.search(text):
        return False
    return bool(_INCLUDE.search(text))


def kst_target_dates(date_only: bool, selected_date: date, scope: str | None, today: date) -> list[date]:
    if date_only:
        return [selected_date]
    scope = str(scope or "앞으로 3일")
    if "7" in scope:
        n = 7
    elif "오늘" in scope:
        n = 1
    elif "전체" in scope:
        n = 7  # bound API discovery; odds providers expose upcoming windows anyway
    else:
        n = 3
    return [today + timedelta(days=i) for i in range(n)]


def api_query_dates_for_kst_dates(kst_dates: Iterable[date]) -> list[str]:
    """KST calendar day overlaps the preceding UTC calendar day and itself."""
    ds = set()
    for d in kst_dates:
        ds.add((d - timedelta(days=1)).isoformat())
        ds.add(d.isoformat())
    return sorted(ds)


def _fixture_kst_date(fx) -> date | None:
    try:
        ts = pd.Timestamp((fx.get("fixture") or {}).get("date"))
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert("Asia/Seoul").date()
    except Exception:
        return None


def discover_api_football_fixtures(api, kst_dates: Iterable[date]):
    """Return senior international fixtures on the requested KST dates plus diagnostics."""
    wanted = set(kst_dates)
    out = {}
    diagnostics = []
    for day in api_query_dates_for_kst_dates(wanted):
        try:
            rows = api.fixtures_by_date(day)
            diagnostics.append({"소스": f"API-Football fixtures {day}", "상태": "수집 완료", "건수": len(rows)})
        except Exception as exc:
            diagnostics.append({"소스": f"API-Football fixtures {day}", "상태": "수집 실패", "이유": str(exc)})
            continue
        for fx in rows:
            league = fx.get("league") or {}
            if not looks_senior_international_competition(league.get("name", ""), league.get("country", "")):
                continue
            kd = _fixture_kst_date(fx)
            if kd not in wanted:
                continue
            fixture = fx.get("fixture") or {}
            fid = fixture.get("id")
            teams = fx.get("teams") or {}
            if fid is None or not (teams.get("home") or {}).get("name") or not (teams.get("away") or {}).get("name"):
                continue
            out[int(fid)] = fx
    return list(out.values()), diagnostics


def _odd_float(value):
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if 1.0 < x <= 50 else None


def _point(value):
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None


def _market_rows_for_bet(bet_name, values, home, away):
    name = str(bet_name or "").strip().lower()
    rows = []
    if name in {"match winner", "1x2", "winner", "fulltime result", "full time result"} or "match winner" in name:
        for v in values or []:
            raw = str(v.get("value") or v.get("name") or "").strip()
            key = raw.lower()
            if key in {"home", "1"} or norm_name(raw) == norm_name(home):
                sel = home
            elif key in {"draw", "x"}:
                sel = "Draw"
            elif key in {"away", "2"} or norm_name(raw) == norm_name(away):
                sel = away
            else:
                continue
            odd = _odd_float(v.get("odd") or v.get("odds") or v.get("price"))
            if odd:
                rows.append(("h2h", sel, None, odd))
        return rows

    if "over/under" in name or "goals over" in name or name in {"goals", "total goals"}:
        for v in values or []:
            raw = str(v.get("value") or v.get("name") or "").strip()
            m = re.search(r"\b(over|under)\s*([0-9]+(?:\.[0-9]+)?)", raw, re.I)
            if not m:
                continue
            odd = _odd_float(v.get("odd") or v.get("odds") or v.get("price"))
            if odd:
                rows.append(("totals", m.group(1).title(), float(m.group(2)), odd))
        return rows

    if "asian handicap" in name or name in {"handicap result", "handicap"}:
        for v in values or []:
            raw = str(v.get("value") or v.get("name") or "").strip()
            low = raw.lower()
            if low.startswith("home") or norm_name(home) in norm_name(raw):
                sel = home
            elif low.startswith("away") or norm_name(away) in norm_name(raw):
                sel = away
            else:
                continue
            p = _point(raw)
            odd = _odd_float(v.get("odd") or v.get("odds") or v.get("price"))
            if odd is not None and p is not None:
                rows.append(("spreads", sel, p, odd))
    return rows


def flatten_api_football_odds(odds_payload, fixture_map: dict[int, dict], markets=("h2h", "spreads", "totals")) -> pd.DataFrame:
    allowed = set(markets)
    rows = []
    for item in odds_payload or []:
        fixture_obj = item.get("fixture") or {}
        fid = fixture_obj.get("id")
        try:
            fid = int(fid)
        except (TypeError, ValueError):
            continue
        fx = fixture_map.get(fid)
        if not fx:
            continue
        teams = fx.get("teams") or {}
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")
        commence = (fx.get("fixture") or {}).get("date")
        if not (home and away and commence):
            continue
        for bm in item.get("bookmakers") or []:
            bname = bm.get("name") or f"API-Football {bm.get('id','book')}"
            bkey = f"api_football_{bm.get('id','book')}"
            for bet in bm.get("bets") or []:
                for market, selection, point, odd in _market_rows_for_bet(bet.get("name"), bet.get("values"), home, away):
                    if market not in allowed:
                        continue
                    rows.append({
                        "event_id": f"af:{fid}",
                        "commence_time": commence,
                        "home_team": home,
                        "away_team": away,
                        "bookmaker": bname,
                        "bookmaker_key": bkey,
                        "market": market,
                        "selection": selection,
                        "odds": float(odd),
                        "point": point,
                        "odds_source": "API-Football",
                    })
    return pd.DataFrame(rows)


def _same_event(a, b, max_minutes=120):
    try:
        if norm_name(a["home_team"]) != norm_name(b["home_team"]) or norm_name(a["away_team"]) != norm_name(b["away_team"]):
            return False
        ta, tb = pd.Timestamp(a["commence_time"]), pd.Timestamp(b["commence_time"])
        if ta.tzinfo is None:
            ta = ta.tz_localize("UTC")
        if tb.tzinfo is None:
            tb = tb.tz_localize("UTC")
        return abs((ta - tb).total_seconds()) <= max_minutes * 60
    except Exception:
        return False


def append_only_missing_events(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    """Prefer primary provider for a fixture; append fallback only if fixture is absent."""
    if fallback is None or fallback.empty:
        return primary.copy() if isinstance(primary, pd.DataFrame) else pd.DataFrame()
    if primary is None or primary.empty:
        return fallback.copy()
    keep = []
    prim_events = [g.iloc[0] for _, g in primary.groupby("event_id")]
    for eid, g in fallback.groupby("event_id"):
        probe = g.iloc[0]
        if not any(_same_event(probe, p) for p in prim_events):
            keep.append(g)
    if not keep:
        return primary.copy()
    return pd.concat([primary, *keep], ignore_index=True)


def competition_rows(fixtures, odds_flat=None):
    odds_events=[]
    if isinstance(odds_flat, pd.DataFrame) and not odds_flat.empty:
        odds_events=[g.iloc[0] for _,g in odds_flat.groupby("event_id")]
    groups = {}
    for fx in fixtures or []:
        league = fx.get("league") or {}
        name = league.get("name") or "Unknown"
        teams=fx.get("teams") or {}; f=fx.get("fixture") or {}
        probe={"home_team":(teams.get("home") or {}).get("name"),"away_team":(teams.get("away") or {}).get("name"),"commence_time":f.get("date")}
        d = groups.setdefault(name, {"대회": name, "경기수": 0, "배당확보 경기": 0, "상태": "일정 활성"})
        d["경기수"] += 1
        if any(_same_event(probe,o) for o in odds_events):
            d["배당확보 경기"] += 1
    for d in groups.values():
        if d["배당확보 경기"] == d["경기수"] and d["경기수"]:
            d["상태"] = "완전 활성"
        elif d["배당확보 경기"]:
            d["상태"] = "부분 활성"
        else:
            d["상태"] = "일정 활성 · 배당 없음"
    return sorted(groups.values(), key=lambda x: (x["상태"] != "완전 활성", x["대회"]))
