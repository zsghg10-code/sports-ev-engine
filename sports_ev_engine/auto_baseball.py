
from __future__ import annotations

import math
from datetime import datetime, timezone
import pandas as pd

from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.providers.sofascore_baseball import (
    SofaScoreBaseball, norm, extract_lineup_players, find_starting_pitcher,
    extract_pitcher_metrics,
)


def _finished(e):
    st = str((e.get("status") or {}).get("type","")).lower()
    hs = e.get("homeScore") or {}
    aws = e.get("awayScore") or {}
    h = hs.get("current") if isinstance(hs,dict) else None
    a = aws.get("current") if isinstance(aws,dict) else None
    return st in {"finished","afterextra","afterpenalties"} and h is not None and a is not None


def _score(e):
    hs = e.get("homeScore") or {}
    aws = e.get("awayScore") or {}
    h = hs.get("current") if isinstance(hs,dict) else None
    a = aws.get("current") if isinstance(aws,dict) else None
    return (float(h),float(a)) if h is not None and a is not None else (None,None)


def _recent_team_form(events, team_name, recent_n=10, decay=.90):
    tn = norm(team_name)
    rows=[]
    for e in events:
        if not _finished(e):
            continue
        hname = (e.get("homeTeam") or {}).get("name","")
        aname = (e.get("awayTeam") or {}).get("name","")
        h,a = norm(hname),norm(aname)
        if tn not in {h,a}:
            continue
        hs,as_ = _score(e)
        if hs is None:
            continue
        ts = int(e.get("startTimestamp") or 0)
        if h == tn:
            rf,ra = hs,as_
            home = True
        else:
            rf,ra = as_,hs
            home = False
        rows.append((ts,rf,ra,home))
    rows.sort(reverse=True)
    rows=rows[:int(recent_n)]
    if not rows:
        return None
    sw=rf=ra=win=0.0
    for i,(_,r_for,r_against,is_home) in enumerate(rows):
        w=decay**i
        sw+=w
        rf+=w*r_for
        ra+=w*r_against
        win+=w*(1 if r_for>r_against else 0)
    return {
        "runs_for":rf/sw,
        "runs_against":ra/sw,
        "win_pct":win/sw,
        "matches":len(rows),
    }


def _league_environment(events):
    vals=[]
    for e in events:
        if not _finished(e):
            continue
        h,a=_score(e)
        if h is not None:
            vals.extend([h,a])
    if not vals:
        return 4.4
    mean=sum(vals)/len(vals)
    return max(2.5,min(7.0,mean))


def _team_strength(events):
    # run-differential rating with shrinkage; enough to opponent-adjust recent form
    league_mean=_league_environment(events)
    totals={}
    for e in events:
        if not _finished(e):
            continue
        hn=(e.get("homeTeam") or {}).get("name","")
        an=(e.get("awayTeam") or {}).get("name","")
        hs,as_=_score(e)
        for t,rf,ra in ((hn,hs,as_),(an,as_,hs)):
            k=norm(t)
            d=totals.setdefault(k,{"rf":0.0,"ra":0.0,"n":0})
            d["rf"]+=rf; d["ra"]+=ra; d["n"]+=1
    out={}
    for k,d in totals.items():
        n=d["n"]
        shrink=8.0
        rf=(d["rf"]+shrink*league_mean)/(n+shrink)
        ra=(d["ra"]+shrink*league_mean)/(n+shrink)
        out[k]=(rf-ra)/max(league_mean,1e-6)
    return out


def _opponent_adjusted_form(events, team_name, strengths, recent_n=10, decay=.90):
    tn=norm(team_name)
    rows=[]
    for e in events:
        if not _finished(e):
            continue
        hn=(e.get("homeTeam") or {}).get("name","")
        an=(e.get("awayTeam") or {}).get("name","")
        h,a=norm(hn),norm(an)
        if tn not in {h,a}: continue
        hs,as_=_score(e)
        if h==tn:
            rf,ra,opp=hs,as_,a
        else:
            rf,ra,opp=as_,hs,h
        ts=int(e.get("startTimestamp") or 0)
        rows.append((ts,rf,ra,opp))
    rows.sort(reverse=True); rows=rows[:int(recent_n)]
    if not rows: return None
    sw=arf=ara=win=0.0
    for i,(_,rf,ra,opp) in enumerate(rows):
        w=decay**i
        opp_s=float(strengths.get(opp,0.0))
        # scoring against strong run-prevention opponent counts more
        arf += w*rf*math.exp(0.13*opp_s)
        # conceding to strong offense counts less
        ara += w*ra*math.exp(-0.13*opp_s)
        win += w*(1 if rf>ra else 0)
        sw += w
    return {"runs_for":arf/sw,"runs_against":ara/sw,"win_pct":win/sw,"matches":len(rows)}


