
"""
Example automatic runner.

1) Fetch odds from The Odds API
2) De-vig bookmaker markets
3) Join with sport-specific feature tables/models
4) Rank +EV candidates
5) Produce 2-6 leg parlays

The exact feature acquisition layer depends on your licensed data provider.
This script demonstrates the plumbing and refuses to fabricate missing features.
"""
import os, sys, pandas as pd
from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.core.odds import devig_power
from sports_ev_engine.pipeline import rank_bets, build_parlays

SPORT=os.getenv("SPORT_KEY","soccer_uefa_nations_league")
FEATURE_FILE=os.getenv("FEATURE_FILE","data/model_candidates.csv")

def main():
    if not os.path.exists(FEATURE_FILE):
        print(f"Missing {FEATURE_FILE}. Create it from your sport model/provider first.")
        print("Required columns: event_id, selection, odds, model_win_prob; optional push_prob, uncertainty_pp")
        return 2
    df=pd.read_csv(FEATURE_FILE)
    ranked=rank_bets(df)
    os.makedirs("outputs",exist_ok=True)
    ranked.to_csv("outputs/ranked_bets.csv",index=False)
    parlays=build_parlays(ranked,sizes=(2,3,4,5,6),top_n=10)
    rows=[]
    for n,plist in parlays.items():
        for i,p in enumerate(plist,1):
            rows.append({
                "legs":n,"rank":i,
                "selections":" | ".join(x.selection for x in p.legs),
                "odds":p.nominal_odds,
                "approx_hit_prob":p.approx_hit_prob,
                "approx_ev":p.approx_ev,
                "score":p.score
            })
    pd.DataFrame(rows).to_csv("outputs/parlays.csv",index=False)
    print("Wrote outputs/ranked_bets.csv and outputs/parlays.csv")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
