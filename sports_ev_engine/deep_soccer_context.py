"""Optional deep soccer context collector for v3.

The collector is fail-soft. It uses only fields actually returned by API-Football;
missing xG/lineup/injury/player data is labelled missing rather than imputed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import pandas as pd

from .models.soccer_auto import norm_name

PROVIDER_BUILD="3.0.0"


def _fixture_match(pool,home,away,kickoff_iso,tolerance_minutes=180):
    target=pd.Timestamp(kickoff_iso)
    if target.tzinfo is None: target=target.tz_localize("UTC")
    candidates=[]
    for fx in pool.get("fixtures",[]):
        teams=fx.get("teams",{})
        if norm_name(teams.get("home",{}).get("name",""))!=norm_name(home):continue
        if norm_name(teams.get("away",{}).get("name",""))!=norm_name(away):continue
        ts=fx.get("fixture",{}).get("timestamp")
        if not ts:continue
        dt=pd.Timestamp(int(ts),unit="s",tz="UTC")
        delta=abs((dt-target.tz_convert("UTC")).total_seconds())/60
        if delta<=tolerance_minutes:candidates.append((delta,fx))
    return min(candidates,key=lambda x:x[0])[1] if candidates else None


def _recent_team_fixtures(pool,team,kickoff_iso,n=5):
    target=pd.Timestamp(kickoff_iso)
    target=target.tz_convert("UTC") if target.tzinfo else target.tz_localize("UTC")
    out=[]
    for fx in pool.get("fixtures",[]):
        f=fx.get("fixture",{}); ts=f.get("timestamp") or 0
        if f.get("status",{}).get("short")!="FT" or not ts:continue
        dt=pd.Timestamp(int(ts),unit="s",tz="UTC")
        if dt>=target:continue
        teams=fx.get("teams",{})
        if norm_name(team) not in {norm_name(teams.get("home",{}).get("name","")),norm_name(teams.get("away",{}).get("name",""))}:continue
        out.append((dt,fx))
    out.sort(key=lambda x:x[0],reverse=True)
    return [fx for _,fx in out[:n]]


def _rest_days(pool,team,kickoff_iso):
    rows=_recent_team_fixtures(pool,team,kickoff_iso,1)
    if not rows:return None
    target=pd.Timestamp(kickoff_iso)
    target=target.tz_convert("UTC") if target.tzinfo else target.tz_localize("UTC")
    dt=pd.Timestamp(int(rows[0]["fixture"]["timestamp"]),unit="s",tz="UTC")
    return max(0.0,(target-dt).total_seconds()/86400)


def _parse_fixture_stats(payload, team_name):
    row=None
    for r in payload or []:
        if norm_name((r.get("team") or {}).get("name",""))==norm_name(team_name):row=r;break
    if not row:return {}
    vals={}
    for s in row.get("statistics",[]):
        typ=str(s.get("type","")).strip().lower().replace(" ","_")
        v=s.get("value")
        if isinstance(v,str) and v.endswith("%"):
            try:v=float(v[:-1])
            except ValueError:continue
        try:
            if v is not None:v=float(v)
        except (TypeError,ValueError):continue
        vals[typ]=v
    return vals


def _xg_average(api,pool,team,kickoff_iso,n=3):
    vals=[]; shots=[]; big=[]
    for fx in _recent_team_fixtures(pool,team,kickoff_iso,max(n,3))[:n]:
        fid=fx.get("fixture",{}).get("id")
        if not fid:continue
        try: payload=api.fixture_statistics(fid)
        except Exception:continue
        st=_parse_fixture_stats(payload,team)
        xg=st.get("expected_goals")
        if xg is None:xg=st.get("expected_goal")
        if xg is not None:vals.append(float(xg))
        if st.get("total_shots") is not None:shots.append(float(st["total_shots"]))
        for key in ("big_chances","big_chances_created"):
            if st.get(key) is not None:big.append(float(st[key]));break
    return {
        "xg":sum(vals)/len(vals) if len(vals)>=3 else None,
        "xg_games":len(vals),
        "shots":sum(shots)/len(shots) if shots else None,
        "big_chances":sum(big)/len(big) if big else None,
    }


def _opponent_xga(api,pool,team,kickoff_iso,n=3):
    vals=[]
    for fx in _recent_team_fixtures(pool,team,kickoff_iso,max(n,3))[:n]:
        fid=fx.get("fixture",{}).get("id")
        teams=fx.get("teams",{})
        home=teams.get("home",{}).get("name",""); away=teams.get("away",{}).get("name","")
        opp=away if norm_name(home)==norm_name(team) else home
        if not fid or not opp:continue
        try: payload=api.fixture_statistics(fid)
        except Exception:continue
        st=_parse_fixture_stats(payload,opp)
        xg=st.get("expected_goals")
        if xg is None:xg=st.get("expected_goal")
        if xg is not None:vals.append(float(xg))
    return sum(vals)/len(vals) if len(vals)>=3 else None


def _player_importance(rows):
    out={}
    for item in rows or []:
        p=item.get("player") or {}; pid=p.get("id")
        best=None
        for s in item.get("statistics",[]) or []:
            games=s.get("games") or {}; mins=games.get("minutes") or 0; apps=games.get("appearences") or games.get("appearances") or 0
            rating=games.get("rating")
            try:rating=float(rating) if rating is not None else None
            except (TypeError,ValueError):rating=None
            pos=games.get("position") or ""
            score=float(mins or 0)+90*float(apps or 0)
            if best is None or score>best[0]:best=(score,float(mins or 0),float(apps or 0),rating,pos)
        if not pid or not best:continue
        _,mins,apps,rating,pos=best
        out[int(pid)]={"name":p.get("name",""),"minutes":mins,"apps":apps,"rating":rating,"position":pos}
    maxmin=max((v["minutes"] for v in out.values()),default=0) or 1
    for v in out.values():
        minute_component=clamp(v["minutes"]/maxmin,0,1)
        rating_component=clamp(((v["rating"] or 6.5)-5.5)/2.5,0,1)
        v["importance"]=.72*minute_component+.28*rating_component
    return out


def clamp(v,lo,hi):return max(lo,min(hi,float(v)))


def _impact_by_position(importance, player_ids):
    atk=defn=0.0; names=[]
    for pid in player_ids:
        v=importance.get(int(pid)) if pid is not None else None
        if not v:continue
        imp=clamp(v["importance"],0,1);pos=str(v.get("position","")).lower(); names.append(v.get("name",""))
        if "att" in pos:
            atk-=2.6*imp;defn-=.25*imp
        elif "def" in pos:
            atk-=.35*imp;defn-=2.2*imp
        elif "goal" in pos:
            defn-=2.8*imp
        else:
            atk-=1.25*imp;defn-=1.15*imp
    return clamp(atk,-10,0),clamp(defn,-10,0),names


def _lineup_change(importance,lineup_ids):
    if not lineup_ids or not importance:return 0.0,0.0
    top=sorted(importance.values(),key=lambda x:x["importance"],reverse=True)[:11]
    baseline=sum(x["importance"] for x in top) or 1
    current=sum(importance.get(int(pid),{}).get("importance",0) for pid in lineup_ids if pid is not None)
    ratio=clamp(current/baseline,.75,1.08)
    # Split lineup effect mildly into attack/defence; position-specific injury layer handles absences.
    pct=clamp((ratio-1)*18,-5,1.5)
    return pct,pct


def collect_deep_context(api,pool,home,away,kickoff_iso,season=None,horizon_hours=24):
    target=pd.Timestamp(kickoff_iso)
    now=pd.Timestamp.now(tz="UTC")
    target_utc=target.tz_convert("UTC") if target.tzinfo else target.tz_localize("UTC")
    hours=(target_utc-now).total_seconds()/3600
    ctx={"deep_context_attempted":True,"horizon_hours":horizon_hours,
         "home_rest_days":_rest_days(pool,home,kickoff_iso),"away_rest_days":_rest_days(pool,away,kickoff_iso),
         "schedule_source":"competition fixture history"}
    if hours<0 or hours>horizon_hours:
        ctx["deep_context_reason"]=f"outside {horizon_hours}h deep-context window"
        return ctx

    fixture=_fixture_match(pool,home,away,kickoff_iso)
    if not fixture:
        # National-team pools contain historical fixtures only. Fall back to the
        # provider's date board and verify both teams + kickoff before using it.
        try:
            date_str=target_utc.date().isoformat()
            board=api.fixtures_by_date(date_str)
            temp={"fixtures":board}
            fixture=_fixture_match(temp,home,away,kickoff_iso)
        except Exception:
            fixture=None
    if not fixture:
        ctx["deep_context_reason"]="target fixture not matched in provider competition/date board"
        return ctx
    fid=fixture.get("fixture",{}).get("id")
    ctx["fixture_id"]=fid
    teams=fixture.get("teams",{})
    htid=teams.get("home",{}).get("id"); atid=teams.get("away",{}).get("id")

    # Recent xG and chance creation. Calls are cached by APIFootball._get.
    hfor=_xg_average(api,pool,home,kickoff_iso); afor=_xg_average(api,pool,away,kickoff_iso)
    hxga=_opponent_xga(api,pool,home,kickoff_iso); axga=_opponent_xga(api,pool,away,kickoff_iso)
    ctx.update(home_xg_for=hfor["xg"],home_xg_against=hxga,away_xg_for=afor["xg"],away_xg_against=axga,
               home_big_chances=hfor.get("big_chances"),away_big_chances=afor.get("big_chances"),
               xg_source="API-Football fixtures/statistics",big_chance_source="API-Football fixtures/statistics")

    # Confirmed XIs.
    lineups=[]
    try: lineups=api.lineups(fid) if fid else []
    except Exception: lineups=[]
    ids={"home":[],"away":[]}
    for r in lineups or []:
        tname=(r.get("team") or {}).get("name","")
        side="home" if norm_name(tname)==norm_name(home) else "away" if norm_name(tname)==norm_name(away) else None
        if not side:continue
        start=r.get("startXI") or []
        pids=[(x.get("player") or {}).get("id") for x in start if (x.get("player") or {}).get("id")]
        if len(set(pids))==11:ids[side]=pids
    ctx["lineup_confirmed"]=len(ids["home"])==11 and len(ids["away"])==11
    ctx["lineup_source"]="API-Football fixtures/lineups"

    # Season player importance is optional and fail-soft. One/two pages per team.
    season=int(season or target_utc.year)
    importance={"home":{},"away":{}}
    for side,tid in (("home",htid),("away",atid)):
        if not tid:continue
        try: importance[side]=_player_importance(api.team_players(tid,season,max_pages=2))
        except Exception: importance[side]={}
        la,ld=_lineup_change(importance[side],ids[side])
        ctx[f"{side}_lineup_attack_pct"]=la;ctx[f"{side}_lineup_defense_pct"]=ld

    # Injuries/suspensions + season importance.
    injuries=[]
    injury_request_ok=False
    try:
        injuries=api.injuries(fid) if fid else []
        injury_request_ok=bool(fid) and isinstance(injuries,list)
    except Exception:
        injuries=[]
        injury_request_ok=False
    ctx["injury_available"]=injury_request_ok
    ctx["injury_source"]="API-Football injuries"
    for side,tname in (("home",home),("away",away)):
        pids=[]
        for r in injuries or []:
            if norm_name((r.get("team") or {}).get("name",""))!=norm_name(tname):continue
            p=(r.get("player") or {}); pids.append(p.get("id"))
        atk,defn,names=_impact_by_position(importance[side],[x for x in pids if x])
        ctx[f"{side}_injury_attack_pct"]=atk;ctx[f"{side}_injury_defense_pct"]=defn
        ctx[f"{side}_missing_players"]=names
    return ctx
