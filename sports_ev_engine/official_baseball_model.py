from __future__ import annotations
import math
import pandas as pd
from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.providers.official_baseball import canonical_english
from sports_ev_engine.reasoning_engine import (
    SignalLedger, build_counter_cases, scenario_assessment, baseball_scenarios, decision_fields,
    final_probability_assessment
)

from .adaptive_model import apply_adaptive_layer

# v3.4.22 audit config: keep the legacy v3.4.21 dispersion unchanged until a
# sufficiently large historical score sample is available to estimate it. Mean
# run construction and variance/dispersion are now explicit separate concerns.
BASEBALL_SCORE_DISTRIBUTION = {
    "family":"negative_binomial",
    "dispersion":5.0,
    "source":"legacy_v3.4.21_fixed_not_refit",
}

def _nbinom_pmf(k, mean, dispersion=None):
    r=float(BASEBALL_SCORE_DISTRIBUTION["dispersion"] if dispersion is None else dispersion)
    p=r/(r+mean)
    return math.exp(math.lgamma(k+r)-math.lgamma(r)-math.lgamma(k+1)+r*math.log(p)+k*math.log(1-p))

def _matrix(hm,am,max_runs=22,dispersion=None):
    hp=[_nbinom_pmf(i,hm,dispersion) for i in range(max_runs+1)]
    ap=[_nbinom_pmf(i,am,dispersion) for i in range(max_runs+1)]
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


