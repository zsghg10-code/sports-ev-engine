
import pandas as pd
from sports_ev_engine.core.odds import devig_power

ALLOWED_MARKETS={"h2h","spreads","totals"}

def clean_odds(df):
    if df.empty:
        return df
    x=df.copy()
    x=x[x["market"].isin(ALLOWED_MARKETS)]
    x=x[(x["odds"]>1.0)&(x["odds"]<=50)]
    # remove exchange-like bookmakers where possible
    bad = x["bookmaker_key"].astype(str).str.contains("exchange|betfair_ex",case=False,regex=True)
    x=x[~bad]
    return x

def market_id_row(r):
    if r["market"]=="h2h":
        return "h2h"
    if pd.isna(r.get("point")):
        point=""
    else:
        value=float(r.get("point"))
        # The Odds API encodes a spread as opposite signed points on the two
        # outcomes (home -1.5 / away +1.5).  They are one two-sided market and
        # must be de-vigged together.  Using the signed point as market_id split
        # the pair into two one-row groups, silently deleting every spread.
        if r["market"]=="spreads":
            value=abs(value)
        point=f"{value:g}"
    return f'{r["market"]}|{point}'

def consensus(df, min_books=3):
    x=clean_odds(df)
    if x.empty:
        return pd.DataFrame()
    x["market_id"]=x.apply(market_id_row,axis=1)
    fair_parts=[]
    for (_,mid,bm),g in x.groupby(["event_id","market_id","bookmaker"]):
        # h2h needs 3-way soccer, spreads/totals usually 2-way
        if len(g)<2:
            continue
        probs=devig_power(g["odds"].tolist())
        gg=g.copy()
        gg["fair_prob"]=probs
        fair_parts.append(gg)
    if not fair_parts:
        return pd.DataFrame()
    y=pd.concat(fair_parts,ignore_index=True)
    c=(y.groupby(["event_id","home_team","away_team","commence_time","market","market_id","selection","point"],dropna=False,as_index=False)
         .agg(consensus_prob=("fair_prob","median"),
              median_odds=("odds","median"),
              books=("bookmaker","nunique")))
    # get best *plausible* odds: max within 25% of median
    merged=[]
    keys=["event_id","market_id","selection"]
    for _,r in c.iterrows():
        g=x[(x["event_id"]==r["event_id"])&(x.apply(market_id_row,axis=1)==r["market_id"])&(x["selection"]==r["selection"])].copy()
        cap=float(r["median_odds"])*1.25
        g=g[g["odds"]<=cap]
        if g.empty: continue
        best=g.sort_values("odds",ascending=False).iloc[0]
        d=r.to_dict()
        d["best_odds"]=float(best["odds"])
        d["best_book"]=best["bookmaker"]
        merged.append(d)
    out=pd.DataFrame(merged)
    if out.empty:return out
    out=out[out["books"]>=min_books].copy()
    out["best_be"]=1/out["best_odds"]
    out["market_edge_pp"]=(out["consensus_prob"]-out["best_be"])*100
    out["market_ev"]=out["consensus_prob"]*out["best_odds"]-1
    return out.sort_values("market_ev",ascending=False)
