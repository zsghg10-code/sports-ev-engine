
"""
KBO + NPB background monitor.

Environment:
  THE_ODDS_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
Optional:
  BASEBALL_MONITOR_LEAGUES=KBO,NPB
  REGION=eu
  RESERVE_CREDITS=2000

Run:
  python monitor_baseball.py
"""
from __future__ import annotations
import os, time, traceback

from sports_ev_engine.baseball_monitoring import BaseballMonitorConfig, BaseballMonitorEngine
from sports_ev_engine.monitoring import JSONStateStore
from sports_ev_engine.telegram_notify import TelegramNotifier

def main():
    odds=os.getenv("THE_ODDS_API_KEY")
    if not odds:
        raise SystemExit("THE_ODDS_API_KEY is required")
    notifier=None
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        notifier=TelegramNotifier(os.getenv("TELEGRAM_BOT_TOKEN"),os.getenv("TELEGRAM_CHAT_ID"))

    wanted=[x.strip().upper() for x in os.getenv("BASEBALL_MONITOR_LEAGUES","KBO,NPB").split(",") if x.strip()]
    mapping={"KBO":"baseball_kbo","NPB":"baseball_npb"}
    region=os.getenv("REGION","eu")
    reserve=int(os.getenv("RESERVE_CREDITS","2000"))

    engines=[]
    for lg in wanted:
        if lg not in mapping: continue
        cfg=BaseballMonitorConfig(
            league=lg,
            sport_key=mapping[lg],
            region=region,
            state_path=f"data/{lg.lower()}_monitor_state.json",
            reserve_credits=reserve,
        )
        engines.append(BaseballMonitorEngine(odds,None,cfg,notifier))

    print("KBO/NPB monitor started:",wanted)
    while True:
        try:
            sleep_min=60
            for e in engines:
                r=e.tick()
                print(r)
                # interval is refined by each event in state; safe default 15 min when active
                st=e.store.load()
                active=False
                from sports_ev_engine.monitoring import minutes_until, scheduled_interval_minutes
                from datetime import datetime, timezone
                for es in st.get("events",{}).values():
                    if es.get("commence_time"):
                        m=minutes_until(es["commence_time"],datetime.now(timezone.utc))
                        if m>0:
                            active=True
                            sleep_min=min(sleep_min,scheduled_interval_minutes(m))
                if not active:
                    sleep_min=min(sleep_min,60)
            time.sleep(max(300,sleep_min*60))
        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc()
            time.sleep(900)

if __name__=="__main__":
    main()