def _apply_context(hm,am,hs,aws,ctx,recent_pair=(1.0,1.0),return_audit=False):
    """Apply run-rate context and optionally return auditable factor contributions.

    Factors are grouped by information family so the UI can show where expected
    runs moved. Multiplication is equivalent to v3.4.21; only the bookkeeping and
    duplicate-recent-form audit are new.
    """
    ctx=ctx or {}; base_hm,base_am=float(hm),float(am); h=float(hm); a=float(am); audit=[]
    def snap(name,bh,ba,note=""):
        audit.append({"factor":name,"home_before":bh,"home_after":h,"home_delta":h-bh,
                      "away_before":ba,"away_after":a,"away_delta":a-ba,"note":note})
    def group(name,fn,note=""):
        nonlocal h,a
        bh,ba=h,a; fn(); snap(name,bh,ba,note)

    if not ctx:
        return (h,a,audit) if return_audit else (h,a)

    def starter():
        nonlocal h,a
        h*=_starter_multiplier(ctx.get("away_starter_stats") or {},aws.get("era"))
        a*=_starter_multiplier(ctx.get("home_starter_stats") or {},hs.get("era"))
        c=((ctx.get("advanced") or {}).get("components") or {})
        if c.get("away_starter_recent_factor") is not None:h*=float(c["away_starter_recent_factor"])
        if c.get("home_starter_recent_factor") is not None:a*=float(c["home_starter_recent_factor"])
        if c.get("away_starter_vs_opponent_factor") is not None:h*=float(c["away_starter_vs_opponent_factor"])
        if c.get("home_starter_vs_opponent_factor") is not None:a*=float(c["home_starter_vs_opponent_factor"])
    group("starter",starter,"season starter + recent 3-5 + starter-vs-opponent (sample-shrunk upstream)")

    adv=ctx.get("advanced") or {}; c=adv.get("components") or {}
    def recent():
        nonlocal h,a
        h*=float(recent_pair[0]); a*=float(recent_pair[1])
        if c.get("home_recent_form_factor") is not None:h*=float(c["home_recent_form_factor"])
        if c.get("away_recent_form_factor") is not None:a*=float(c["away_recent_form_factor"])
    group("recent_form",recent,"recent10 strength + measured recent team form; MLB totals ensemble duplicate suppressed downstream")

    def bullpen():
        nonlocal h,a
        for key in ("home_vs_bullpen_factor","home_vs_bullpen_exact_factor"):
            if c.get(key) is not None:h*=float(c[key])
        for key in ("away_vs_bullpen_factor","away_vs_bullpen_exact_factor"):
            if c.get(key) is not None:a*=float(c[key])
    group("bullpen",bullpen,"bullpen workload/availability")

    def platoon_lineup():
        nonlocal h,a
        if str(ctx.get("league",""))=="NPB":
            hf=ctx.get("home_lineup_strength"); af=ctx.get("away_lineup_strength")
            if hf is not None:h*=max(.90,min(1.10,float(hf)))
            if af is not None:a*=max(.90,min(1.10,float(af)))
        elif str(ctx.get("league",""))=="KBO":
            hw=ctx.get("home_lineup_strength"); aw=ctx.get("away_lineup_strength")
            if hw is not None and aw is not None:
                diff=max(-8.0,min(8.0,float(hw)-float(aw))); rel=math.exp(.008*diff); h*=rel; a/=rel
        for key in ("home_split_factor","home_lineup_platoon_factor","home_pitch_matchup_factor","home_bvp_factor"):
            if c.get(key) is not None:h*=float(c[key])
        for key in ("away_split_factor","away_lineup_platoon_factor","away_pitch_matchup_factor","away_bvp_factor"):
            if c.get(key) is not None:a*=float(c[key])
    group("platoon_lineup",platoon_lineup,"team split + confirmed lineup platoon/pitch matchup/BvP")

    def deep():
        nonlocal h,a
        if c.get("away_starter_deep_factor") is not None:h*=float(c["away_starter_deep_factor"])
        if c.get("home_starter_deep_factor") is not None:a*=float(c["home_starter_deep_factor"])
    group("statcast_deep",deep,"Statcast/discipline/pitch-quality composite")

    def weather():
        nonlocal h,a
        if c.get("weather_factor") is not None:h*=float(c["weather_factor"]);a*=float(c["weather_factor"])
    group("weather_park",weather,"park/weather environment")

    def rest():
        nonlocal h,a
        if c.get("home_travel_rest_factor") is not None:h*=float(c["home_travel_rest_factor"])
        if c.get("away_travel_rest_factor") is not None:a*=float(c["away_travel_rest_factor"])
    group("rest_travel",rest,"travel/time-zone/rest")

    bh,ba=h,a
    h=max(base_hm*.84,min(base_hm*1.16,h)); a=max(base_am*.84,min(base_am*1.16,a))
    snap("context_cap",bh,ba,"legacy ±16% context cap retained")
    return (h,a,audit) if return_audit else (h,a)



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
    _run_audit=[{"factor":"base_expected_runs","home_before":None,"home_after":hm,"home_delta":None,
                 "away_before":None,"away_after":am,"away_delta":None,
                 "note":"season runs scored/allowed + legacy home/away run constants"}]

    hwp,awp=hs.get("win_pct"),aws.get("win_pct")
    if hwp is not None and awp is not None:
        _bh,_ba=hm,am; gap=float(hwp)-float(awp); hm*=math.exp(.20*gap); am*=math.exp(-.20*gap)
        _run_audit.append({"factor":"season_strength","home_before":_bh,"home_after":hm,"home_delta":hm-_bh,
                           "away_before":_ba,"away_after":am,"away_delta":am-_ba,"note":"season win-pct strength adjustment"})
    hr10,ar10=hs.get("recent10_win_pct"),aws.get("recent10_win_pct")
    _recent_pair=(1.0,1.0)
    if hr10 is not None and ar10 is not None:
        gap=float(hr10)-float(ar10); _recent_pair=(math.exp(.12*gap),math.exp(-.12*gap))

    context=context or {}
    hm,am,_ctx_audit=_apply_context(hm,am,hs,aws,context,recent_pair=_recent_pair,return_audit=True)
    _run_audit.extend(_ctx_audit)
    _run_audit.append({"factor":"final_expected_runs","home_before":None,"home_after":hm,"home_delta":None,
                       "away_before":None,"away_after":am,"away_delta":None,"note":"final mean run model before score distribution"})
    # Team expected runs can legitimately fall below 1.3 in extreme pitcher/park
    # matchups. v3.4.20 used 1.3 as a hard lower gate, which incorrectly rejected
    # otherwise valid MLB events. Gross source corruption is caught above at the
    # team-stat level; this is only a final numerical safety rail.
    if not all(math.isfinite(v) and 0.35 <= v <= 10.0 for v in (hm, am)):
        return pd.DataFrame(), {"status": "data_failed", "reason":
            f"예상 득점 모델 범위 이탈로 평가 보류: home={hm:.3f}, away={am:.3f}; 원본 팀 집계/단위 확인 필요"}
    _dispersion=float(BASEBALL_SCORE_DISTRIBUTION["dispersion"])
    matrix=_matrix(hm,am,dispersion=_dispersion)

    stage=context.get("stage","PRE-LINEUP")
    if stage=="FINAL": quality,unc="HIGH",3.5
    elif stage in {"STARTER CONFIRMED","LINEUP CONFIRMED","DATA PARTIAL"}: quality,unc="MEDIUM",4.5
    else: quality,unc="LOW",5.8

    adv=context.get("advanced") or {}
    unc += float(adv.get("extra_uncertainty_pp") or 0)
    completeness=float(adv.get("advanced_completeness") or 0)
    # FINAL is a source-state: both starters + both 1-9 batting orders confirmed.
    # Missing advanced metrics are already charged through extra_uncertainty_pp and
    # advanced_completeness; do not double-penalize them by downgrading FINAL.

    # v3 audit ledger. These signals were already applied in _apply_context; the
    # ledger makes their availability and omissions explicit without double-counting.
    signal_ledger=SignalLedger()
    statuses=adv.get("statuses") or {}
    labels={
        "starter_recent":"starter_recent_3_5","starter_vs_opponent":"starter_vs_opponent_team","recent_form":"recent_team_form","bullpen":"bullpen_workload",
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
    _hsv=(adv.get("home_starter_vs_opponent") or {})
    _asv=(adv.get("away_starter_vs_opponent") or {})
    _hbp_adv=(adv.get("home_bullpen") or {})
    _abp_adv=(adv.get("away_bullpen") or {})
    _hrecent=(adv.get("home_recent") or {})
    _arecent=(adv.get("away_recent") or {})
    def _avg_recent_ip(rec):
        vals=[]
        for x in (rec.get("starts") or []):
            try:
                v=float(x.get("ip"))
            except (TypeError,ValueError):
                continue
            vals.append(v)
        if vals:
            return sum(vals)/len(vals)
        try:
            g=float(rec.get("games") or 0); ip=float(rec.get("ip") or 0)
            return ip/g if g>0 and ip>=0 else None
        except (TypeError,ValueError):
            return None
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
            "raw_independent_prob":rw,"raw_push_prob":rp,"model_win_prob":fw,"push_prob":rp,"model_lose_prob":fl,
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
            "home_starter_vs_opponent_games":_hsv.get("games"),"away_starter_vs_opponent_games":_asv.get("games"),
            "home_starter_vs_opponent_ip":_hsv.get("ip"),"away_starter_vs_opponent_ip":_asv.get("ip"),
            "home_starter_vs_opponent_era":_hsv.get("era"),"away_starter_vs_opponent_era":_asv.get("era"),
            "home_starter_vs_opponent_kbb_pct":_hsv.get("kbb_pct"),"away_starter_vs_opponent_kbb_pct":_asv.get("kbb_pct"),
            "home_starter_vs_opponent_team":_hsv.get("opponent"),"away_starter_vs_opponent_team":_asv.get("opponent"),
            "home_bullpen_pitches_last3":(_hbp_adv.get("total_relief_pitches") if _hbp_adv.get("total_relief_pitches") is not None else _hb_exact.get("total_relief_pitches")),
            "away_bullpen_pitches_last3":(_abp_adv.get("total_relief_pitches") if _abp_adv.get("total_relief_pitches") is not None else _ab_exact.get("total_relief_pitches")),
            "home_bullpen_relief_ip_last3":_hbp_adv.get("relief_ip_last3"),"away_bullpen_relief_ip_last3":_abp_adv.get("relief_ip_last3"),
            "home_bullpen_exact":bool(_hbp_adv.get("exact")),"away_bullpen_exact":bool(_abp_adv.get("exact")),
            "home_recent_runs_for":_hrecent.get("runs_for_per_game"),"home_recent_runs_against":_hrecent.get("runs_against_per_game"),
            "away_recent_runs_for":_arecent.get("runs_for_per_game"),"away_recent_runs_against":_arecent.get("runs_against_per_game"),
            "home_lineup_strength":context.get("home_lineup_strength"),"away_lineup_strength":context.get("away_lineup_strength"),
            "home_season_rf":hs["runs_per_game"],"home_season_ra":hs["runs_allowed_per_game"],"away_season_rf":aws["runs_per_game"],"away_season_ra":aws["runs_allowed_per_game"],
            "home_recent_rf":hs.get("recent_runs_per_game"),"home_recent_ra":hs.get("recent_runs_allowed_per_game"),"away_recent_rf":aws.get("recent_runs_per_game"),"away_recent_ra":aws.get("recent_runs_allowed_per_game"),
            "home_form_matches":hs["games"],"away_form_matches":aws["games"],"home_expected_runs":hm,"away_expected_runs":am,
            "expected_runs_adjustments":_run_audit,"score_distribution_family":BASEBALL_SCORE_DISTRIBUTION["family"],
            "score_distribution_dispersion":_dispersion,"score_distribution_dispersion_source":BASEBALL_SCORE_DISTRIBUTION["source"],
            "home_win_pct":hwp,"away_win_pct":awp,"home_recent10":hr10,"away_recent10":ar10,
            "advanced_completeness": float((context.get("advanced") or {}).get("advanced_completeness") or 0),
            "advanced_used": int((context.get("advanced") or {}).get("advanced_used") or 0),
            "recent_form_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("recent_form")),
            "starter_recent_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("starter_recent")),
            "starter_vs_opponent_used": bool(((context.get("advanced") or {}).get("statuses") or {}).get("starter_vs_opponent")),
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
            hm,am,lambda mm:_market_probs(r["market"],side,point,mm),lambda h,a:_matrix(h,a,dispersion=_dispersion)
        )
        robust=scenario_assessment(
            odds=float(r["best_odds"]),market_prob=mp,model_weight=mw,scenarios=scenario_rows,
            base_ev=ev.ev_roi,sanity=sanity,data_ready=(quality in {"HIGH","MEDIUM"}),
            lineup_required=True,lineup_confirmed=bool(context.get("lineup_confirmed")),
        )
        counter_cases,counter_risk=build_counter_cases(
            sample_matches=min(int(hs.get("games") or 0),int(aws.get("games") or 0)),
            lineup_confirmed=bool(context.get("lineup_confirmed")),sanity=sanity,
            uncertainty_pp=row_unc,signal_coverage=None,stage=stage,
            advanced_completeness=completeness,
        )
        legacy_ok=grade in {"A","B","C"} and ev.conservative_ev_roi>0 and sanity not in {"OUTLIER_SHRUNK","HIGH_DISAGREEMENT"}
        d.update(decision_fields(ledger=signal_ledger,counter_cases=counter_cases,counter_risk=counter_risk,
                                 robust=robust,legacy_eligible=legacy_ok))
        d["parlay_eligible"]=bool(d["v3_parlay_eligible"]) and stage=="FINAL" and quality=="HIGH"
        rows.append(d)
    _family={"MLB":"baseball_mlb","KBO":"baseball_kbo","NPB":"baseball_npb"}.get(str(league).upper(),f"baseball_{str(league).lower()}")
    _frame=apply_adaptive_layer(pd.DataFrame(rows),_family)

    # v3.4.22: calibration/coherence is not allowed to leave behind a stale
    # pre-calibration ROBUST label. Rebuild stress probabilities around the final
    # displayed conditional probability, preserving structural scenario deltas.
    for i,row in _frame.iterrows():
        side=_side(row); point=None if row["market"]=="h2h" else float(row["point"])
        base_raw_w,base_raw_p,_=_market_probs(row["market"],side,point,matrix)
        base_raw_q=base_raw_w/max(1e-9,1-base_raw_p)
        final_push=max(0.0,float(row.get("push_prob") or 0.0)); resolved=max(1e-9,1-final_push)
        final_q=float(row.get("model_win_prob"))/resolved
        scenario_raw=baseball_scenarios(hm,am,lambda mm:_market_probs(row["market"],side,point,mm),lambda h,a:_matrix(h,a,dispersion=_dispersion))
        final_scenarios=[]
        for sw,sp,label in scenario_raw:
            sr=max(1e-9,1-sp); sq=sw/sr
            # Keep the calibrated final probability as the centre while retaining
            # the structural run-model sensitivity around it. Cap only to [0,1].
            fq=max(1e-6,min(1-1e-6,final_q+(sq-base_raw_q)))
            final_scenarios.append((fq*(1-sp),sp,label))
        forced=None
        if bool(row.get("mlb_totals_market_disagreement_gate")):
            forced=("MLB totals raw-market divergence exceeds "
                    f"{float(row.get('mlb_totals_divergence_threshold_pp') or 0):.1f}pp without sufficient historical calibration evidence")
        elif str(row.get("ensemble_gate") or "")=="REVIEW" and not (
                _family=="baseball_mlb" and str(row.get("market"))=="totals" and bool(row.get("mlb_totals_calibration_evidence_sufficient"))):
            forced="ensemble component disagreement requires review"
        robust=final_probability_assessment(
            odds=float(row["best_odds"]),final_win_prob=float(row["model_win_prob"]),final_push_prob=final_push,
            uncertainty_pp=float(row.get("uncertainty_pp") or 0),scenario_probabilities=final_scenarios,
            data_ready=(quality in {"HIGH","MEDIUM"}),lineup_required=True,lineup_confirmed=bool(context.get("lineup_confirmed")),
            forced_review_reason=forced,
        )
        _mlb_totals_evidence=(_family=="baseball_mlb" and str(row.get("market"))=="totals" and bool(row.get("mlb_totals_calibration_evidence_sufficient")))
        _final_ev=analyze_bet(float(row["best_odds"]),float(row["model_win_prob"]),final_push,float(row.get("uncertainty_pp") or 0))
        _final_grade=_final_ev.grade
        if quality!="HIGH" and _final_grade=="A":_final_grade="B"
        if stage!="FINAL" and _final_grade=="A":_final_grade="B"
        if stage=="PRE-LINEUP" and _final_grade in {"A","B"}:_final_grade="C"
        if str(row.get("sanity") or "")=="OUTLIER_SHRUNK" and not _mlb_totals_evidence:_final_grade="REVIEW"
        _frame.at[i,"grade"]=_final_grade
        legacy_ok=(float(row.get("ev_roi") or 0)>0 and float(row.get("conservative_ev_roi") or 0)>0 and _final_grade in {"A","B","C"})
        status=robust.get("robust_status")
        candidate=status in {"ROBUST","SENSITIVE"} and legacy_ok
        parlay=status=="ROBUST" and bool(robust.get("robust_parlay_eligible")) and legacy_ok and str(row.get("counter_case_risk") or "")!="HIGH"
        for k,v in robust.items(): _frame.at[i,k]=v
        _frame.at[i,"v3_decision_status"]=status
        _frame.at[i,"v3_candidate"]=candidate
        _frame.at[i,"v3_parlay_eligible"]=parlay
        _frame.at[i,"parlay_eligible"]=bool(parlay) and stage=="FINAL" and quality=="HIGH"
        _frame.at[i,"final_decision_recomputed"]=True
        _frame.at[i,"final_downgrade_reason"]=robust.get("robust_reason") if status in {"REVIEW","PASS","SENSITIVE","FRAGILE"} else ""
        _frame.at[i,"final_market_gap_pp"]=(final_q-float(row.get("consensus_prob")))*100
    return _frame,{"status":"ok","home":home,"away":away,"data_quality":quality,"stage":stage,"home_expected_runs":hm,"away_expected_runs":am,"source":context.get("source") or hs.get("source"),"context":context}
