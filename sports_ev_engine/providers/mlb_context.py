from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import re

import pandas as pd
import requests

from sports_ev_engine.providers.mlb_statsapi import BASE, match_schedule
from sports_ev_engine.providers.mlb_deep import MLBDeepContext

KST=ZoneInfo("Asia/Seoul")
UA="Sports-EV-Engine/3.1.0"


def _num(v):
    try:
        if v in (None,"","-","--"):return None
        x=float(str(v).replace(",",""))
        return x if math.isfinite(x) else None
    except Exception:return None


def _ip(v):
    """MLB innings notation 5.1=5 1/3, 5.2=5 2/3."""
    try:
        s=str(v).strip()
        if not s:return None
        if "." not in s:return float(s)
        a,b=s.split(".",1); base=float(a or 0)
        if b=="1":return base+1/3
        if b=="2":return base+2/3
        return float(s)
    except Exception:return None


def _norm(s):
    return re.sub(r"[^a-z0-9]","",str(s or "").lower())


def _ratio_factor(value, baseline, max_move=.04, strength=.35):
    if value is None or baseline in (None,0) or value<=0 or baseline<=0:return None
    ratio=float(value)/float(baseline)
    raw=math.exp(strength*math.log(ratio))
    return max(1-max_move,min(1+max_move,raw))


def _safe_status(payload):
    try:return payload["stats"][0]["splits"]
    except Exception:return []