def _starter_adjustment(metrics, league_mean):
    """
    Multiplicative expected-runs adjustment.
    Very conservative: starter info must not dominate the model.
    """
    if not metrics:
        return 1.0, 0.0
    era=metrics.get("era")
    whip=metrics.get("whip")
    adj=1.0
    confidence=0.0
    if isinstance(era,(int,float)) and 0.5 <= era <= 12:
        # Typical league ERAs hover around ~4; clamp effect to +/-12%.
        adj *= max(.88,min(1.12,1 + (era-4.0)*0.025))
        confidence += 0.6
    if isinstance(whip,(int,float)) and .5 <= whip <= 2.5:
        adj *= max(.94,min(1.06,1 + (whip-1.30)*0.10))
        confidence += 0.3
    kbb=metrics.get("kbb")
    if isinstance(kbb,(int,float)) and .2 <= kbb <= 10:
        adj *= max(.96,min(1.04,1 - (kbb-2.5)*0.012))
        confidence += 0.1
    return adj, min(confidence,1.0)


def _build_expected_runs(home_form, away_form, league_mean, home_starter=None, away_starter=None):
    # League mean is per team, not total.
    h_raw=(home_form["runs_for"] + away_form["runs_against"])/2
    a_raw=(away_form["runs_for"] + home_form["runs_against"])/2
    # modest home-field effect in baseball
    h_raw *= 1.025
    a_raw *= .985

    # Starting pitcher adjustment applies to opponent scoring.
    h_adj,hconf=_starter_adjustment(away_starter,league_mean)
    a_adj,aconf=_starter_adjustment(home_starter,league_mean)
    h_raw*=h_adj
    a_raw*=a_adj

    # shrink to league environment to limit small-sample overreaction
    h=.72*h_raw + .28*league_mean
    a=.72*a_raw + .28*league_mean
    return max(1.2,min(8.5,h)),max(1.2,min(8.5,a)),hconf,aconf


def _nbinom_pmf(k, mean, dispersion=4.5):
    # Var = mean + mean^2/r. Lower r => more baseball-like overdispersion.
    r=float(dispersion)
    p=r/(r+mean)
    return math.exp(
        math.lgamma(k+r)-math.lgamma(r)-math.lgamma(k+1)
        + r*math.log(p) + k*math.log(1-p)
    )


def score_matrix(home_mean, away_mean, max_runs=22):
    hp=[_nbinom_pmf(i,home_mean) for i in range(max_runs+1)]
    ap=[_nbinom_pmf(i,away_mean) for i in range(max_runs+1)]
    s_h=sum(hp); s_a=sum(ap)
    hp=[x/s_h for x in hp]; ap=[x/s_a for x in ap]
    return [[hp[i]*ap[j] for j in range(max_runs+1)] for i in range(max_runs+1)]


def _market_probs(matrix, market, side, point=None):
    win=push=loss=0.0
    for h,row in enumerate(matrix):
        for a,p in enumerate(row):
            if market=="h2h":
                margin=h-a
                result=margin if side=="home" else -margin
            elif market=="spreads":
                margin=h-a
                base=margin if side=="home" else -margin
                result=base + float(point or 0)
            elif market=="totals":
                total=h+a
                result=(total-float(point)) if side=="over" else (float(point)-total)
            else:
                continue
            if result>1e-9: win+=p
            elif result<-1e-9: loss+=p
            else: push+=p
    return win,push,loss


def _blend(raw_win, raw_push, market_prob, sample, quality):
    resolved=max(1e-9,1-raw_push)
    raw_cond=raw_win/resolved
    gap_pp=(raw_cond-market_prob)*100
    # Baseball public-data model: intentionally less aggressive than soccer model.
    w=.42
    if sample>=8: w=.48
    if quality=="HIGH": w=.52
    if abs(gap_pp)>20: w=min(w,.30)
    elif abs(gap_pp)>14: w=min(w,.38)
    final_cond=w*raw_cond+(1-w)*market_prob
    return final_cond*resolved,w,gap_pp


