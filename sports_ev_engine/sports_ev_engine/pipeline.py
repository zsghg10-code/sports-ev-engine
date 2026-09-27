
from __future__ import annotations
import pandas as pd
from sports_ev_engine.core.odds import devig_power
from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.core.parlay import Leg, optimize_parlays

def consensus_no_vig(odds_df: pd.DataFrame) -> pd.DataFrame:
    """
    Input columns: event_id, market_id, bookmaker, selection, odds
    Assumes all outcomes for a bookmaker+market are present.
    """
    out=[]
    group_cols=["event_id","market_id","bookmaker"]
    for keys,g in odds_df.groupby(group_cols):
        if len(g)<2: 
            continue
        probs=devig_power(g["odds"].tolist())
        gg=g.copy()
        gg["fair_prob"]=probs
        out.append(gg)
    if not out:
        return pd.DataFrame()
    x=pd.concat(out,ignore_index=True)
    return (x.groupby(["event_id","market_id","selection"],as_index=False)
              .agg(consensus_prob=("fair_prob","median"),
                   best_odds=("odds","max"),
                   books=("bookmaker","nunique")))

def rank_bets(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Required columns:
    event_id, selection, odds, model_win_prob
    Optional: push_prob, uncertainty_pp
    """
    rows=[]
    for _,r in candidates.iterrows():
        push=float(r.get("push_prob",0.0) or 0.0)
        unc=float(r.get("uncertainty_pp",0.0) or 0.0)
        ev=analyze_bet(float(r.odds),float(r.model_win_prob),push,unc)
        row=r.to_dict()
        row.update({
            "break_even":ev.break_even_win_prob,
            "edge_pp":ev.edge_pp,
            "ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,
            "kelly_scaled":ev.kelly_scaled,
            "grade":ev.grade,
        })
        rows.append(row)
    out=pd.DataFrame(rows)
    if out.empty: return out
    return out.sort_values(["grade","conservative_ev_roi","edge_pp"],ascending=[True,False,False])

def build_parlays(ranked: pd.DataFrame, sizes=(2,3,4,5,6), top_n=10):
    legs=[]
    for _,r in ranked.iterrows():
        if r.get("grade")=="PASS": 
            continue
        legs.append(Leg(
            event_id=str(r["event_id"]),
            selection=str(r["selection"]),
            odds=float(r["odds"]),
            win_prob=float(r["model_win_prob"]),
            push_prob=float(r.get("push_prob",0.0) or 0.0),
            conservative_ev=float(r["conservative_ev_roi"]),
            uncertainty_pp=float(r.get("uncertainty_pp",0.0) or 0.0),
            group=str(r.get("group","") or ""),
        ))
    results={}
    for n in sizes:
        results[n]=optimize_parlays(legs,n_legs=n,top_n=top_n,min_leg_ev=0.0)
    return results
