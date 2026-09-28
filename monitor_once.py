
from __future__ import annotations
import os, json

from sports_ev_engine.monitoring import MonitorConfig, MonitorEngine
from sports_ev_engine.telegram_notify import TelegramNotifier

def env(name, default=None):
    return os.getenv(name, default)

odds=env("THE_ODDS_API_KEY")
if not odds:
    raise SystemExit("THE_ODDS_API_KEY is required")

notifier=None
if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
    notifier=TelegramNotifier(env("TELEGRAM_BOT_TOKEN"),env("TELEGRAM_CHAT_ID"))

cfg=MonitorConfig(
    sport_key=env("SPORT_KEY","soccer_uefa_nations_league"),
    competition_name=env("COMPETITION_NAME","UEFA Nations League"),
    region=env("REGION","eu"),
    state_path=env("MONITOR_STATE_PATH","data/monitor_state.json"),
    monthly_budget=int(env("MONTHLY_CREDIT_BUDGET","20000")),
    reserve_credits=int(env("RESERVE_CREDITS","2000")),
)

engine=MonitorEngine(odds,env("API_FOOTBALL_KEY"),cfg,notifier=notifier)
print(json.dumps(engine.tick(),ensure_ascii=False,indent=2,default=str))