def _selection_side(r):
    sel=norm(r["selection"]); home=norm(r["home_team"]); away=norm(r["away_team"])
    if r["market"]=="h2h":
        if sel==home:return "home"
        if sel==away:return "away"
    if r["market"]=="spreads":
        if sel==home:return "home"
        if sel==away:return "away"
    if r["market"]=="totals":
        return "over" if "over" in str(r["selection"]).lower() else "under"
    return None


def build_league_pool(provider: SofaScoreBaseball, league: str, current_event=None):
    league=league.upper()
    seed=current_event
    tid=sid=None
    source=[]

    if seed:
        meta=provider.event_meta(seed)
        tid=meta.get("tournament_id")
        sid=meta.get("season_id")
        source.append("current_event")

    # v2.4.1: direct league/season discovery if current-event matching fails.
    if not tid or not sid:
        try:
            resolved=provider.resolve_league(league, datetime.now(timezone.utc).year)
            if resolved:
                t=resolved.get("tournament") or {}
                s=resolved.get("season") or {}
                tid=tid or t.get("id")
                sid=sid or s.get("id")
                source.append("unique_tournaments")
        except Exception:
            pass

    events=[]
    if tid and sid:
        events=provider.tournament_last_events(tid,sid,pages=8)
        if events:
            source.append("tournament_history")

    if len(events)<20:
        try:
            fallback=provider.recent_events_fallback(league,days=35)
        except Exception:
            fallback=[]
        by_id={e.get("id"):e for e in events if e.get("id") is not None}
        for e in fallback:
            if e.get("id") is not None:
                by_id[e["id"]]=e
        events=list(by_id.values())
        if fallback:
            source.append("daily_schedule_fallback")

    return {
        "league":league,
        "events":events,
        "tournament_id":tid,
        "season_id":sid,
        "source":source,
        "provider_error":provider.last_error,
    }


def _merge_team_history(provider, events, team_name):
    """Last-resort team-specific history lookup."""
    try:
        team_id=provider.find_team_id(team_name)
    except Exception:
        team_id=None
    if not team_id:
        return events
    try:
        extra=provider.team_last_events(team_id,pages=3)
    except Exception:
        extra=[]
    by_id={e.get("id"):e for e in events if e.get("id") is not None}
    for e in extra:
        if e.get("id") is not None:
            by_id[e["id"]]=e
    return list(by_id.values())


