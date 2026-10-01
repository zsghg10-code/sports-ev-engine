"""Auditable source-health/fallback summary.

Health is based on what the engine actually fetched and stored.  It does not
claim a provider is globally healthy when we have not checked it.
"""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
from .prediction_store import load_predictions, load_market_observations


def _age(ts, now):
    x=pd.to_datetime(ts,utc=True,errors="coerce")
    if pd.isna(x): return None
    return max(0.0,(pd.Timestamp(now)-x).total_seconds()/60)


def _latest(rows,keyfn,timefield):
    out={}
    for r in rows:
        k=keyfn(r); t=str(r.get(timefield) or "")
        if k not in out or t>str(out[k].get(timefield) or ""): out[k]=r
    return out


def source_health_rows(now=None):
    now=now or datetime.now(timezone.utc)
    preds=_latest(load_predictions(),lambda r:(str(r.get("event_id") or ""),str(r.get("sport_key") or "")),"recorded_at")
    obs=_latest(load_market_observations(),lambda r:(str(r.get("event_id") or ""),str(r.get("sport_key") or "")),"observed_at")
    rows=[]
    for key,r in preds.items():
        kick=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        if pd.isna(kick) or kick < pd.Timestamp(now)-pd.Timedelta(hours=6): continue
        market_age=_age((obs.get(key) or {}).get("observed_at"),now)
        analysis_age=_age(r.get("recorded_at"),now)
        lineup_status=str(r.get("lineup_status") or ("CONFIRMED" if r.get("lineup_confirmed") else "PRE-LINEUP"))
        lineup_source=str(r.get("lineup_source") or "—")
        fallback=bool(r.get("lineup_fallback_used")) or "fallback" in lineup_source.lower()
        sources=[
            ("배당","OK" if market_age is not None and market_age<=30 else "STALE" if market_age is not None else "MISSING", f"{market_age:.0f}분 전" if market_age is not None else "미관측","The Odds API"),
            ("모델","OK" if analysis_age is not None and analysis_age<=90 else "STALE",f"{analysis_age:.0f}분 전" if analysis_age is not None else "—","engine snapshot"),
            ("라인업","OK" if lineup_status=="CONFIRMED" else "FALLBACK" if lineup_status=="PROBABLE" or fallback else "WAIT",lineup_status,lineup_source),
        ]
        if str(r.get("sport_family") or "").startswith("baseball"):
            sources.extend([
                ("Statcast","OK" if r.get("statcast_quality_used") else "FALLBACK" if r.get("statcast_fallback_used") else "MISSING","Savant 사용" if r.get("statcast_quality_used") else "MLB Stats API PBP fallback" if r.get("statcast_fallback_used") else "미수집","Baseball Savant → MLB Stats API fallback"),
                ("불펜 exact","OK" if r.get("bullpen_exact_used") else "MISSING","사용" if r.get("bullpen_exact_used") else "미수집","MLB/KBO/NPB context"),
            ])
        else:
            sources.extend([
                ("xG","FALLBACK" if r.get("xg_fallback_used") else "PARTIAL" if r.get("xg_partial") or ((r.get('xg_samples_home') or 0) or (r.get('xg_samples_away') or 0)) and "recent_xg:MISSING" in str(r.get("signal_summary") or "") else "OK" if "recent_xg:MISSING" not in str(r.get("signal_summary") or "") else "MISSING",
                 f"홈 {r.get('xg_samples_home') or 0}/3 · 원정 {r.get('xg_samples_away') or 0}/3",
                 r.get("xg_source") or r.get("xg_fallback_source") or r.get("xg_sources_tried") or "API-Football → ESPN → FotMob → SofaScore"),
                ("부상/결장","OK" if "injuries_player_impact:MISSING" not in str(r.get("signal_summary") or "") else "MISSING","신호 ledger","API-Football injuries"),
            ])
        for name,status,detail,source in sources:
            fb=(fallback and name=="라인업") or (name=="Statcast" and bool(r.get("statcast_fallback_used"))) or (name=="xG" and bool(r.get("xg_fallback_used")))
            rows.append({"event_id":r.get("event_id"),"경기":f"{r.get('away_team')} @ {r.get('home_team')}","sport_family":r.get("sport_family"),
                         "소스항목":name,"상태":status,"상세":detail,"사용소스":source,"fallback":"YES" if fb else "NO"})
    return rows
