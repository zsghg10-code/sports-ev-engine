from __future__ import annotations
import math
import pandas as pd
from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.providers.official_baseball import canonical_english
from sports_ev_engine.reasoning_engine import (
    SignalLedger, build_counter_cases, scenario_assessment, baseball_scenarios, decision_fields
)


def _nbinom_pmf(k, mean, dispersion=5.0):
    r=float(dispersion)
    p=r/(r+mean)
    return math.exp(math.lgamma(k+r)-math.lgamma(r)-math.lgamma(k+1)+r*math.log(p)+k*math.log(1-p))


def _matrix(hm,am,max_runs=22):
    hp=[_nbinom_pmf(i,hm) for i in range(max_runs+1)]
    ap=[_nbinom_pmf(i,am) for i in range(max_runs+1)]
    sh,sa=sum(hp),sum(ap)
    hp=[x/sh for x in hp]; ap=[x/sa for x in ap]
    return [[hp[i]*ap[j] for j in range(max_runs+1)] for i in range(max_runs+1)]


def _market_probs(market,side,point,matrix):
    win=push=loss=0.0
    for h,row in enumerate(matrix):
        for a,p in enumerate(row):
            if market=="h2h": result=(h-a) if side=="home" else (a-h)
            elif market=="spreads": result=((h-a) if side=="home" else (a-h))+float(point or 0)
            elif market=="totals": result=(h+a-float(point)) if side=="over" else (float(point)-(h+a))
            else: continue
            if result>1e-9: win+=p
            elif result<-1e-9: loss+=p
            else: push+=p
    return win,push,loss


def _side(r):
    home=canonical_english(r["home_team"]); away=canonical_english(r["away_team"]); sel=canonical_english(r["selection"])
    if r["market"] in {"h2h","spreads"}:
        if sel==home:return "home"
        if sel==away:return "away"
    if r["market"]=="totals":
        return "over" if "over" in str(r["selection"]).lower() else "under"
    return None


def _blend(raw_win,raw_push,market_prob,quality):
    resolved=max(1e-9,1-raw_push)
    raw_cond=raw_win/resolved
    gap=(raw_cond-market_prob)*100
    weight={"HIGH":.52,"MEDIUM":.42,"LOW":.32}.get(quality,.38)
    if abs(gap)>18: weight=min(weight,.28)
    elif abs(gap)>12: weight=min(weight,.36)
    final=(weight*raw_cond+(1-weight)*market_prob)*resolved
    return final,weight,gap


def _starter_multiplier(starter, team_era):
    if not starter:return 1.0
    mult=1.0
    era=starter.get("era"); whip=starter.get("whip"); kbb=starter.get("kbb")
    if era is not None and team_era not in (None,0):
        ratio=max(.45,min(2.0,float(era)/float(team_era)))
        mult*=ratio**.28
    if whip is not None:
        mult*=max(.94,min(1.06,1+(float(whip)-1.30)*.09))
    if kbb is not None:
        mult*=max(.97,min(1.03,1-(float(kbb)-2.5)*.01))
    return max(.84,min(1.17,mult))


def _apply_context(hm,am,hs,aws,ctx):
    if not ctx:return hm,am
    base_hm,base_am=hm,am

    # Opposing starter season quality.
    hm*=_starter_multiplier(ctx.get("away_starter_stats") or {},aws.get("era"))
    am*=_starter_multiplier(ctx.get("home_starter_stats") or {},hs.get("era"))

    if str(ctx.get("league",""))=="NPB":
        hf=ctx.get("home_lineup_strength"); af=ctx.get("away_lineup_strength")
        if hf is not None: hm*=max(.90,min(1.10,float(hf)))
        if af is not None: am*=max(.90,min(1.10,float(af)))
    elif str(ctx.get("league",""))=="KBO":
        hw=ctx.get("home_lineup_strength"); aw=ctx.get("away_lineup_strength")
        if hw is not None and aw is not None:
            diff=max(-8.0,min(8.0,float(hw)-float(aw)))
            rel=math.exp(.008*diff)
            hm*=rel; am/=rel

    adv=ctx.get("advanced") or {}
    c=adv.get("components") or {}
    for key in ("home_recent_form_factor","home_split_factor","home_vs_bullpen_factor"):
        if c.get(key) is not None: hm*=float(c[key])
    for key in ("away_recent_form_factor","away_split_factor","away_vs_bullpen_factor"):
        if c.get(key) is not None: am*=float(c[key])
    if c.get("away_starter_recent_factor") is not None: hm*=float(c["away_starter_recent_factor"])
    if c.get("home_starter_recent_factor") is not None: am*=float(c["home_starter_recent_factor"])
    if c.get("weather_factor") is not None:
        hm*=float(c["weather_factor"]); am*=float(c["weather_factor"])

    # Season + lineup + advanced signals combined are capped to +/-14% from
    # the pre-context scoring environment.
    hm=max(base_hm*.86,min(base_hm*1.14,hm))
    am=max(base_am*.86,min(base_am*1.14,am))
    return hm,am



