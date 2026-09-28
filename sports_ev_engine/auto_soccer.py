from __future__ import annotations
from .adaptive_model import apply_adaptive_layer
import math
from datetime import datetime, timezone
import pandas as pd

from sports_ev_engine.models.soccer_auto import score_matrix, price_from_matrix, norm_name
from sports_ev_engine.models.elo import build_elo, opponent_adjusted_form
from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.national_policy import scenario_matrices, assess, POLICY_ID
from sports_ev_engine.free_national import adjust_lambdas
from sports_ev_engine.review_policy import review_reason, REVIEW_STATES
from sports_ev_engine.reasoning_engine import (
    apply_soccer_context, build_counter_cases, scenario_assessment,
    soccer_scenarios, decision_fields,
)

def _side_for_row(r):
    home=norm_name(r["home_team"])
    away=norm_name(r["away_team"])
    sel=norm_name(r["selection"])
    if r["market"]=="h2h":
        if sel==home:return "home"
        if sel==away:return "away"
        if "draw" in sel:return "draw"
    elif r["market"]=="totals":
        return "over" if "over" in sel else "under"
    elif r["market"]=="spreads":
        if sel==home:return "home"
        if sel==away:return "away"
    return None

def _cutoff_ts(iso):
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z","+00:00")).timestamp())
    except Exception:
        return None

def _build_lambdas(home_form, away_form, home_elo, away_elo, goal_mean=2.55, home_adv=55.0):
    # opponent-adjusted recent form
    h_raw=max(0.18,(home_form["gf"]+away_form["ga"])/2 + 0.10)
    a_raw=max(0.18,(away_form["gf"]+home_form["ga"])/2)

    elo_diff=(home_elo+home_adv)-away_elo
    elo_mult=math.exp(elo_diff/1000.0)
    home=h_raw*(elo_mult**0.38)
    away=a_raw*(elo_mult**-0.38)

    # PPG is only a mild correction; Elo already handles opponent strength.
    ppg_gap=home_form["ppg"]-away_form["ppg"]
    home*=math.exp(0.035*ppg_gap)
    away*=math.exp(-0.035*ppg_gap)

    total=home+away
    # Strong shrinkage toward the competition environment.
    desired=0.68*total+0.32*goal_mean
    factor=desired/max(total,1e-9)
    return max(.15,home*factor),max(.15,away*factor)

def _blend_probability(raw_win, raw_push, market_prob, sample_matches, raw_gap_pp):
    """
    Market is a calibration prior, not the answer.
    More independent weight is allowed when sample quality is better.
    Extreme model/market gaps receive more market shrinkage.
    """
    sample=min(int(sample_matches),8)
    independent_weight=0.42 + 0.025*sample  # 0.52..0.62 around 4-8 games
    independent_weight=min(0.62,max(0.48,independent_weight))

    gap=abs(raw_gap_pp)
    if gap>25:
        independent_weight=min(independent_weight,0.28)
    elif gap>18:
        independent_weight=min(independent_weight,0.36)
    elif gap>12:
        independent_weight=min(independent_weight,0.45)

    resolved=max(1e-9,1.0-raw_push)
    raw_cond=raw_win/resolved
    final_cond=independent_weight*raw_cond + (1-independent_weight)*market_prob
    final_win=final_cond*resolved
    return max(0.0,min(resolved,final_win)), independent_weight

