"""Run one v3.2 smart refresh cycle (market/lineup reanalysis + settlement)."""
import json, os
from sports_ev_engine.smart_refresh import run_smart_cycle
from sports_ev_engine.prediction_store import configure_persistence

odds=os.getenv("THE_ODDS_API_KEY")
if not odds:raise SystemExit("THE_ODDS_API_KEY is required")
configure_persistence(os.getenv("SUPABASE_URL"),os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"))
result=run_smart_cycle(odds,os.getenv("API_FOOTBALL_KEY"),region=os.getenv("AUTO_REFRESH_REGION","eu"),reserve_credits=int(os.getenv("RESERVE_CREDITS","2000")))
print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
