"""Settle saved v3 prediction snapshots using The Odds API scores endpoint.
Run at least once every 1-3 days on an always-on scheduler for durable automatic settlement.
"""
import os, json
from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.prediction_store import pending_sport_keys, auto_settle, evaluation

key=os.getenv("THE_ODDS_API_KEY")
if not key:raise SystemExit("THE_ODDS_API_KEY is required")
api=TheOddsAPI(key)
result={}
for sport_key in pending_sport_keys():
    result[sport_key]=auto_settle(api,sport_key)
result["evaluation"]=evaluation()
print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