def analyze_baseball_event(event_market: pd.DataFrame, provider: SofaScoreBaseball, league: str, recent_n=10, pool=None):
    first=event_market.iloc[0]
    home=str(first["home_team"]); away=str(first["away_team"])
    sofa_event=provider.find_event(home,away,first["commence_time"])

    if pool is None:
        pool=build_league_pool(provider,league,sofa_event)
    events=pool.get("events",[])

    # If league history is missing/incomplete, resolve each team's own recent games.
    strengths=_team_strength(events) if events else {}
    hf=_opponent_adjusted_form(events,home,strengths,recent_n=recent_n) if events else None
    af=_opponent_adjusted_form(events,away,strengths,recent_n=recent_n) if events else None

    if not hf:
        events=_merge_team_history(provider,events,home)
    if not af:
        events=_merge_team_history(provider,events,away)

    if not events:
        detail=pool.get("provider_error") or provider.last_error or "no events returned"
        return pd.DataFrame(),{
            "status":"data_failed",
            "reason":f"SofaScore league/team history unavailable ({detail})"
        }

    strengths=_team_strength(events)
    hf=_opponent_adjusted_form(events,home,strengths,recent_n=recent_n)
    af=_opponent_adjusted_form(events,away,strengths,recent_n=recent_n)
    if not hf or not af:
        missing=[]
        if not hf: missing.append(home)
        if not af: missing.append(away)
        return pd.DataFrame(),{
            "status":"data_failed",
            "reason":"recent team form unavailable: "+", ".join(missing)
        }

    league_mean=_league_environment(events)
    lineup={"home":[],"away":[],"confirmed":False}
    home_sp=None; away_sp=None
    home_metrics={}; away_metrics={}
    lineup_available=False

    if sofa_event:
        meta=provider.event_meta(sofa_event)
        tid=meta.get("tournament_id") or pool.get("tournament_id")
        sid=meta.get("season_id") or pool.get("season_id")
        lp=provider.lineups(int(meta["event_id"]))
        lineup=extract_lineup_players(lp)
        lineup_available=bool(lineup["home"] or lineup["away"])
        hp=find_starting_pitcher(lineup["home"])
        ap=find_starting_pitcher(lineup["away"])
        if hp and hp.get("id") and tid and sid:
            home_sp=hp.get("name")
            home_metrics=extract_pitcher_metrics(provider.player_season_stats(hp["id"],tid,sid))
        if ap and ap.get("id") and tid and sid:
            away_sp=ap.get("name")
            away_metrics=extract_pitcher_metrics(provider.player_season_stats(ap["id"],tid,sid))

    starter_count=int(bool(home_sp))+int(bool(away_sp))
    if starter_count==2 and lineup.get("confirmed"):
        quality="HIGH"
    elif starter_count>=1 or lineup_available:
        quality="MEDIUM"
    else:
        quality="LOW"

    hmean,amean,hconf,aconf=_build_expected_runs(hf,af,league_mean,home_metrics,away_metrics)
    matrix=score_matrix(hmean,amean)
    sample=min(hf["matches"],af["matches"])

    # uncertainty explicitly reflects missing lineups/starters
    unc=4.0
    if sample<8: unc+=1.0
    if quality=="MEDIUM": unc+=1.0
    if quality=="LOW": unc+=2.5

    rows=[]
    for _,r in event_market.iterrows():
        side=_selection_side(r)
        if not side: continue
        point=None if r["market"]=="h2h" else float(r["point"])
        rw,rp,rl=_market_probs(matrix,r["market"],side,point)
        market_prob=float(r["consensus_prob"])
        fw,model_weight,raw_gap=_blend(rw,rp,market_prob,sample,quality)
        fl=max(0,1-fw-rp)
        ev=analyze_bet(float(r["best_odds"]),fw,rp,unc)
        resolved=max(1e-9,1-rp)
        final_gap=(fw/resolved-market_prob)*100

        sanity="OK"
        if abs(raw_gap)>20: sanity="OUTLIER_SHRUNK"
        elif abs(raw_gap)>14: sanity="HIGH_DISAGREEMENT"
        elif abs(raw_gap)>9: sanity="CHECK"

        grade=ev.grade
        # Never let incomplete baseball data masquerade as a high-confidence multi.
        if quality=="LOW" and grade in {"A","B"}: grade="C"
        if sanity=="OUTLIER_SHRUNK": grade="REVIEW"
        elif sanity=="HIGH_DISAGREEMENT" and grade=="A": grade="B"

        display=f'{home}-{away} | {r["selection"]}'
        if point is not None:
            display += f' {point:+g}' if r["market"]=="spreads" else f' {point:g}'

        d=r.to_dict()
        d.update({
            "sport_key":"baseball_kbo" if league.upper()=="KBO" else "baseball_npb",
            "league":league.upper(),
            "display_pick":display,
            "raw_independent_prob":rw,
            "model_win_prob":fw,
            "push_prob":rp,
            "model_lose_prob":fl,
            "break_even":ev.break_even,
            "edge_pp":ev.edge_pp,
            "ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,
            "uncertainty_pp":unc,
            "kelly_scaled":ev.kelly_scaled,
            "grade":grade,
            "sanity":sanity,
            "raw_market_gap_pp":raw_gap,
            "final_market_gap_pp":final_gap,
            "model_weight":model_weight,
            "data_quality":quality,
            "lineup_confirmed":bool(lineup.get("confirmed")),
            "home_starter":home_sp,
            "away_starter":away_sp,
            "home_starter_era":home_metrics.get("era"),
            "away_starter_era":away_metrics.get("era"),
            "home_starter_whip":home_metrics.get("whip"),
            "away_starter_whip":away_metrics.get("whip"),
            "home_recent_rf":hf["runs_for"],
            "home_recent_ra":hf["runs_against"],
            "away_recent_rf":af["runs_for"],
            "away_recent_ra":af["runs_against"],
            "home_form_matches":hf["matches"],
            "away_form_matches":af["matches"],
            "home_expected_runs":hmean,
            "away_expected_runs":amean,
            "league_mean_runs":league_mean,
        })
        rows.append(d)

    meta={
        "status":"ok",
        "home":home,"away":away,
        "data_quality":quality,
        "lineup_confirmed":bool(lineup.get("confirmed")),
        "home_starter":home_sp,"away_starter":away_sp,
        "home_metrics":home_metrics,"away_metrics":away_metrics,
        "home_form":hf,"away_form":af,
        "home_expected_runs":hmean,"away_expected_runs":amean,
        "league_mean_runs":league_mean,
    }
    return pd.DataFrame(rows),meta
