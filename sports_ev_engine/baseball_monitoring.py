
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.providers.api_sports_baseball import APISportsBaseball
from sports_ev_engine.market import clean_odds, consensus
from sports_ev_engine.auto_baseball import analyze_baseball_event, build_league_pool
from sports_ev_engine.monitoring import (
    JSONStateStore, minutes_until, scheduled_interval_minutes,
    reanalysis_threshold_pp, should_take_full_snapshot, format_alert,
)


@dataclass
class BaseballMonitorConfig:
    league: str
    sport_key: str
    region: str="eu"
    min_books: int=2
    recent_n: int=10
    state_path: str="data/baseball_monitor_state.json"
    reserve_credits: int=2000
    require_two_observations: bool=True
    strong_move_pp: float=4.0
    line_move_trigger: float=.25


def _hash(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


class BaseballMonitorEngine:
    def __init__(self, odds_key, baseball_key, config: BaseballMonitorConfig, notifier=None):
        self.odds=TheOddsAPI(odds_key)
        self.baseball=APISportsBaseball(baseball_key)
        self.cfg=config
        self.notifier=notifier
        self.store=JSONStateStore(config.state_path)

    def notify(self,text):
        if self.notifier: self.notifier(text)

    def tick(self, now=None):
        now=now or datetime.now(timezone.utc)
        state=self.store.load()
        alerts=[]
        if state.get("credits_remaining") is not None and int(state["credits_remaining"]) <= self.cfg.reserve_credits:
            return {"status":"paused_reserve","credits_remaining":state["credits_remaining"],"alerts":[]}

        events,headers=self.odds.odds(self.cfg.sport_key,self.cfg.region,"h2h")
        raw_h2h=clean_odds(self.odds.flatten(events))
        market_h2h=consensus(raw_h2h,min_books=self.cfg.min_books)
        rem=headers.get("x-requests-remaining")
        if rem is not None:
            try: state["credits_remaining"]=int(rem)
            except: state["credits_remaining"]=rem

        full_due=False
        threshold=None
        for eid,g in market_h2h.groupby("event_id") if not market_h2h.empty else []:
            mins=minutes_until(g.iloc[0]["commence_time"],now)
            es=state["events"].setdefault(str(eid),{})
            fired=es.setdefault("full_snapshots_fired",[])
            t=should_take_full_snapshot(mins,fired)
            if t is not None:
                full_due=True; threshold=t; break

        if full_due:
            events2,h2=self.odds.odds(self.cfg.sport_key,self.cfg.region,"h2h,spreads,totals")
            raw=clean_odds(self.odds.flatten(events2))
            market=consensus(raw,min_books=self.cfg.min_books)
            rem=h2.get("x-requests-remaining")
            if rem is not None:
                try: state["credits_remaining"]=int(rem)
                except: state["credits_remaining"]=rem
            for eid,g in market.groupby("event_id") if not market.empty else []:
                mins=minutes_until(g.iloc[0]["commence_time"],now)
                es=state["events"].setdefault(str(eid),{})
                fired=es.setdefault("full_snapshots_fired",[])
                for t in (360,120,60,30,15,5):
                    if 0<mins<=t and t not in fired: fired.append(t)
        else:
            market=market_h2h

        triggered=set()
        for _,r in market.iterrows():
            eid=str(r["event_id"])
            es=state["events"].setdefault(eid,{})
            es["home_team"]=r["home_team"]; es["away_team"]=r["away_team"]; es["commence_time"]=r["commence_time"]
            key=f'{r["market"]}|{r["market_id"]}|{r["selection"]}'
            prev=es.setdefault("markets",{}).get(key,{})
            old=prev.get("consensus_prob")
            move=(float(r["consensus_prob"])-float(old))*100 if old is not None else None
            mins=minutes_until(r["commence_time"],now)
            th=reanalysis_threshold_pp(mins)
            direction=1 if move is not None and move>0 else -1 if move is not None and move<0 else 0
            streak=int(prev.get("move_streak",0))
            if move is not None and abs(move)>=th:
                streak=streak+1 if prev.get("direction")==direction else 1
            else:
                streak=0
            fire=(move is not None and abs(move)>=self.cfg.strong_move_pp) or (
                move is not None and abs(move)>=th and (not self.cfg.require_two_observations or streak>=2)
            )
            if fire:
                triggered.add(eid)
            es["markets"][key]={
                "consensus_prob":float(r["consensus_prob"]),
                "best_odds":float(r["best_odds"]),
                "move_streak":streak,
                "direction":direction,
                "observed_at":now.isoformat(),
            }

            # Starting-lineup watch is intentionally disabled in v2.4.2.
            # API-Sports Baseball is used for stable history; it does not expose
            # a reliable KBO/NPB starting-lineup feed in this integration.

        # reanalyze triggered events
        pool=None
        if triggered and not market.empty:
            # seed with one current Sofa event when possible
            try:
                r0=market.iloc[0]
                seed=self.baseball.find_event(r0["home_team"],r0["away_team"],r0["commence_time"])
                pool=build_league_pool(self.baseball,self.cfg.league,seed)
            except Exception:
                pool=None

        for eid in triggered:
            g=market[market["event_id"].astype(str)==eid].copy()
            if g.empty: continue
            try:
                analyzed,meta=analyze_baseball_event(g,self.baseball,self.cfg.league,self.cfg.recent_n,pool)
            except Exception:
                continue
            if analyzed.empty: continue
            best=analyzed.sort_values(["conservative_ev_roi","edge_pp"],ascending=False).iloc[0]
            es=state["events"][eid]
            old_grade=es.get("best_grade")
            old_edge=es.get("best_edge_pp")
            es["best_grade"]=str(best["grade"])
            es["best_edge_pp"]=float(best["edge_pp"])
            es["best_pick"]=str(best["display_pick"])
            es["last_reanalysis"]=now.isoformat()
            lineup_changed=bool(es.pop("lineup_changed",False))
            if old_grade != es["best_grade"] or lineup_changed:
                title="⚾ KBO/NPB 자동 재분석"
                if lineup_changed: title="⚾ 라인업/선발 변화 → 자동 재분석"
                text=format_alert(title,[
                    f'{es["home_team"]} - {es["away_team"]}',
                    f'픽: {es["best_pick"]}',
                    f'등급: {old_grade or "-"} → {es["best_grade"]}',
                    f'Edge: {old_edge if old_edge is not None else "-"} → {es["best_edge_pp"]:.1f}%p',
                    f'데이터 품질: {best.get("data_quality")}',
                    f'선발: {best.get("away_starter") or "미확인"} / {best.get("home_starter") or "미확인"}',
                ])
                self.notify(text); alerts.append(text)

        state["last_run"]=now.isoformat()
        self.store.save(state)
        return {
            "status":"ok",
            "league":self.cfg.league,
            "checked_at":now.isoformat(),
            "credits_remaining":state.get("credits_remaining"),
            "triggered_events":list(triggered),
            "alerts":alerts,
            "full_snapshot":full_due,
            "full_snapshot_threshold":threshold,
        }
