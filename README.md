# Sports EV Engine v2.3 — 20K Monitor

v2.3 adds background odds/lineup monitoring to the v2.2 model.

## Monitoring schedule

- 24–6h: h2h every 60 min
- 6–2h: h2h every 30 min
- 2h–30m: h2h every 15 min
- final 30m: h2h every 5 min
- full `h2h+spreads+totals` snapshots at:
  - 6h / 2h / 60m / 30m / 15m / 5m

## Reanalysis triggers

- no-vig probability move:
  - >2h: 2.0 percentage points
  - 30m–2h: 1.5pp
  - <30m: 1.0pp
- 4.0pp move: immediate strong trigger
- Asian handicap / total line move >= 0.25: immediate
- ordinary price-only trigger requires two consecutive observations in the same direction
- lineup change: immediate

## Lineups

API-Football is checked every 15 minutes from 90 minutes before kickoff.
The Odds API credits are not used for lineup checks.

## Telegram

Add these Streamlit/worker secrets:

```toml
THE_ODDS_API_KEY = "..."
API_FOOTBALL_KEY = "..."
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
```

Telegram notifications do not consume The Odds API credits.

## Background mode

Streamlit Community Cloud is the dashboard. For actual monitoring with the phone closed,
run the included worker on an always-on host:

```bash
python monitor.py
```

Or for a cron/scheduler:

```bash
python monitor_once.py
```

The worker persists previous observations in `data/monitor_state.json`.

## Credit budget

Default:
- monthly budget: 20,000
- emergency reserve: 2,000
- auto monitor stops before eating into the reserve

The actual provider response header is recorded as `credits_remaining`.