def analyze_event(event_rows, competition_pool, recent_n=6):
    first=event_rows.iloc[0]
    home=first["home_team"]
    away=first["away_team"]
    international=bool(competition_pool.get("international"))
    names=competition_pool.get("team_names",{})
    home_lookup=names.get(norm_name(home),home)
    away_lookup=names.get(norm_name(away),away)
    cutoff_ts=_cutoff_ts(first.get("commence_time"))
    if cutoff_ts is None:
        return pd.DataFrame(),{"status":"data_failed","reason":"경기 시작 시각 미확인"}
    # Only regulation-time fixtures before this event; never reuse future-contaminated Elo.
    fixtures=[f for f in competition_pool.get("fixtures",[]) if f.get("fixture",{}).get("status",{}).get("short")=="FT" and 0<int(f.get("fixture",{}).get("timestamp") or 0)<cutoff_ts]
    ratings=build_elo(fixtures, home_adv=20.0 if international else 55.0)

    hf=opponent_adjusted_form(fixtures,home_lookup,ratings,cutoff_ts,recent_n=recent_n)
    af=opponent_adjusted_form(fixtures,away_lookup,ratings,cutoff_ts,recent_n=recent_n)

    if not hf or not af:
        missing=[]
        if not hf:missing.append(home)
        if not af:missing.append(away)
        return pd.DataFrame(),{
            "status":"data_failed","home":home,"away":away,
            "reason":"same-competition completed fixtures unavailable: "+", ".join(missing)
        }

    he=ratings.get(norm_name(home_lookup),1500.0)
    ae=ratings.get(norm_name(away_lookup),1500.0)
    ctx=competition_pool.get("manual_context",{})
    neutral=bool(ctx and all(c.get("neutral") for c in ctx.values()))
    hl,al=_build_lambdas(hf,af,he,ae,home_adv=(0.0 if neutral or competition_pool.get("venue_unknown") else 20.0) if international else 55.0)
    lineup_ok=False; evidence_note=""
    if international:hl,al,lineup_ok,evidence_note=adjust_lambdas(hl,al,competition_pool)

    # v3 context layer: xG, lineup/player importance, injuries and rest are used only
    # when the provider actually returned them. Missing deep signals are never imputed.
    deep_ctx=competition_pool.get("event_context") or {"deep_context_attempted":False}
    hl,al,context_unc,signal_ledger=apply_soccer_context(
        hl,al,deep_ctx,hf,af,international=international
    )
    # Deep provider context can confirm the XI for both club and international matches.
    # Previously A-match (international=True) ignored a valid API-Football startXI.
    if deep_ctx.get("lineup_confirmed"):
        lineup_ok=True
        if deep_ctx.get("lineup_source"):
            evidence_note=(evidence_note + "; " if evidence_note else "") + f"확정 라인업 반영: {deep_ctx.get('lineup_source')}"
    matrix=score_matrix(hl,al)
    scenarios=scenario_matrices(hl,al) if international else []

    sample=min(hf["matches"],af["matches"])
    base_unc=3.5 + (1.5 if sample<5 else 0.0) + (1.0 if sample<3 else 0.0)
    if international:
        # The cross-competition Elo graph is thin and match venues may be neutral.
        base_unc += 3.0 + float(competition_pool.get("extra_uncertainty",0))
        if not lineup_ok:base_unc += 1.0
    base_unc += float(context_unc)

    # A mutually exclusive 1X2 market must use one shared blend weight.
    h2h_gaps=[]
    for _,q in event_rows[event_rows['market']=='h2h'].iterrows():
        side=_side_for_row(q)
        if side:
            pw,pp,_=price_from_matrix(matrix,'h2h',side,None)
            h2h_gaps.append((pw/max(1e-9,1-pp)-float(q['consensus_prob']))*100)
    h2h_gap=max(h2h_gaps,key=abs) if h2h_gaps else 0
    rows=[]
    for _,r in event_rows.iterrows():
        side=_side_for_row(r)
        if side is None:
            continue

        line=None
        if r["market"] in {"totals","spreads"}:
            if pd.isna(r.get("point")):
                continue
            line=float(r["point"])

        raw_w,raw_p,raw_l=price_from_matrix(matrix,r["market"],side,line)
        market_prob=float(r["consensus_prob"])
        resolved=max(1e-9,1.0-raw_p)
        raw_cond=raw_w/resolved
        raw_gap_pp=(raw_cond-market_prob)*100.0

        final_w,model_weight=_blend_probability(
            raw_w,raw_p,market_prob,sample,h2h_gap if r["market"]=="h2h" else raw_gap_pp
        )
        if international:
            model_weight=min(model_weight,0.35)
            final_w=(model_weight*raw_cond+(1-model_weight)*market_prob)*resolved
        final_l=max(0.0,1.0-final_w-raw_p)
        ev=analyze_bet(float(r["best_odds"]),final_w,raw_p,base_unc)

        final_gap_pp=(final_w/resolved-market_prob)*100.0
        sanity="OK"
        if abs(raw_gap_pp)>25:
            sanity="OUTLIER_SHRUNK"
        elif abs(raw_gap_pp)>15:
            sanity="HIGH_DISAGREEMENT"
        elif abs(raw_gap_pp)>10:
            sanity="CHECK"

        grade=ev.grade
        # Huge disagreements are not allowed into parlays automatically.
        if sanity=="OUTLIER_SHRUNK":
            grade="REVIEW"
        elif sanity=="HIGH_DISAGREEMENT" and grade=="A":
            grade="B"
        if international and grade=="A":
            grade="B"

        display=f'{home}-{away} | {r["selection"]}'
        if line is not None:
            display += f' {line:+g}' if r["market"]=="spreads" else f' {line:g}'

        d=r.to_dict()
        d.update({
            "display_pick":display,
            "raw_independent_prob":raw_w,
            "raw_push_prob":raw_p,
            "market_prob":market_prob,
            "model_win_prob":final_w,
            "push_prob":raw_p,
            "model_lose_prob":final_l,
            "raw_market_gap_pp":raw_gap_pp,
            "final_market_gap_pp":final_gap_pp,
            "model_weight":model_weight,
            "sanity":sanity,
            "review_reason":review_reason(raw_gap_pp,sample,international),
            "home_elo":he,
            "away_elo":ae,
            "home_lambda":hl,
            "away_lambda":al,
            "home_recent_gf":hf["gf"],
            "home_recent_ga":hf["ga"],
            "away_recent_gf":af["gf"],
            "away_recent_ga":af["ga"],
            "home_form_matches":hf["matches"],
            "away_form_matches":af["matches"],
            "uncertainty_pp":base_unc,
            "break_even":ev.break_even,
            "edge_pp":ev.edge_pp,
            "ev_roi":ev.ev_roi,
            "point_ev_roi":ev.ev_roi,
            "stress_ev_roi":ev.conservative_ev_roi,
            "uncertainty_method":"사용자 검증 전 보수 차감 가정; 통계적 신뢰구간 아님",
            "calibration_status":"시장 혼합/EV 미검증" if international else "미검증",
            "observation_only":bool(ev.ev_roi>0 and sanity not in REVIEW_STATES),
            "conservative_ev_roi":ev.conservative_ev_roi,
            "kelly_scaled":ev.kelly_scaled,
            "grade":grade,
            "data_source":competition_pool.get("data_source","A매치 최근 기록") if international else "대회 기록",
            "evidence_note":evidence_note,
            "lineup_confirmed":lineup_ok,
            "probable_lineup":bool(deep_ctx.get("probable_lineup_available")) and not lineup_ok,
            "lineup_source":deep_ctx.get("lineup_source") or ("manual/public" if lineup_ok else deep_ctx.get("probable_lineup_source")),
            "lineup_status":deep_ctx.get("lineup_status") or ("CONFIRMED" if lineup_ok else "NOT_PUBLISHED"),
            "stage":"FINAL" if lineup_ok else ("PROBABLE" if deep_ctx.get("probable_lineup_available") else "PRE-LINEUP"),
            "home_probable_players":deep_ctx.get("home_probable_players"),
            "away_probable_players":deep_ctx.get("away_probable_players"),
            "fixture_id":deep_ctx.get("fixture_id"),
            "parlay_eligible":bool((lineup_ok or not international) and grade in {"A","B","C"} and ev.conservative_ev_roi>0 and sanity not in REVIEW_STATES),
        })
        if international:
            d.update(assess(d,scenarios,side,line))
            d['selection_policy']=POLICY_ID
            d['legacy_grade']=d['grade']
            d['grade']=d['selection_status']
            d['legacy_conservative_ev_roi']=d['conservative_ev_roi']
            d['uncertainty_method']='27개 가정 범위; 통계적 신뢰구간/실측 오차 아님'
            d['kelly_scaled']=0.0
            # Generic optimizer must not silently treat these as calibrated picks.
            d['parlay_eligible']=False

        # v3 counter-case + robustness layer.  Perturb both teams' scoring rates and
        # the model-vs-market calibration weight.  This is a stress test, not a CI.
        scenario_rows=soccer_scenarios(
            hl,al,
            lambda mm: price_from_matrix(mm,r["market"],side,line),
            score_matrix,
        )
        lineup_required=bool(international or deep_ctx.get("deep_context_attempted"))
        robust=scenario_assessment(
            odds=float(r["best_odds"]),market_prob=market_prob,model_weight=model_weight,
            scenarios=scenario_rows,base_ev=ev.ev_roi,sanity=sanity,
            data_ready=sample>=3,lineup_required=lineup_required,lineup_confirmed=lineup_ok,
        )
        _stage="FINAL" if lineup_ok else ("PROBABLE" if deep_ctx.get("probable_lineup_available") else "PRE-LINEUP")
        counter_cases,counter_risk=build_counter_cases(
            sample_matches=sample,lineup_confirmed=lineup_ok if lineup_required else None,
            sanity=sanity,uncertainty_pp=base_unc,
            signal_coverage=signal_ledger.coverage if deep_ctx.get("deep_context_attempted") else None,
            stage=_stage, international=international,
        )
        if international:
            legacy_ok=bool(d.get('scenario_candidate')) and d.get('selection_status') not in {'REVIEW','DATA_HOLD','PASS'}
        else:
            legacy_ok=grade in {'A','B','C'} and ev.conservative_ev_roi>0 and sanity not in REVIEW_STATES
        d.update(decision_fields(ledger=signal_ledger,counter_cases=counter_cases,counter_risk=counter_risk,
                                 robust=robust,legacy_eligible=legacy_ok))
        d['deep_context_attempted']=bool(deep_ctx.get('deep_context_attempted'))
        d['deep_context_reason']=deep_ctx.get('deep_context_reason','')
        if international:
            d['scenario_parlay_eligible']=bool(d.get('scenario_parlay_eligible')) and bool(d['v3_parlay_eligible'])
        else:
            d['parlay_eligible']=bool(d.get('parlay_eligible')) and bool(d['v3_parlay_eligible'])
        rows.append(d)

    _family="soccer_national" if international else "soccer_club"
    _frame=apply_adaptive_layer(pd.DataFrame(rows),_family)
    return _frame,{
        "status":"ok","home":home,"away":away,
        "home_form":hf,"away_form":af,
        "home_elo":he,"away_elo":ae,
        "home_lambda":hl,"away_lambda":al,
    }
