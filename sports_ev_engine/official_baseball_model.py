from __future__ import annotations
import math
import pandas as pd
from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.providers.official_baseball import canonical_english
from sports_ev_engine.reasoning_engine import (
    SignalLedger, build_counter_cases, scenario_assessment, baseball_scenarios, decision_fields
)

from .adaptive_model import apply_adaptive_layer

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
    for key in ("home_recent_form_factor","home_split_factor","home_vs_bullpen_factor",
                "home_vs_bullpen_exact_factor","home_lineup_platoon_factor","home_pitch_matchup_factor",
                "home_travel_rest_factor","home_bvp_factor"):
        if c.get(key) is not None: hm*=float(c[key])
    for key in ("away_recent_form_factor","away_split_factor","away_vs_bullpen_factor",
                "away_vs_bullpen_exact_factor","away_lineup_platoon_factor","away_pitch_matchup_factor",
                "away_travel_rest_factor","away_bvp_factor"):
        if c.get(key) is not None: am*=float(c[key])
    if c.get("away_starter_recent_factor") is not None: hm*=float(c["away_starter_recent_factor"])
    if c.get("home_starter_recent_factor") is not None: am*=float(c["home_starter_recent_factor"])
    # v3.1 Statcast/discipline/workload composite for the opposing starter.
    if c.get("away_starter_deep_factor") is not None: hm*=float(c["away_starter_deep_factor"])
    if c.get("home_starter_deep_factor") is not None: am*=float(c["home_starter_deep_factor"])
    if c.get("weather_factor") is not None:
        hm*=float(c["weather_factor"]); am*=float(c["weather_factor"])

    # More measured signals are available in v3.1, but the full context layer is
    # still prevented from overwhelming the independent scoring model.
    hm=max(base_hm*.84,min(base_hm*1.16,hm))
    am=max(base_am*.84,min(base_am*1.16,am))
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

    # Validate before clamping, blending or assigning a positive-EV label.
    for team, values in ((home, hs), (away, aws)):
        for key in ("runs_per_game", "runs_allowed_per_game", "games"):
            try:
                value = float(values.get(key))
            except (TypeError, ValueError):
                value = float("nan")
            valid = math.isfinite(value)
            if key == "games":
                valid = valid and 1 <= value <= 200 and value == int(value)
            else:
                valid = valid and 0 <= value <= 30
            if not valid:
                return pd.DataFrame(), {"status": "data_failed", "reason":
                    f"데이터 오류로 평가 보류: {team} {key}={values.get(key)!r}; 원본 열/단위 확인 필요"}

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
    if not all(math.isfinite(v) and 1.3 <= v <= 8.8 for v in (hm, am)):
        return pd.DataFrame(), {"status": "data_failed", "reason":
            f"예상 득점 모델 범위 이탈로 평가 보류: home={hm:.3f}, away={am:.3f}; 상한 고정 없이 입력 재검증"}
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
    labels={
        "starter_recent":"starter_recent_3_5","recent_form":"recent_team_form","bullpen":"bullpen_workload",
        "split":"team_platoon_split","velocity":"velocity_trend","weather":"park_weather",
        "plate_discipline":"whiff_chase_zone_contact","statcast_quality":"statcast_xwoba_barrel_hardhit",
        "batted_ball_regression":"gb_fb_hrfb_babip_regression","pitch_mix":"pitch_mix_arsenal",
        "starter_workload":"starter_pitchcount_rest_workload","bullpen_exact":"bullpen_exact_3day_pitchcount",
        "lineup_platoon_exact":"lineup_player_lr_ops","pitch_matchup":"pitch_type_hitter_matchup",
        "availability_news":"injury_return_transactions","lineup_change":"lineup_change_watch",
        "market_movement":"market_odds_movement","roof":"roof_open_closed","umpire":"home_plate_umpire",
        "travel_rest":"travel_timezone_rest","bvp":"batter_vs_pitcher","bullpen_manager":"bullpen_manager_pattern",
        "news_scan":"injury_rest_news_scan",
    }
    for key,label in labels.items():
        signal_ledger.add(label,bool(statuses.get(key)),"context" if statuses.get(key) else "neutral",0,.80 if statuses.get(key) else 0,
                          source=context.get("source","") or hs.get("source",""),
                          note="measured/observed signal wired into v3.1 context" if statuses.get(key) else "MISSING: source did not return a usable pregame value")
    signal_ledger.add("confirmed_lineup",bool(context.get("lineup_confirmed")),"context",0,.9,source=context.get("source","") or "")
    signal_ledger.add("confirmed_starters",bool(context.get("starter_confirmed")),"context",0,.9,source=context.get("source","") or "")

    # Pregame starter baselines persisted for objective post-game comparison.
    _hsr=(adv.get("home_starter_recent") or {})
    _asr=(adv.get("away_starter_recent") or {})
    def _avg_recent_ip(rec):
        vals=[]
        for x in (rec.get("starts") or []):
            try:
                v=float(x.get("ip"))
            except (TypeError,ValueError):
                continue
            vals.append(v)
        return sum(vals)/len(vals) if vals else None
    _home_expected_ip=_avg_recent_ip(_hsr)
    _away_expected_ip=_avg_recent_ip(_asr)
    _deep31=(adv.get("deep_v31") or {})
    _hb_exact=(_deep31.get("home_bullpen_exact") or {})
    _ab_exact=(_deep31.get("away_bullpen_exact") or {})

    rows=[]
    for _,r in event_market.iterrows():
        side=_side(r)
        if not side:continue
        point=None if r["market"]=="h2h" else float(r["point"])
        rw,rp,rl=_market_probs(r["market"],side,point,matrix)
        mp=float(r["consensus_prob"])
        fw,mw,raw_gap=_blend(rw,rp,mp,quality)
        fl=max(0,1-fw-rp)
        # Row-specific market movement from locally observed Odds API snapshots.
        deep31=(adv.get("deep_v31") or {})
        move_map=((deep31.get("market_movement") or {}).get("by_key") or {})
        point_val=None if r["market"]=="h2h" else float(r["point"])
        point_s="" if point_val is None else f"{point_val:g}"
        move_key=f'{r["market"]}|{r["selection"]}|{point_s}'
        move=(move_map.get(move_key) or {})
        move_pp=move.get("move_pp")
        row_unc=float(unc)
        if move_pp is not None:
            if float(move_pp)<=-2.0: row_unc+=.70
            elif float(move_pp)<=-1.0: row_unc+=.35
            elif float(move_pp)>=2.0: row_unc=max(2.5,row_unc-.20)
        ev=analyze_bet(float(r["best_odds"]),fw,rp,row_unc)
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
            "uncertainty_pp":row_unc,"market_move_pp":move_pp,"market_from_open_pp":move.get("from_open_pp"),"kelly_scaled":ev.kelly_scaled,"grade":grade,"sanity":sanity,
            "raw_market_gap_pp":raw_gap,"final_market_gap_pp":final_gap,"model_weight":mw,
            "data_quality":quality,"starter_confirmed":bool(context.get("starter_confirmed")),"lineup_confirmed":bool(context.get("lineup_confirmed")),
            "lineup_source":context.get("lineup_source") or context.get("source"),
            "lineup_fallback_used":bool(context.get("lineup_fallback_used")),
            "lineup_status":"CONFIRMED" if bool(context.get("lineup_confirmed")) else "PRE-LINEUP",
            "home_starter":context.get("home_starter"),"away_starter":context.get("away_starter"),
            "home_starter_era":(context.get("home_starter_stats") or {}).get("era"),"away_starter_era":(context.get("away_starter_stats") or {}).get("era"),
            "home_starter_whip":(context.get("home_starter_stats") or {}).get("whip"),"away_starter_whip":(context.get("away_starter_stats") or {}).get("whip"),
            "home_starter_expected_ip":_home_expected_ip,"away_starter_expected_ip":_away_expected_ip,
            "home_starter_recent_bb_pct":_hsr.get("bb_pct"),"away_starter_recent_bb_pct":_asr.get("bb_pct"),
            "home_starter_recent_k_pct":_hsr.get("k_pct"),"away_starter_recent_k_pct":_asr.get("k_pct"),
            "home_starter_recent_kbb_pct":_hsr.get("kbb_pct"),"away_starter_recent_kbb_pct":_asr.get("kbb_pct"),
            "home_bullpen_pitches_last3":_hb_exact.get("total_relief_pitches"),"away_bullpen_pitches_last3":_ab_exact.get("total_relief_pitches"),
            "home_lineup_strength":context.get("home_lineup_strength"),"away_lineup_strength":context.get("away_lineup_strength"),
            "home_season_rf":hs["runs_per_game"],"home_season_ra":hs["runs_allowed_per_game"],"away_season_rf":aws["runs_per_game"],"away_season_ra":aws["runs_allowed_per_game"],
            "home_recent_rf":hs.get("recent_runs_per_game"),"home_recent_ra":hs.get("recent_runs_allowed_per_game"),"away_recent_rf":aws.get("recent_runs_per_game"),"away_recent_ra":aws.get("recent_runs_allowed_per_game"),
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
            "plate_discipline_used":bool(statuses.get("plate_discipline")),"statcast_quality_used":bool(statuses.get("statcast_quality")),"statcast_fallback_used":bool((((context.get("advanced") or {}).get("deep_v31") or {}).get("statcast_fallback_used"))),
            "batted_ball_regression_used":bool(statuses.get("batted_ball_regression")),"pitch_mix_used":bool(statuses.get("pitch_mix")),
            "starter_workload_used":bool(statuses.get("starter_workload")),"bullpen_exact_used":bool(statuses.get("bullpen_exact")),
            "lineup_platoon_exact_used":bool(statuses.get("lineup_platoon_exact")),"pitch_matchup_used":bool(statuses.get("pitch_matchup")),
            "availability_news_used":bool(statuses.get("availability_news")),"lineup_change_used":bool(statuses.get("lineup_change")),
            "market_movement_used":bool(statuses.get("market_movement")),"roof_used":bool(statuses.get("roof")),
            "umpire_used":bool(statuses.get("umpire")),"travel_rest_used":bool(statuses.get("travel_rest")),
            "bvp_used":bool(statuses.get("bvp")),"bullpen_manager_used":bool(statuses.get("bullpen_manager")),
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
            uncertainty_pp=row_unc,signal_coverage=signal_ledger.coverage,stage=stage,
            advanced_completeness=completeness,
        )
        legacy_ok=grade in {"A","B","C"} and ev.conservative_ev_roi>0 and sanity not in {"OUTLIER_SHRUNK","HIGH_DISAGREEMENT"}
        d.update(decision_fields(ledger=signal_ledger,counter_cases=counter_cases,counter_risk=counter_risk,
                                 robust=robust,legacy_eligible=legacy_ok))
        d["parlay_eligible"]=bool(d["v3_parlay_eligible"]) and stage=="FINAL" and quality=="HIGH"
        rows.append(d)
    _family={"MLB":"baseball_mlb","KBO":"baseball_kbo","NPB":"baseball_npb"}.get(str(league).upper(),f"baseball_{str(league).lower()}")
    _frame=apply_adaptive_layer(pd.DataFrame(rows),_family)
    return _frame,{"status":"ok","home":home,"away":away,"data_quality":quality,"stage":stage,"home_expected_runs":hm,"away_expected_runs":am,"source":context.get("source") or hs.get("source"),"context":context}
