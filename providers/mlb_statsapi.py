from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import math
import re

import pandas as pd
import requests

BASE="https://statsapi.mlb.com/api/v1"
KST=ZoneInfo("Asia/Seoul")
UTC=ZoneInfo("UTC")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _iso_dt(v):
    try:
        return pd.Timestamp(v).tz_convert("UTC") if pd.Timestamp(v).tzinfo else pd.Timestamp(v).tz_localize("UTC")
    except Exception:
        return pd.NaT


def _schedule_rows(payload):
    rows=[]
    for d in (payload or {}).get("dates",[]):
        for g in d.get("games",[]):
            away=g.get("teams",{}).get("away",{})
            home=g.get("teams",{}).get("home",{})
            ap=away.get("probablePitcher") or {}
            hp=home.get("probablePitcher") or {}
            venue=g.get("venue") or {}
            rows.append({
                "gamePk":g.get("gamePk"),
                "gameDate":g.get("gameDate"),
                "commence_time":g.get("gameDate"),
                "away":(away.get("team") or {}).get("name"),
                "home":(home.get("team") or {}).get("name"),
                "away_team_id":(away.get("team") or {}).get("id"),
                "home_team_id":(home.get("team") or {}).get("id"),
                "away_probable":ap.get("fullName"),
                "home_probable":hp.get("fullName"),
                "away_probable_id":ap.get("id"),
                "home_probable_id":hp.get("id"),
                "venue_id":venue.get("id"),
                "venue":venue.get("name"),
                "status":(g.get("status") or {}).get("detailedState"),
                "abstract_status":(g.get("status") or {}).get("abstractGameState"),
            })
    return rows


def _filter_kst_rows(rows, selected_date):
    frame=pd.DataFrame(rows)
    if frame.empty:
        return frame
    ts=pd.to_datetime(frame["gameDate"],utc=True,errors="coerce").dt.tz_convert(KST)
    frame=frame.loc[ts.dt.date==selected_date].copy()
    frame["gameDateKST"]=ts.loc[frame.index].dt.strftime("%Y-%m-%d %H:%M KST")
    return frame.sort_values("gameDate").reset_index(drop=True)


def schedule(date_str):
    """Backward compatible MLB calendar-date lookup (MLB schedule date semantics)."""
    r=requests.get(
        f"{BASE}/schedule",
        params={"sportId":1,"date":date_str,"hydrate":"probablePitcher,team,venue"},
        timeout=30,
    )
    r.raise_for_status()
    return pd.DataFrame(_schedule_rows(r.json()))


def schedule_kst(selected_date):
    """Return only games whose first pitch falls on selected_date in Asia/Seoul.

    MLB's `date=` parameter is a baseball calendar date, not a KST day.  Querying
    the neighboring dates then filtering by the UTC gameDate prevents the common
    bug where a KST morning slate is omitted and the following KST day is shown.
    """
    if isinstance(selected_date,str):
        selected_date=pd.Timestamp(selected_date).date()
    elif isinstance(selected_date,pd.Timestamp):
        selected_date=selected_date.date()
    if not isinstance(selected_date,date):
        raise ValueError("selected_date must be a date")
    rows=[]
    for delta in (-1,0,1):
        d=(selected_date+timedelta(days=delta)).isoformat()
        r=requests.get(
            f"{BASE}/schedule",
            params={"sportId":1,"date":d,"hydrate":"probablePitcher,team,venue"},
            timeout=30,
        )
        r.raise_for_status()
        rows.extend(_schedule_rows(r.json()))
    # dedupe because adjacent-date queries can occasionally overlap on postponed/rescheduled games
    seen=set(); unique=[]
    for row in rows:
        key=row.get("gamePk") or (row.get("away"),row.get("home"),row.get("gameDate"))
        if key in seen:continue
        seen.add(key); unique.append(row)
    return _filter_kst_rows(unique,selected_date)


def match_schedule(frame, home, away, commence_time=None):
    if frame is None or frame.empty:
        return None
    h=_norm(home); a=_norm(away)
    cand=frame[(frame["home"].map(_norm)==h)&(frame["away"].map(_norm)==a)].copy()
    if cand.empty:
        # Fallback for occasional abbreviation/punctuation differences.
        cand=frame[
            frame["home"].map(lambda x: h in _norm(x) or _norm(x) in h) &
            frame["away"].map(lambda x: a in _norm(x) or _norm(x) in a)
        ].copy()
    if cand.empty:return None
    if commence_time is not None and len(cand)>1:
        target=pd.Timestamp(commence_time)
        target=target.tz_convert("UTC") if target.tzinfo else target.tz_localize("UTC")
        dt=pd.to_datetime(cand["gameDate"],utc=True,errors="coerce")
        cand=cand.assign(_diff=(dt-target).abs().dt.total_seconds()).sort_values("_diff")
    return cand.iloc[0].to_dict()
