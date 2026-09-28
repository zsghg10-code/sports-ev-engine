"""Continuous v3.2 worker.

Recommended on Railway/Render/VPS. It records line movement for CLV, re-analyses
on price/lineup/starter changes, settles finished games and refreshes MLB review.
"""
import json, os, time, traceback
from sports_ev_engine.smart_refresh import run_smart_cycle
from sports_ev_engine.prediction_store import configure_persistence

odds=os.getenv("THE_ODDS_API_KEY")
if not odds:raise SystemExit("THE_ODDS_API_KEY is required")
configure_persistence(os.getenv("SUPABASE_URL"),os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"))
football=os.getenv("API_FOOTBALL_KEY")
region=os.getenv("AUTO_REFRESH_REGION","eu")
interval=max(300,int(os.getenv("SMART_WORKER_INTERVAL_SECONDS","300")))
print(f"Sports EV smart worker started; interval={interval}s")
while True:
    try:
        r=run_smart_cycle(odds,football,region=region,reserve_credits=int(os.getenv("RESERVE_CREDITS","2000")))
        print(json.dumps(r,ensure_ascii=False,default=str))
    except KeyboardInterrupt:break
    except Exception:traceback.print_exc()
    time.sleep(interval)