def analyze_official_event(event_market:pd.DataFrame,stats:dict,league:str,context:dict|None=None):
    first=event_market.iloc[0]
    home=canonical_english(first["home_team"]); away=canonical_english(first["away_team"])
    hs=stats.get(home); aws=stats.get(away)
    if not hs or not aws:
        missing=[]
        if not hs:missing.append(home)
        if not aws:missing.append(away)
        return pd.DataFrame(),{"status":"data_failed","reason":"official team stats unavailable: "+", ".join(missing)}

    hm=(float(hs["runs_per_game"])+float(aws["runs_allowed_per_game"]))/2*1.025
    am=(float(aws["runs_per_game"])+float(hs["runs_allowed_per_game"]))/2*.985

    hwp,awp=hs.get("win_pct"),aws.get("win_pct")
    if hwp is not None and awp is not None:
        gap=float(hwp)-float(awp); hm*=math.exp(.20*gap); am*=math.exp(-.20*gap)
    hr10,ar10=hs.get("recent10_win_pct"),aws.get("recent10_win_pct")
    if hr10 is not None and ar10 is not None:
        gap=float(hr10)-float(ar10); hm*=math.exp(.12*gap); am*=math.exp(-.12*gap)

    context=context or {}
    hm,am=_apply_context(hm,am,hs,aws,context)
    hm=max(1.3,min(8.8,hm)); am=max(1.3,min(8.8,am))
    matrix=_matrix(hm,am)

    stage=context.get("stage","PRE-LINEUP")
    if stage=="FINAL": quality,unc="HIGH",3.5
    elif stage in {"STARTER CONFIRMED","LINEUP CONFIRMED","DATA PARTIAL"}: quality,unc="MEDIUM",4.5
    else: quality,unc="LOW",5.8

    adv=context.get("advanced") or {}
    unc += float(adv.get("extra_uncertainty_pp") or 0)
    completeness=float(adv.get("advanced_completeness") or 0)
    if stage=="FINAL" and completeness < .55:
        quality="MEDIUM"

    # v3 audit ledger. These signals were already applied in _apply_context; the
    # ledger makes their availability and omissions explicit without double-counting.
    signal_ledger=SignalLedger()
    statuses=adv.get("statuses") or {}
    for key,label in (("starter_recent","starter_recent_3_5"),("recent_form","recent_team_form"),
                      ("bullpen","bullpen_workload"),("split","platoon_split"),
                      ("velocity","velocity_trend"),("weather","park_weather")):
        signal_ledger.add(label,bool(statuses.get(key)),"context" if statuses.get(key) else "neutral",0,.75 if statuses.get(key) else 0,
                          source=context.get("source","") or hs.get("source",""),
                          note="used by advanced baseball context" if statuses.get(key) else "not returned by wired source")
    signal_ledger.add("confirmed_lineup",bool(context.get("lineup_confirmed")),"context",0,.9,source=context.get("source","") or "")
    signal_ledger.add("confirmed_starters",bool(context.get("starter_confirmed")),"context",0,.9,source=context.get("source","") or "")

    rows=[]
    for _,r in event_market.iterrows():
        side=_side(r)
        if not side:continue
        point=None if r["market"]=="h2h" else float(r["point"])
        rw,rp,rl=_market_probs(r["market"],side,point,matrix)
        mp=float(r["consensus_prob"])
        fw,mw,raw_gap=_blend(rw,rp,mp,quality)
        fl=max(0,1-fw-rp)
        ev=analyze_bet(float(r["best_odds"]),fw,rp,unc)
        resolved=max(1e-9,1-rp); final_gap=(fw/resolved-mp)*100
        sanity="OUTLIER_SHRUNK" if abs(raw_gap)>18 else "HIGH_DISAGREEMENT" if abs(raw_gap)>12 else "CHECK" if abs(raw_gap)>8 else "OK"
        grade=ev.grade
        if quality!="HIGH" and grade=="A":grade="B"
        if stage!="FINAL" and grade=="A":grade="B"
        if stage=="PRE-LINEUP" and grade in {"A","B"}:grade="C"
        if sanity=="OUTLIER_SHRUNK":grade="REVIEW"

        d=r.to_dict()
        d.update({
            "league":str(league).upper(),"stage":stage,
            "display_pick":f'{home}-{away} | {r["selection"]}'+("" if point is None else (f" {point:+g}" if r["market"]=="spreads" else f" {point:g}")),
            "raw_independent_prob":rw,"model_win_prob":fw,"push_prob":rp,"model_lose_prob":fl,
            "break_even":ev.break_even,"edge_pp":ev.edge_pp,"ev_roi":ev.ev_roi,"conservative_ev_roi":ev.conservative_ev_roi,
            "uncertainty_pp":unc,"kelly_scaled":ev.kelly_scaled,"grade":grade,"sanity":sanity,
            "raw_market_gap_pp":raw_gap,"final_market_gap_pp":final_gap,"model_weight":mw,
            "data_quality":quality,"starter_confirmed":bool(context.get("starter_confirmed")),"lineup_confirmed":bool(context.get("lineup_confirmed")),
            "home_starter":context.get("home_starter"),"away_starter":context.get("away_starter"),
            "home_starter_era":(context.get("home_starter_stats") or {}).get("era"),"away_starter_era":(context.get("away_starter_stats") or {}).get("era"),
            "home_starter_whip":(context.get("home_starter_stats") or {}).get("whip"),"away_starter_whip":(context.get("away_starter_stats") or {}).get("whip"),
            "home_lineup_strength":context.get("home_lineup_strength"),"away_lineup_strength":context.get("away_lineup_strength"),
            "home_recent_rf":hs["runs_per_game"],"home_recent_ra":hs["runs_allowed_per_game"],"away_recent_rf":aws["runs_per_game"],"away_recent_ra":aws["runs_allowed_per_game"],
            "home_form_matches":hs["games"],"away_form_matches":aws["games"],"home_expected_runs":hm,"away_expected_runs":am,
            "home_win_pct":hwp,"away_win_pct":awp,"home_recent10":hr10,"away_recent10":ar10,
            "advanced_completeness": float((context.get("advanced") or {}).get("advanced_completeness") or 0),
            "advanced_used": int((context.get("advanced") or {}).get("advanced_used") or 0),
            "recent_form_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("recent_form")),
            "starter_recent_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("starter_recent")),
            "velocity_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("velocity")),
            "bullpen_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("bullpen")),
            "split_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("split")),
            "weather_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("weather")),
            "weather_temp_c": ((context.get("advanced") or {}).get("weather") or {}).get("temperature_c"),
            "weather_wind_kmh": ((context.get("advanced") or {}).get("weather") or {}).get("wind_kmh"),
            "weather_precip_mm": ((context.get("advanced") or {}).get("weather") or {}).get("precip_mm"),
            "source":context.get("source") or hs.get("source"),
        })

        scenario_rows=baseball_scenarios(
            hm,am,lambda mm:_market_probs(r["market"],side,point,mm),_matrix
        )
        robust=scenario_assessment(
            odds=float(r["best_odds"]),market_prob=mp,model_weight=mw,scenarios=scenario_rows,
            base_ev=ev.ev_roi,sanity=sanity,data_ready=(quality in {"HIGH","MEDIUM"}),
            lineup_required=True,lineup_confirmed=bool(context.get("lineup_confirmed")) and stage=="FINAL",
        )
        counter_cases,counter_risk=build_counter_cases(
            sample_matches=min(int(hs.get("games") or 0),int(aws.get("games") or 0)),
            lineup_confirmed=bool(context.get("lineup_confirmed")),sanity=sanity,
            uncertainty_pp=unc,signal_coverage=signal_ledger.coverage,stage=stage,
            advanced_completeness=completeness,
        )
        legacy_ok=grade in {"A","B","C"} and ev.conservative_ev_roi>0 and sanity not in {"OUTLIER_SHRUNK","HIGH_DISAGREEMENT"}
        d.update(decision_fields(ledger=signal_ledger,counter_cases=counter_cases,counter_risk=counter_risk,
                                 robust=robust,legacy_eligible=legacy_ok))
        d["parlay_eligible"]=bool(d["v3_parlay_eligible"]) and stage=="FINAL" and quality=="HIGH"
        rows.append(d)
    return pd.DataFrame(rows),{"status":"ok","home":home,"away":away,"data_quality":quality,"stage":stage,"home_expected_runs":hm,"away_expected_runs":am,"source":context.get("source") or hs.get("source"),"context":context}
