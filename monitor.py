
"""
Sports EV Engine v2.3 background monitor.

Run this on an always-on worker (Railway/Render/VPS etc.):

    python monitor.py

Required environment variables:
    THE_ODDS_API_KEY
Optional:
    API_FOOTBALL_KEY
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    SPORT_KEY
    COMPETITION_NAME
    REGION
    MONITOR_STATE_PATH

Streamlit Community Cloud is still used for the dashboard; this worker is
what keeps monitoring when your phone/browser is closed.
"""
from __future__ import annotations
import os, time, traceback

from sports_ev_engine.monitoring import MonitorConfig, MonitorEngine, JSONStateStore, recommended_sleep_seconds
from sports_ev_engine.telegram_notify import TelegramNotifier


def env(name, default=None):
    return os.getenv(name, default)


def main():
    odds_key=env("THE_ODDS_API_KEY")
    if not odds_key:
        raise SystemExit("THE_ODDS_API_KEY is required")

    football_key=env("API_FOOTBALL_KEY")
    notifier=None
    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        notifier=TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))

    cfg=MonitorConfig(
        sport_key=env("SPORT_KEY","soccer_uefa_nations_league"),
        competition_name=env("COMPETITION_NAME","UEFA Nations League"),
        region=env("REGION","eu"),
        state_path=env("MONITOR_STATE_PATH","data/monitor_state.json"),
        monthly_budget=int(env("MONTHLY_CREDIT_BUDGET","20000")),
        reserve_credits=int(env("RESERVE_CREDITS","2000")),
    )
    engine=MonitorEngine(odds_key,football_key,cfg,notifier=notifier)
    store=JSONStateStore(cfg.state_path)

    print("Sports EV Engine monitor started")
    while True:
        try:
            result=engine.tick()
            print(result)
        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc()

        state=store.load()
        sleep_sec=recommended_sleep_seconds(state)
        print(f"sleep {sleep_sec}s")
        time.sleep(sleep_sec)


if __name__=="__main__":
    main()
