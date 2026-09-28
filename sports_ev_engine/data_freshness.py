"""Freshness dashboard based on the engine's own last fetch/check times.

These ages describe when *our engine last fetched/confirmed* a source, not the
publisher's underlying data-release timestamp.
"""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
from .prediction_store import load_predictions, load_market_observations


def _age(ts,now):
    x=pd.to_datetime(ts,utc=True,errors="coerce")
    if pd.isna(x): return None
    return max(0.0,(pd.Timestamp(now)-x).total_seconds()/60)


def _grade(age,fresh,stale):
    if age is None:return "MISSING"
    return "✓" if age<=fresh else "△" if age<=stale else "⚠"


def freshness_rows(now=None):
    now=now or datetime.now(timezone.utc)
    preds=load_predictions(); obs=load_market_observations()
    latest={}
    for r in preds:
        eid=str(r.get("event_id") or ""); key=(eid,str(r.get("sport_key") or ""))
        if key not in latest or str(r.get("recorded_at") or "")>str(latest[key].get("recorded_at") or ""): latest[key]=r
    mobs={}
    for r in obs:
        key=(str(r.get("event_id") or ""),str(r.get("sport_key") or ""))
        if key not in mobs or str(r.get("observed_at") or "")>str(mobs[key].get("observed_at") or ""): mobs[key]=r
    rows=[]
    for key,r in latest.items():
        kickoff=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        if pd.isna(kickoff) or kickoff < pd.Timestamp(now)-pd.Timedelta(hours=4): continue
        analysis_age=_age(r.get("recorded_at"),now); market_age=_age((mobs.get(key) or {}).get("observed_at"),now)
        lineup_age=analysis_age if bool(r.get("lineup_confirmed")) else None
        statcast_age=analysis_age if bool(r.get("statcast_quality_used")) else None
        weather_age=analysis_age if bool(r.get("weather_used")) else None
        news_age=analysis_age if bool(r.get("availability_news_used")) or bool(r.get("news_scan_used")) else None
        ages=[x for x in [market_age,analysis_age,lineup_age] if x is not None]
        worst=max(ages) if ages else None
        rows.append({
            "경기":f"{r.get('away_team')} @ {r.get('home_team')}","sport_family":r.get("sport_family"),"commence_time":r.get("commence_time"),
            "배당":f"{_grade(market_age,10,30)} {market_age:.0f}분" if market_age is not None else "MISSING",
            "모델":f"{_grade(analysis_age,20,90)} {analysis_age:.0f}분" if analysis_age is not None else "MISSING",
            "라인업":f"{_grade(lineup_age,30,90)} {lineup_age:.0f}분" if lineup_age is not None else "미확정",
            "Statcast":f"{_grade(statcast_age,120,360)} {statcast_age:.0f}분" if statcast_age is not None else "MISSING",
            "날씨":f"{_grade(weather_age,60,180)} {weather_age:.0f}분" if weather_age is not None else "MISSING",
            "부상/뉴스":f"{_grade(news_age,180,720)} {news_age:.0f}분" if news_age is not None else "MISSING",
            "freshness_risk":"HIGH" if worst is not None and worst>90 else "MEDIUM" if worst is not None and worst>30 else "LOW",
        })
    return rows
