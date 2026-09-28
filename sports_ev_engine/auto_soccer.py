
from __future__ import annotations
import math
from datetime import datetime, timezone
import pandas as pd

from sports_ev_engine.models.soccer_auto import score_matrix, price_from_matrix, norm_name
from sports_ev_engine.models.elo import build_elo, opponent_adjusted_form
from sports_ev_engine.core.ev import analyze_bet

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

def _build_lambdas(home_form, away_form, home_elo, away_elo, goal_mean=2.55):
    # opponent-adjusted recent form
    h_raw=max(0.18,(home_form["gf"]+away_form["ga"])/2 + 0.10)
    a_raw=max(0.18,(away_form["gf"]+home_form["ga"])/2)

    elo_diff=(home_elo+55.0)-away_elo
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
    cutoff_ts=_cutoff_ts(first.get("commence_time"))
    fixtures=competition_pool.get("fixtures",[])

    ratings=competition_pool.get("elo")
    if ratings is None:
        ratings=build_elo(fixtures)
        competition_pool["elo"]=ratings

    hf=opponent_adjusted_form(fixtures,home,ratings,cutoff_ts,recent_n=recent_n)
    af=opponent_adjusted_form(fixtures,away,ratings,cutoff_ts,recent_n=recent_n)

    if not hf or not af:
        missing=[]
        if not hf:missing.append(home)
        if not af:missing.append(away)
        return pd.DataFrame(),{
            "status":"data_failed","home":home,"away":away,
            "reason":"same-competition completed fixtures unavailable: "+", ".join(missing)
        }

    he=ratings.get(norm_name(home),1500.0)
    ae=ratings.get(norm_name(away),1500.0)
    hl,al=_build_lambdas(hf,af,he,ae)
    matrix=score_matrix(hl,al)

    sample=min(hf["matches"],af["matches"])
    base_unc=3.5 + (1.5 if sample<5 else 0.0) + (1.0 if sample<3 else 0.0)

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
            raw_w,raw_p,market_prob,sample,raw_gap_pp
        )
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
            "conservative_ev_roi":ev.conservative_ev_roi,
            "kelly_scaled":ev.kelly_scaled,
            "grade":grade,
        })
        rows.append(d)

    return pd.DataFrame(rows),{
        "status":"ok","home":home,"away":away,
        "home_form":hf,"away_form":af,
        "home_elo":he,"away_elo":ae,
        "home_lambda":hl,"away_lambda":al,
    }
