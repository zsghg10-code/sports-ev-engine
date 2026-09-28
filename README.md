# Sports EV Engine v2.4.1 — Soccer + KBO/NPB + 20K Monitor

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


## v2.4 KBO / NPB

The Odds API keys:
- `baseball_kbo`
- `baseball_npb`

Automatic baseball data source:
- SofaScore public baseball endpoints (no extra API key)
- recent league results
- event matching
- starting lineups when available
- starting pitcher identification when available
- season pitcher statistics when the provider exposes them

Model:
- recent runs scored / allowed
- opponent-strength adjustment
- home-field adjustment
- starting-pitcher ERA / WHIP / K-BB adjustment when available
- Negative Binomial run distribution
- market-prior calibration + outlier shrinkage
- data quality HIGH / MEDIUM / LOW
- LOW-quality events are excluded from automatic parlays

Run KBO+NPB monitor:
```bash
python monitor_baseball.py
```

Important:
SofaScore is an unofficial public-data dependency. Its response shape can change.
The app fails soft: missing lineup/starter data increases uncertainty and caps grades
instead of pretending that the data exists.


## v2.4.1 NPB/KBO source fix

v2.4 could fail with `SofaScore recent league games unavailable` when the daily event
match was not enough to discover the current tournament/season IDs.

v2.4.1 uses a three-stage history strategy:

1. Discover NPB/KBO from `/sport/baseball/unique-tournaments` and select the current season.
2. Fetch paginated `/unique-tournament/{id}/season/{id}/events/last/{page}`.
3. If still incomplete, fall back to daily schedules and then team-specific `/team/{id}/events/last/{page}`.

Two SofaScore hosts are tried automatically. Provider errors are surfaced in the UI instead
of being hidden behind a generic "unavailable" message.
