"""One-cycle cross-sport smart refresh for v3.2.

Purpose
-------
* poll current h2h/spreads/totals for sports that have saved upcoming predictions,
* append market observations for CLV,
* trigger a full re-analysis when price/line or lineup/starter state changes,
* save the new immutable prediction snapshot so Daily Best Combo updates naturally,
* settle finished events every cycle.

The module is intentionally fail-soft: one sport/provider failure never blocks the
remaining sports.  It reuses the same model/provider functions as the Streamlit
analysis tabs rather than inventing a second probability model.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .providers.the_odds_api import TheOddsAPI
from .providers.api_football import APIFootball
from .providers.official_baseball import OfficialBaseballStats
from .providers.live_baseball import LiveBaseballContext
from .providers.baseball_advanced import AdvancedBaseballSignals
from .providers.mlb_context import MLBContextProvider
from .providers.mlb_statsapi import schedule_kst, match_schedule
from .market import clean_odds, consensus
from .competition_form import build_competition_pool
from .auto_soccer import analyze_event
from .national_soccer import build_national_event_pool
from .deep_soccer_context import collect_deep_context
from .auto_national import collect as collect_automatic, automatic_pool, fetch_lineup
from .models.soccer_auto import norm_name
from .official_baseball_model import analyze_official_event
from .prediction_store import (
    load_predictions, load_market_observations, record_market_frame, record_frame,
    record_refresh_event, pending_sport_keys, auto_settle,
)
from .providers.mlb_postgame import analyze_settled_mlb
from .monitoring import reanalysis_threshold_pp, minutes_until, scheduled_interval_minutes

KST = ZoneInfo("Asia/Seoul")
DEFAULT_STATE = "data/smart_refresh_state.json"


def _load_state(path=DEFAULT_STATE):
    p=Path(path)
    if not p.exists():return {"events":{},"last_run":None}
    try:
        x=json.loads(p.read_text(encoding="utf-8"))
        return x if isinstance(x,dict) else {"events":{},"last_run":None}
    except Exception:return {"events":{},"last_run":None}


def _save_state(state,path=DEFAULT_STATE):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    tmp.replace(p)


def _point(v):
    try:
        if v is None or pd.isna(v):return None
    except Exception:pass
    try:return round(float(v),4)
    except Exception:return None


def _base_key(r):
    return (str(r.get("event_id") or ""),str(r.get("market") or ""),str(r.get("selection") or ""))


def _exact_key(r):
    return (*_base_key(r),_point(r.get("point")))


def _latest_previous(observations,sport_key,event_id):
    rows=[x for x in observations if str(x.get("sport_key") or "")==str(sport_key) and str(x.get("event_id") or "")==str(event_id)]
    rows.sort(key=lambda x:str(x.get("observed_at") or ""))
    exact={};base={}
    for x in rows:
        exact[_exact_key(x)]=x;base[_base_key(x)]=x
    return exact,base


def market_trigger_reasons(current_event: pd.DataFrame, previous_observations: list[dict], *, now=None, sport_key=""):
    """Pure trigger logic used by tests and the worker."""
    now=now or datetime.now(timezone.utc)
    if current_event is None or current_event.empty:return []
    eid=str(current_event.iloc[0].get("event_id") or "")
    exact,base=_latest_previous(previous_observations,sport_key,eid)
    reasons=[]
    mins=minutes_until(current_event.iloc[0].get("commence_time"),now)
    threshold=reanalysis_threshold_pp(mins)
    for _,row in current_event.iterrows():
        d=row.to_dict();prev=exact.get(_exact_key(d))
        if prev and prev.get("consensus_prob") is not None and d.get("consensus_prob") is not None:
            move=(float(d["consensus_prob"])-float(prev["consensus_prob"]))*100
            if abs(move)>=max(4.0,threshold):
                reasons.append(f"시장확률 강이동 {move:+.1f}%p")
            elif abs(move)>=threshold:
                reasons.append(f"시장확률 이동 {move:+.1f}%p")
        oldbase=base.get(_base_key(d))
        if oldbase:
            a=_point(oldbase.get("point"));b=_point(d.get("point"))
            if a is not None and b is not None and abs(b-a)>=.25:
                reasons.append(f"라인 이동 {a:g}→{b:g}")
    return list(dict.fromkeys(reasons))


def _hash_stage(obj):
    raw=json.dumps(obj,ensure_ascii=False,sort_keys=True,default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _latest_upcoming_predictions(now=None,horizon_hours=72):
    now=pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:now=now.tz_localize("UTC")
    end=now+pd.Timedelta(hours=horizon_hours)
    rows=[]
    for r in load_predictions():
        ts=pd.to_datetime(r.get("commence_time"),utc=True,errors="coerce")
        if pd.isna(ts) or not (now < ts <= end):continue
        rows.append(r)
    rows.sort(key=lambda r:str(r.get("recorded_at") or ""))
    latest={}
    for r in rows:
        latest[(str(r.get("sport_key") or ""),str(r.get("event_id") or ""))]=r
    return list(latest.values())


def _sport_titles(api):
    try:return {str(x.get("key")):str(x.get("title") or x.get("key")) for x in api.sports(all_sports=True)}
    except Exception:return {}


def _reanalyze_mlb(g,snapshot,region):
    kickoff=g.iloc[0]["commence_time"];home=g.iloc[0]["home_team"];away=g.iloc[0]["away_team"]
    d=pd.Timestamp(kickoff).tz_convert(KST).date()
    sched=match_schedule(schedule_kst(d),home,away,kickoff)
    provider=MLBContextProvider()
    stats,ctx=provider.collect(home,away,kickoff,schedule_row=sched,recent_n=10,deep=True,market_frame=g,event_id=g.iloc[0]["event_id"])
    analyzed,meta=analyze_official_event(g,stats,"MLB",ctx) if stats else (pd.DataFrame(),{"status":"data_failed"})
    sig=_hash_stage({"stage":ctx.get("stage"),"hs":ctx.get("home_starter"),"as":ctx.get("away_starter"),"hl":ctx.get("home_lineup"),"al":ctx.get("away_lineup")})
    return analyzed,sig,meta


def _reanalyze_kbo_npb(g,snapshot,region):
    family=str(snapshot.get("sport_family") or "")
    league="KBO" if "kbo" in family else "NPB"
    kickoff=g.iloc[0]["commence_time"];home=g.iloc[0]["home_team"];away=g.iloc[0]["away_team"]
    stats=OfficialBaseballStats().load(league,pd.Timestamp(kickoff).year)
    ctx=LiveBaseballContext().context(league,home,away,kickoff)
    try:ctx["advanced"]=AdvancedBaseballSignals().collect(league,home,away,kickoff,ctx,recent_n=10)
    except Exception as exc:
        ctx["advanced"]={"advanced_used":0,"advanced_total":7,"advanced_completeness":0.0,"extra_uncertainty_pp":2.1,"statuses":{},"notes":[str(exc)]}
    analyzed,meta=analyze_official_event(g,stats,league,ctx)
    sig=_hash_stage({"stage":ctx.get("stage"),"hs":ctx.get("home_starter"),"as":ctx.get("away_starter"),"hl":ctx.get("home_lineup"),"al":ctx.get("away_lineup")})
    return analyzed,sig,meta


def _reanalyze_club(g,snapshot,football_key,title,region):
    if not football_key:return pd.DataFrame(),None,{"status":"missing_api_football"}
    kickoff=g.iloc[0]["commence_time"];home=g.iloc[0]["home_team"];away=g.iloc[0]["away_team"]
    foot=APIFootball(football_key)
    pool=build_competition_pool(foot,title,pd.Timestamp(kickoff).year)
    ep=dict(pool)
    try:ep["event_context"]=collect_deep_context(foot,pool,home,away,kickoff,season=(pool.get("seasons") or [pd.Timestamp(kickoff).year])[0],horizon_hours=24)
    except Exception as exc:ep["event_context"]={"deep_context_attempted":True,"deep_context_reason":str(exc)}
    analyzed,meta=analyze_event(g,ep,recent_n=6)
    sig=_hash_stage(ep.get("event_context") or {})
    return analyzed,sig,meta


def _reanalyze_national(g,snapshot,football_key,sport_key,region):
    kickoff=g.iloc[0]["commence_time"];home=g.iloc[0]["home_team"];away=g.iloc[0]["away_team"]
    if football_key:
        foot=APIFootball(football_key);cache={}
        pool=build_national_event_pool(foot,home,away,kickoff,cache,6);pool["manual_context"]={}
        try:pool["event_context"]=collect_deep_context(foot,pool,home,away,kickoff,season=pd.Timestamp(kickoff).year,horizon_hours=24)
        except Exception as exc:pool["event_context"]={"deep_context_attempted":True,"deep_context_reason":str(exc)}
        analyzed,meta=analyze_event(g,pool,recent_n=6)
        return analyzed,_hash_stage(pool.get("event_context") or {}),meta
    records,events,diagnostics=collect_automatic([sport_key])
    pool=automatic_pool(records,events,home,away,kickoff,6);pool["manual_context"]={};matched=None
    for e in events:
        if norm_name(e.get("home"))==norm_name(home) and norm_name(e.get("away"))==norm_name(away):
            try:
                if pd.Timestamp(e.get("kickoff"))==pd.Timestamp(kickoff):matched=e;break
            except Exception:pass
    if matched:
        try:pool["manual_context"]=fetch_lineup(matched)
        except Exception:pass
    pool["event_context"]={"deep_context_attempted":False,"deep_context_reason":"public-source mode"}
    analyzed,meta=analyze_event(g,pool,recent_n=6)
    return analyzed,_hash_stage(pool.get("manual_context") or {}),meta


def run_smart_cycle(odds_key,football_key=None,*,region="eu",state_path=DEFAULT_STATE,horizon_hours=72,min_books=2,reserve_credits=2000,now=None):
    now=now or datetime.now(timezone.utc)
    api=TheOddsAPI(odds_key);state=_load_state(state_path);state.setdefault("events",{});state.setdefault("sports",{})
    month_key=pd.Timestamp(now).strftime("%Y-%m")
    if state.get("credit_month")!=month_key:
        state["credit_month"]=month_key;state["credits_remaining"]=None
    predictions=_latest_upcoming_predictions(now,horizon_hours)
    titles=_sport_titles(api);previous_obs=load_market_observations()
    by_sport={}
    for s in predictions:
        sk=str(s.get("sport_key") or "")
        if sk:by_sport.setdefault(sk,[]).append(s)
    result={"checked_at":now.isoformat(),"sports":{},"reanalyzed":0,"observations":0,"settled":0,"errors":[]}
    for sk,snaps in by_sport.items():
        try:
            sport_region=str((snaps[-1].get("odds_region") if snaps else None) or region)
            positive=[minutes_until(x.get("commence_time"),now) for x in snaps if x.get("commence_time")]
            positive=[x for x in positive if x>0]
            nearest=min(positive) if positive else 999999
            interval=scheduled_interval_minutes(nearest)
            ss=state["sports"].setdefault(sk,{})
            last=pd.to_datetime(ss.get("last_market_check"),utc=True,errors="coerce")
            elapsed=999999 if pd.isna(last) else (pd.Timestamp(now)-last).total_seconds()/60
            if elapsed < interval:
                result["sports"][sk]={"events":len(snaps),"reanalyzed":0,"reasons":[],"skipped":f"next market poll in {max(0,interval-elapsed):.0f}m"}
                continue
            rem=state.get("credits_remaining")
            if rem is not None and int(rem)<=int(reserve_credits):
                result["sports"][sk]={"events":len(snaps),"reanalyzed":0,"reasons":[],"skipped":"Odds API reserve protected"}
                continue
            events,headers=api.odds(sk,sport_region,"h2h,spreads,totals")
            ss["last_market_check"]=now.isoformat();ss["poll_interval_min"]=interval
            hr=headers.get("x-requests-remaining")
            if hr is not None:
                try:state["credits_remaining"]=int(hr)
                except Exception:state["credits_remaining"]=hr
            raw=clean_odds(api.flatten(events));market=consensus(raw,min_books=min_books) if not raw.empty else pd.DataFrame()
            if not market.empty:
                market["sport_key"]=sk
                result["observations"]+=record_market_frame(market,sport_key=sk,source="smart_worker",observed_at=now.isoformat())
            sport_info={"events":0,"reanalyzed":0,"reasons":[]};result["sports"][sk]=sport_info
            for snap in snaps:
                eid=str(snap.get("event_id") or "");g=market[market["event_id"].astype(str)==eid].copy() if not market.empty else pd.DataFrame()
                if g.empty:continue
                sport_info["events"]+=1
                reasons=market_trigger_reasons(g,previous_obs,now=now,sport_key=sk)
                mins=minutes_until(g.iloc[0]["commence_time"],now)
                es=state["events"].setdefault(f"{sk}|{eid}",{})
                # Check lineup/starter state every 15 minutes inside 150m even with no price move.
                last_check=pd.to_datetime(es.get("last_context_check"),utc=True,errors="coerce")
                context_due=0<mins<=150 and (pd.isna(last_check) or (pd.Timestamp(now)-last_check).total_seconds()>=900)
                if not reasons and not context_due:continue
                family=str(snap.get("sport_family") or "")
                try:
                    if family=="baseball_mlb":analyzed,sig,meta=_reanalyze_mlb(g,snap,sport_region)
                    elif family in {"baseball_kbo","baseball_npb"}:analyzed,sig,meta=_reanalyze_kbo_npb(g,snap,sport_region)
                    elif family=="soccer_club":analyzed,sig,meta=_reanalyze_club(g,snap,football_key,titles.get(sk,sk),sport_region)
                    elif family=="soccer_national":analyzed,sig,meta=_reanalyze_national(g,snap,football_key,sk,sport_region)
                    else:continue
                    es["last_context_check"]=now.isoformat()
                    old_sig=es.get("stage_signature")
                    stage_changed=bool(sig and old_sig and sig!=old_sig)
                    first_sig=bool(sig and not old_sig)
                    if sig:es["stage_signature"]=sig
                    if stage_changed:reasons.append("라인업/선발/가용성 변화")
                    if analyzed is not None and not analyzed.empty and (reasons or first_sig):
                        analyzed["sport_key"]=sk;analyzed["odds_region"]=sport_region
                        saved=record_frame(analyzed,sport_key=sk,sport_family=family)
                        result["reanalyzed"]+=int(saved>0);sport_info["reanalyzed"]+=int(saved>0)
                        reason_text=" / ".join(reasons or ["초기 자동 컨텍스트 확인"])
                        record_refresh_event({"event_id":eid,"sport_key":sk,"sport_family":family,"commence_time":g.iloc[0]["commence_time"],"home_team":g.iloc[0]["home_team"],"away_team":g.iloc[0]["away_team"],"reason":reason_text,"saved_snapshots":saved})
                        sport_info["reasons"].append({"event_id":eid,"reason":reason_text,"saved":saved})
                except Exception as exc:
                    result["errors"].append(f"{sk}/{eid}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            result["errors"].append(f"{sk}: {type(exc).__name__}: {exc}")
    last_settle=pd.to_datetime(state.get("last_settlement"),utc=True,errors="coerce")
    settlement_due=pd.isna(last_settle) or (pd.Timestamp(now)-last_settle).total_seconds()>=3600
    if settlement_due:
        for sk in pending_sport_keys():
            info=auto_settle(api,sk);result["settled"]+=info.get("settled",0)
            if info.get("error"):result["errors"].append(f"settle {sk}: {info['error']}")
        try:result["mlb_postgame"]=analyze_settled_mlb()
        except Exception as exc:result["errors"].append(f"MLB postgame: {exc}")
        state["last_settlement"]=now.isoformat()
    result["credits_remaining"]=state.get("credits_remaining")
    state["last_run"]=now.isoformat();_save_state(state,state_path)
    return result