class MLBContextProvider:
    """MLB Stats API context collector with fail-soft, no-fake semantics.

    Every advanced signal is marked available only when a real MLB/Weather source
    returned enough fields. Missing velocity/splits/weather stay MISSING and add
    uncertainty instead of receiving league-average placeholders.
    """
    def __init__(self, timeout=20):
        self.timeout=timeout
        self.s=requests.Session(); self.s.headers.update({"User-Agent":UA})
        self.cache={}
        self.standings_cache={}
        # Reuse one deep provider for the whole slate so Savant leaderboards and
        # player lookups are downloaded once and served from cache across games.
        self.deep_provider=MLBDeepContext(self,timeout=min(18,self.timeout))

    def _get(self,path,params=None):
        key=(path,tuple(sorted((params or {}).items())))
        if key in self.cache:return self.cache[key]
        r=self.s.get(f"{BASE}{path}",params=params,timeout=self.timeout)
        r.raise_for_status(); data=r.json(); self.cache[key]=data; return data

    def teams(self,season):
        data=self._get("/teams",{"sportId":1,"season":int(season)})
        return { _norm(t.get("name")):t for t in data.get("teams",[]) }

    def team_id(self,name,season):
        teams=self.teams(season); n=_norm(name)
        if n in teams:return teams[n].get("id")
        hits=[t for k,t in teams.items() if n in k or k in n]
        return hits[0].get("id") if len(hits)==1 else None

    def _stat(self,*,team_id=None,person_id=None,stats="season",group="hitting",season=None,start=None,end=None,sit_code=None):
        if person_id:
            path=f"/people/{int(person_id)}/stats"
        elif team_id:
            # IMPORTANT: /stats?teamId=... can return leaderboard/player splits on
            # some StatsAPI responses. Taking splits[0] then treats one player's
            # runs/games as the whole team's scoring rate (the v3.4.20 MLB
            # 0.4~0.6 expected-runs failure). The team-scoped endpoint returns
            # the aggregate team split we actually need.
            path=f"/teams/{int(team_id)}/stats"
        else:
            path="/stats"
        params={"stats":stats,"group":group}
        if season:params["season"]=int(season)
        if start:params["startDate"]=str(start)
        if end:params["endDate"]=str(end)
        if sit_code:params["sitCodes"]=sit_code
        data=self._get(path,params)
        return _safe_status(data)

    def standings(self,season):
        season=int(season)
        if season in self.standings_cache:return self.standings_cache[season]
        data=self._get("/standings",{"leagueId":"103,104","season":season,"standingsTypes":"regularSeason"})
        out={}
        for rec in data.get("records",[]):
            for tr in rec.get("teamRecords",[]):
                tid=(tr.get("team") or {}).get("id")
                w=_num(tr.get("wins")); l=_num(tr.get("losses"))
                if tid and w is not None and l is not None and w+l>0:
                    out[int(tid)]={"win_pct":w/(w+l),"wins":w,"losses":l}
        self.standings_cache[season]=out
        return out

    def season_team(self,team_id,season):
        try:hit=self._stat(team_id=team_id,stats="season",group="hitting",season=season)
        except Exception:hit=[]
        try:pit=self._stat(team_id=team_id,stats="season",group="pitching",season=season)
        except Exception:pit=[]
        hs=(hit[0].get("stat") if hit else {}) or {}
        ps=(pit[0].get("stat") if pit else {}) or {}
        games=_num(hs.get("gamesPlayed")) or _num(ps.get("gamesPlayed"))
        runs=_num(hs.get("runs")); runs_allowed=_num(ps.get("runs"))
        try:st=self.standings(season).get(int(team_id),{})
        except Exception:st={}
        return {
            "games":int(games or 0),
            "runs_per_game":(runs/games if runs is not None and games else None),
            "runs_allowed_per_game":(runs_allowed/games if runs_allowed is not None and games else None),
            "win_pct":st.get("win_pct"),
            "era":_num(ps.get("era")),
            "ops":_num(hs.get("ops")),
            "source":"MLB Stats API season",
        }

    def recent_games(self,team_id,kickoff,n=10):
        kd=pd.Timestamp(kickoff)
        kd=kd.tz_convert("UTC") if kd.tzinfo else kd.tz_localize("UTC")
        end=(kd-timedelta(days=1)).date(); start=end-timedelta(days=max(25,int(n)*3))
        data=self._get("/schedule",{
            "sportId":1,"teamId":int(team_id),"startDate":start.isoformat(),"endDate":end.isoformat(),
        })
        rows=[]
        for d in data.get("dates",[]):
            for g in d.get("games",[]):
                state=(g.get("status") or {}).get("abstractGameState")
                if state!="Final":continue
                ht=g.get("teams",{}).get("home",{}); at=g.get("teams",{}).get("away",{})
                hid=(ht.get("team") or {}).get("id"); aid=(at.get("team") or {}).get("id")
                hs=_num(ht.get("score")); ass=_num(at.get("score"))
                if hs is None or ass is None:continue
                is_home=int(hid or -1)==int(team_id)
                rf=hs if is_home else ass; ra=ass if is_home else hs
                rows.append({"gamePk":g.get("gamePk"),"date":g.get("gameDate"),"rf":rf,"ra":ra,"win":rf>ra,
                             "venue_id":(g.get("venue") or {}).get("id"),"venue":(g.get("venue") or {}).get("name")})
        rows=sorted(rows,key=lambda x:str(x.get("date")))[-int(n):]
        return rows

    def range_ops(self,team_id,games):
        if not games:return None
        try:
            start=pd.Timestamp(games[0]["date"]).date().isoformat(); end=pd.Timestamp(games[-1]["date"]).date().isoformat()
            splits=self._stat(team_id=team_id,stats="byDateRange",group="hitting",start=start,end=end)
            stat=(splits[0].get("stat") if splits else {}) or {}
            return _num(stat.get("ops"))
        except Exception:return None

    def team_profile(self,name,team_id,season,kickoff,n=10):
        season_stat=self.season_team(team_id,season)
        season_stat["season_games"]=season_stat.get("games")
        recent=self.recent_games(team_id,kickoff,n)
        if recent:
            season_stat["recent10_win_pct"]=sum(int(x["win"]) for x in recent)/len(recent)
            season_stat["recent_runs_per_game"]=sum(x["rf"] for x in recent)/len(recent)
            season_stat["recent_runs_allowed_per_game"]=sum(x["ra"] for x in recent)/len(recent)
        else:
            season_stat["recent10_win_pct"]=None
            season_stat["recent_runs_per_game"]=None
            season_stat["recent_runs_allowed_per_game"]=None
        season_stat["recent_ops"]=self.range_ops(team_id,recent)
        # Real recent results are a fail-soft fallback if the season aggregate endpoint
        # is temporarily unavailable. They are not a fabricated league average.
        if recent and season_stat.get("runs_per_game") is None:
            season_stat["runs_per_game"]=sum(x["rf"] for x in recent)/len(recent)
            season_stat["source"]="MLB Stats API recent schedule fallback"
        if recent and season_stat.get("runs_allowed_per_game") is None:
            season_stat["runs_allowed_per_game"]=sum(x["ra"] for x in recent)/len(recent)
            season_stat["source"]="MLB Stats API recent schedule fallback"
        # Counter-case sample should reflect actual recent games, not season length.
        season_stat["games"]=len(recent)
        season_stat["team_id"]=int(team_id)
        season_stat["name"]=name
        season_stat["recent_games"]=recent
        return season_stat

    def person(self,pid):
        if not pid:return {}
        try:
            data=self._get(f"/people/{int(pid)}")
            p=(data.get("people") or [{}])[0]
            return {"id":pid,"name":p.get("fullName"),"hand":(p.get("pitchHand") or {}).get("code")}
        except Exception:return {"id":pid}

    def pitcher(self,pid,season,recent_n=5):
        if not pid:return {"available":False,"reason":"probable starter unavailable"}
        season_splits=self._stat(person_id=pid,stats="season",group="pitching",season=season)
        ss=(season_splits[0].get("stat") if season_splits else {}) or {}
        all_logs=self._stat(person_id=pid,stats="gameLog",group="pitching",season=season)
        logs=all_logs[-int(recent_n):] if all_logs else []
        bf=sum(_num((x.get("stat") or {}).get("battersFaced")) or 0 for x in logs)
        so=sum(_num((x.get("stat") or {}).get("strikeOuts")) or 0 for x in logs)
        bb=sum(_num((x.get("stat") or {}).get("baseOnBalls")) or 0 for x in logs)
        er=sum(_num((x.get("stat") or {}).get("earnedRuns")) or 0 for x in logs)
        ip=sum(_ip((x.get("stat") or {}).get("inningsPitched")) or 0 for x in logs)
        person=self.person(pid)
        starts=[]
        for x in logs:
            st=(x.get("stat") or {})
            starts.append({
                "gamePk":(x.get("game") or {}).get("gamePk"),"date":x.get("date") or (x.get("game") or {}).get("gameDate"),
                "pitches":_num(st.get("numberOfPitches")) or _num(st.get("pitchesThrown")),
                "ip":_ip(st.get("inningsPitched")),"bf":_num(st.get("battersFaced")),
            })
        recent={
            "available":bool(logs),"games":len(logs),"bf":bf,"so":so,"bb":bb,"er":er,"ip":ip,
            "era":9*er/ip if ip else None,"k_pct":so/bf if bf else None,"bb_pct":bb/bf if bf else None,
            "kbb_pct":(so-bb)/bf if bf else None,"hand":person.get("hand"),
            "game_pks":[(x.get("game") or {}).get("gamePk") for x in logs if (x.get("game") or {}).get("gamePk")],
            "starts":starts,
        }
        try:
            prior_splits=self._stat(person_id=pid,stats="season",group="pitching",season=int(season)-1)
            prior=(prior_splits[0].get("stat") if prior_splits else {}) or {}
        except Exception: prior={}
        season_out={
            "era":_num(ss.get("era")),"whip":_num(ss.get("whip")),
            "kbb":((_num(ss.get("strikeOuts")) or 0)/max(1,(_num(ss.get("baseOnBalls")) or 0))) if _num(ss.get("strikeOuts")) is not None else None,
            "innings":_ip(ss.get("inningsPitched")),"prior_innings":_ip(prior.get("inningsPitched")),
            "hand":person.get("hand"),
        }
        return {"available":bool(season_splits),"name":person.get("name"),"season":season_out,"recent":recent}

    def lineup(self,game_pk):
        if not game_pk:return {"confirmed":False,"home":[],"away":[],"reason":"gamePk unavailable"}
        try:data=self._get(f"/game/{int(game_pk)}/boxscore")
        except Exception as e:return {"confirmed":False,"home":[],"away":[],"reason":str(e)}
        out={}
        for side in ("home","away"):
            t=(data.get("teams") or {}).get(side,{})
            players=t.get("players") or {}
            rows=[]
            for p in players.values():
                order=str(p.get("battingOrder") or "").strip()
                if not order:continue
                rows.append({
                    "player_id":(p.get("person") or {}).get("id"),"name":(p.get("person") or {}).get("fullName"),
                    "order":int(order)//100 if order.isdigit() else order,"position":(p.get("position") or {}).get("abbreviation"),
                })
            rows=sorted(rows,key=lambda x:(x["order"] if isinstance(x["order"],int) else 99))[:9]
            out[side]=rows
        out["confirmed"]=len(out.get("home",[]))>=9 and len(out.get("away",[]))>=9
        return out

    def bullpen(self,team_profile):
        games=(team_profile or {}).get("recent_games",[])[-3:]
        total=0.0; used=0
        for g in games:
            gp=g.get("gamePk")
            if not gp:continue
            try:box=self._get(f"/game/{int(gp)}/boxscore")
            except Exception:continue
            side=None
            for s in ("home","away"):
                if int(((box.get("teams") or {}).get(s,{"team":{}}).get("team") or {}).get("id") or -1)==int(team_profile.get("team_id")):
                    side=s;break
            if not side:continue
            team=(box.get("teams") or {}).get(side,{})
            pitchers=team.get("pitchers") or []
            players=team.get("players") or {}
            if not pitchers:continue
            relief=0.0
            for idx,pid in enumerate(pitchers):
                if idx==0:continue
                p=players.get(f"ID{pid}",{})
                relief+=_ip(((p.get("stats") or {}).get("pitching") or {}).get("inningsPitched")) or 0
            total+=relief; used+=1
        return {"available":used>0,"games":used,"relief_ip_last3":total if used else None}

    def split_ops(self,team_id,season,starter_hand,season_ops):
        if starter_hand not in {"L","R"}:return {"available":False,"reason":"starter hand unavailable"}
        code="vl" if starter_hand=="L" else "vr"
        try:splits=self._stat(team_id=team_id,stats="season",group="hitting",season=season,sit_code=code)
        except Exception as e:return {"available":False,"reason":str(e)}
        stat=(splits[0].get("stat") if splits else {}) or {}
        ops=_num(stat.get("ops"))
        return {"available":ops is not None,"ops":ops,"season_ops":season_ops,"hand":starter_hand}

    def velocity(self,pitcher_recent):
        """Fastball (FF/SI) velocity from recent MLB play-by-play. Missing stays missing."""
        pks=(pitcher_recent or {}).get("game_pks",[])[-3:]
        if not pks:return {"available":False,"reason":"recent game ids unavailable"}
        game_means=[]
        for gp in pks:
            try:data=self._get(f"/game/{int(gp)}/playByPlay")
            except Exception:continue
            vals=[]
            for play in data.get("allPlays",[]):
                for ev in play.get("playEvents",[]):
                    det=ev.get("details") or {}; pdx=ev.get("pitchData") or {}
                    ptype=(det.get("type") or {}).get("code")
                    if ptype not in {"FF","SI"}:continue
                    speed=_num(pdx.get("startSpeed"))
                    if speed and 70<speed<110:vals.append(speed)
            if vals:game_means.append(sum(vals)/len(vals))
        if not game_means:return {"available":False,"reason":"FF/SI velocity unavailable"}
        current=game_means[-1]; baseline=(sum(game_means[:-1])/len(game_means[:-1])) if len(game_means)>1 else None
        return {"available":True,"games":len(game_means),"fastball_mph":current,"delta_mph":current-baseline if baseline else None}

    def venue_weather(self,venue_id,kickoff):
        if not venue_id:return {"available":False,"reason":"venue unavailable"}
        try:
            data=self._get(f"/venues/{int(venue_id)}")
            v=(data.get("venues") or [{}])[0]
            loc=v.get("location") or {}; coords=loc.get("defaultCoordinates") or {}
            lat=_num(coords.get("latitude")); lon=_num(coords.get("longitude"))
            name=v.get("name")
            field=v.get("fieldInfo") or {}; roof=str(field.get("roofType") or "")
            if "dome" in roof.lower() or "closed" in roof.lower() or "fixed" in roof.lower():
                return {"available":True,"stadium":name,"roof":roof,"run_factor":1.0,"temperature_c":None,"wind_kmh":0.0,"precip_mm":0.0,"source":"MLB venue roof"}
            if lat is None or lon is None:return {"available":False,"stadium":name,"reason":"venue coordinates unavailable"}
            dt=pd.Timestamp(kickoff); dt=dt.tz_convert("UTC") if dt.tzinfo else dt.tz_localize("UTC")
            # Venue timezone is not always in the venue endpoint; Open-Meteo can return UTC.
            day=dt.date().isoformat()
            r=self.s.get("https://api.open-meteo.com/v1/forecast",params={
                "latitude":lat,"longitude":lon,"hourly":"temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m",
                "timezone":"UTC","start_date":day,"end_date":day,
            },timeout=min(12,self.timeout))
            r.raise_for_status(); h=r.json().get("hourly") or {}; times=h.get("time") or []
            if not times:return {"available":False,"stadium":name,"reason":"weather hourly empty"}
            target=dt.floor("h").strftime("%Y-%m-%dT%H:%M")
            idx=min(range(len(times)),key=lambda i:abs(pd.Timestamp(times[i],tz="UTC").timestamp()-dt.timestamp()))
            temp=_num((h.get("temperature_2m") or [None]*len(times))[idx]); rain=_num((h.get("precipitation") or [None]*len(times))[idx]) or 0
            wind=_num((h.get("wind_speed_10m") or [None]*len(times))[idx]) or 0
            temp_move=0 if temp is None else max(-.025,min(.025,(temp-20)*.0015))
            return {"available":True,"stadium":name,"roof":roof,"temperature_c":temp,"precip_mm":rain,"wind_kmh":wind,"run_factor":1+temp_move,"source":"Open-Meteo + MLB venue"}
        except Exception as e:return {"available":False,"reason":str(e)}

    def collect(self,home,away,kickoff,schedule_row=None,recent_n=10,deep=True,market_frame=None,event_id=None):
        season=int(pd.Timestamp(kickoff).year)
        schedule_row=schedule_row or {}
        hid=schedule_row.get("home_team_id") or self.team_id(home,season)
        aid=schedule_row.get("away_team_id") or self.team_id(away,season)
        if not hid or not aid:
            return {},{"stage":"DATA PARTIAL","source":"MLB Stats API","note":"team id match failed","advanced":{"advanced_used":0,"advanced_total":6,"advanced_completeness":0,"extra_uncertainty_pp":3.0,"statuses":{},"notes":["team id match failed"]}}
        hs=self.team_profile(home,hid,season,kickoff,recent_n)
        aws=self.team_profile(away,aid,season,kickoff,recent_n)
        stats={home:hs,away:aws}

        hp=schedule_row.get("home_probable_id"); ap=schedule_row.get("away_probable_id")
        hpdata=self.pitcher(hp,season,5) if hp else {"available":False,"reason":"home probable starter unavailable"}
        apdata=self.pitcher(ap,season,5) if ap else {"available":False,"reason":"away probable starter unavailable"}
        lineup=self.lineup(schedule_row.get("gamePk")) if schedule_row.get("gamePk") else {"confirmed":False,"home":[],"away":[],"reason":"gamePk unavailable"}
        starters=bool(hp and ap)
        if lineup.get("confirmed") and starters:stage="FINAL"
        elif lineup.get("confirmed"):stage="LINEUP CONFIRMED"
        elif starters:stage="STARTER CONFIRMED"
        else:stage="PRE-LINEUP"

        adv={"components":{},"statuses":{},"notes":[],"advanced_total":6}
        # Recent team offense: recent OPS preferred, runs/game fallback only as evidence.
        rec_ok=hs.get("recent_ops") is not None and aws.get("recent_ops") is not None
        adv["statuses"]["recent_form"]=rec_ok
        if hs.get("recent_ops") is not None and hs.get("ops"):
            adv["components"]["home_recent_form_factor"]=_ratio_factor(hs["recent_ops"],hs["ops"],.04,.30)
        if aws.get("recent_ops") is not None and aws.get("ops"):
            adv["components"]["away_recent_form_factor"]=_ratio_factor(aws["recent_ops"],aws["ops"],.04,.30)

        # Starter recent 3-5 form versus own season baseline.
        sr_ok=bool((hpdata.get("recent") or {}).get("available") and (apdata.get("recent") or {}).get("available"))
        adv["statuses"]["starter_recent"]=sr_ok
        def starter_factor(p):
            sea=p.get("season") or {}; rec=p.get("recent") or {}
            if rec.get("era") is None or sea.get("era") in (None,0):return None
            f=_ratio_factor(rec["era"],sea["era"],.045,.22)
            # Higher recent K-BB% is a positive pitching signal, reducing opponent runs.
            kb=rec.get("kbb_pct")
            if kb is not None:
                f=(f or 1.0)*max(.975,min(1.025,1-(float(kb)-.12)*.08))
            return max(.94,min(1.06,f))
        adv["components"]["home_starter_recent_factor"]=starter_factor(hpdata)
        adv["components"]["away_starter_recent_factor"]=starter_factor(apdata)

        hb=self.bullpen(hs); ab=self.bullpen(aws)
        bullpen_ok=hb.get("available") and ab.get("available")
        adv["statuses"]["bullpen"]=bool(bullpen_ok)
        def bp_factor(x):
            ip=x.get("relief_ip_last3")
            if ip is None:return None
            # Only modest directional adjustment; workload also remains visible as a counter-case signal.
            return 1.02 if ip>=12 else 1.01 if ip>=9 else .995 if ip<=4 else 1.0
        adv["components"]["home_vs_bullpen_factor"]=bp_factor(ab)
        adv["components"]["away_vs_bullpen_factor"]=bp_factor(hb)

        hsplit=self.split_ops(hid,season,(apdata.get("recent") or {}).get("hand"),hs.get("ops")) if deep else {"available":False,"reason":"deep context disabled"}
        asplit=self.split_ops(aid,season,(hpdata.get("recent") or {}).get("hand"),aws.get("ops")) if deep else {"available":False,"reason":"deep context disabled"}
        split_ok=hsplit.get("available") and asplit.get("available")
        adv["statuses"]["split"]=bool(split_ok)
        if hsplit.get("ops") is not None and hsplit.get("season_ops"):
            adv["components"]["home_split_factor"]=_ratio_factor(hsplit["ops"],hsplit["season_ops"],.035,.25)
        if asplit.get("ops") is not None and asplit.get("season_ops"):
            adv["components"]["away_split_factor"]=_ratio_factor(asplit["ops"],asplit["season_ops"],.035,.25)

        hv=self.velocity(hpdata.get("recent") or {}) if deep else {"available":False,"reason":"deep context disabled"}
        av=self.velocity(apdata.get("recent") or {}) if deep else {"available":False,"reason":"deep context disabled"}
        vel_ok=hv.get("available") and av.get("available")
        adv["statuses"]["velocity"]=bool(vel_ok)
        # Fold fastball loss into starter factor without pretending it is an independent calibrated model.
        def apply_vel(existing,v):
            d=v.get("delta_mph")
            if d is None:return existing
            vf=1.018 if d<=-1.5 else 1.010 if d<=-.8 else .992 if d>=1.0 else 1.0
            return max(.93,min(1.07,(existing or 1.0)*vf))
        adv["components"]["home_starter_recent_factor"]=apply_vel(adv["components"].get("home_starter_recent_factor"),hv)
        adv["components"]["away_starter_recent_factor"]=apply_vel(adv["components"].get("away_starter_recent_factor"),av)

        weather=self.venue_weather(schedule_row.get("venue_id"),kickoff) if deep else {"available":False,"reason":"deep context disabled"}
        adv["statuses"]["weather"]=bool(weather.get("available"))
        if weather.get("available"):adv["components"]["weather_factor"]=weather.get("run_factor") or 1.0

        # v3.1 highest-detail MLB layer. All slots are fail-soft; unavailable data stays MISSING.
        deep_adv={}
        if deep:
            try:
                deep_adv=self.deep_provider.collect(
                    home=home,away=away,kickoff=kickoff,schedule_row=schedule_row,
                    home_profile=hs,away_profile=aws,home_pitcher=hpdata,away_pitcher=apdata,
                    lineup=lineup,market_frame=market_frame,event_id=event_id,season=season,
                )
            except Exception as e:
                deep_adv={"statuses":{},"components":{},"advanced_total":0,"advanced_used":0,
                          "advanced_completeness":0,"extra_uncertainty_pp":1.0,
                          "missing_high_priority":["v3.1_deep_layer"],"error":str(e)}
        adv["statuses"].update(deep_adv.get("statuses") or {})
        adv["components"].update({k:v for k,v in (deep_adv.get("components") or {}).items() if v is not None})
        base_keys=["recent_form","starter_recent","bullpen","split","velocity","weather"]
        all_keys=base_keys+list((deep_adv.get("statuses") or {}).keys())
        # preserve order while removing duplicates
        all_keys=list(dict.fromkeys(all_keys))
        adv["advanced_total"]=len(all_keys)
        used=sum(bool(adv["statuses"].get(k)) for k in all_keys)
        adv["advanced_used"]=used; adv["advanced_completeness"]=used/max(1,adv["advanced_total"])
        missing=[k for k in all_keys if not adv["statuses"].get(k)]
        # Missing high-priority deep data widens uncertainty; no fabricated average fills the slot.
        adv["extra_uncertainty_pp"]=min(4.5,.18*len(missing)+float(deep_adv.get("extra_uncertainty_pp") or 0))
        if missing:adv["notes"].append("MISSING: "+", ".join(missing))
        adv["deep_v31"]=deep_adv

        ctx={
            "league":"MLB","stage":stage,"source":"MLB Stats API + Baseball Savant + The Odds API",
            "starter_confirmed":starters,"lineup_confirmed":bool(lineup.get("confirmed")),
            "home_starter":schedule_row.get("home_probable") or hpdata.get("name"),
            "away_starter":schedule_row.get("away_probable") or apdata.get("name"),
            "home_starter_id":hp,"away_starter_id":ap,
            "home_starter_stats":hpdata.get("season") or {},"away_starter_stats":apdata.get("season") or {},
            "home_lineup":lineup.get("home",[]),"away_lineup":lineup.get("away",[]),
            "advanced":adv,"note":"; ".join(adv.get("notes") or []),
        }
        adv.update({
            "home_starter_recent":hpdata.get("recent") or {},"away_starter_recent":apdata.get("recent") or {},
            "home_lineup_form":{"available":hs.get("recent_ops") is not None,"recent10_ops":hs.get("recent_ops"),"season_ops":hs.get("ops")},
            "away_lineup_form":{"available":aws.get("recent_ops") is not None,"recent10_ops":aws.get("recent_ops"),"season_ops":aws.get("ops")},
            "home_bullpen":hb,"away_bullpen":ab,"home_split":hsplit,"away_split":asplit,
            "home_velocity":hv,"away_velocity":av,"weather":weather,
        })
        return stats,ctx
