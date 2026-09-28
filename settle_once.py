"""Settle saved prediction snapshots and run MLB post-game review once.

Use this script on a persistent host/scheduler if you want unattended settlement.
The Streamlit app can do the same work from the Model Validation tab.
"""
import os, json
from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.prediction_store import pending_sport_keys, auto_settle, evaluation
from sports_ev_engine.providers.mlb_postgame import analyze_settled_mlb

key=os.getenv("THE_ODDS_API_KEY")
if not key:raise SystemExit("THE_ODDS_API_KEY is required")
api=TheOddsAPI(key)
result={}
for sport_key in pending_sport_keys():
    result[sport_key]=auto_settle(api,sport_key)
result["mlb_postgame_review"]=analyze_settled_mlb()
result["evaluation"]=evaluation()
print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
