# Sports EV Engine v2.4.2 — Soccer + KBO/NPB + 20K Monitor

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


## v2.4.2 NPB/KBO source fix

v2.4 could fail with `SofaScore recent league games unavailable` when the daily event
match was not enough to discover the current tournament/season IDs.

v2.4.2 uses a three-stage history strategy:

1. Discover NPB/KBO from `/sport/baseball/unique-tournaments` and select the current season.
2. Fetch paginated `/unique-tournament/{id}/season/{id}/events/last/{page}`.
3. If still incomplete, fall back to daily schedules and then team-specific `/team/{id}/events/last/{page}`.

Two SofaScore hosts are tried automatically. Provider errors are surfaced in the UI instead
of being hidden behind a generic "unavailable" message.


## v2.4.2 — KBO/NPB provider switch

The prior SofaScore dependency returned HTTP 403 from Streamlit Cloud.
v2.4.2 removes SofaScore from the core KBO/NPB model and uses:

- The Odds API: current KBO/NPB prices
- API-Sports Baseball (`https://v1.baseball.api-sports.io`): league discovery + season games
- `API_BASEBALL_KEY` if provided
- otherwise the existing `API_FOOTBALL_KEY` is reused

API-Sports documents KBO and NPB schedule/historical-data coverage. The app uses only
real returned game data; if the provider does not return a season, it fails visibly.

### Secrets

```toml
THE_ODDS_API_KEY = "..."
API_FOOTBALL_KEY = "..."

# Optional. If omitted, API_FOOTBALL_KEY is used for Baseball too.
API_BASEBALL_KEY = "..."
```

### Lineups / starting pitchers

v2.4.2 deliberately does NOT pretend to have KBO/NPB starting lineups.
The core API-Sports Baseball integration is used for stable schedule/history data.
Until a stable lineup source is connected, those games are usually MEDIUM data quality
when recent history is sufficient, and the uncertainty penalty remains active.


## v2.4.3 — official league data only

KBO/NPB modeling no longer depends on SofaScore or API-Sports Baseball.

NPB:
- NPB.jp team batting stats
- NPB.jp team pitching stats
- current-season runs scored / allowed, win percentage

KBO:
- KBO official team batting stats
- KBO official team pitching stats
- KBO official team standings / recent 10-game record

The Odds API remains the only paid/quota-based source used by the baseball tab.

Because starting pitchers and confirmed lineups are not yet on a stable provider,
baseball rows are MEDIUM quality and the model caps aggressive grades.

## v2.5 — KBO/NPB official starter + lineup FINAL model

### KBO
- Official GameCenter `GetKboGameList`: game ID, announced starters, starter/lineup flags.
- Official `GetLineUpAnalysis`: batting order 1–9, positions, lineup confirmation flag and lineup WAR groups.
- Official pitcher table is matched for ERA / WHIP / K-BB when the starter appears in the current table.

### NPB
- NPB.jp `予告先発投手`: announced starters.
- NPB official score page: batting orders when the official order is published before the game.
- NPB individual pitcher pages: ERA, calculated WHIP and K-BB.
- NPB individual batter pages: OPS; a confirmed lineup is compared with the team-season baseline.

### Analysis stages
- `PRE-LINEUP`
- `STARTER CONFIRMED`
- `LINEUP CONFIRMED`
- `FINAL`

Only `FINAL` can receive an A grade. The KBO/NPB parlay builder defaults to FINAL-only.
No new paid API key is required for starter/lineup collection.


## v2.5.1 parser fix

The `official stats table not found` error came from assuming that pandas.read_html()
would always promote the NPB/KBO official table header into DataFrame column names.

v2.5.1 no longer depends on that behavior.

- NPB team batting/pitching: parse HTML `tr/th/td` rows directly.
- NPB fallback uses the official fixed column order if a header row is unusual.
- KBO team batting/pitching/standings: parse HTML rows directly.
- Japanese NPB pages are forced to UTF-8.
- Errors identify the exact league/table/source URL instead of only saying
  `official stats table not found`.
- Old baseball session results are cleared on this build.


## v2.6 Advanced KBO/NPB model

The baseball final model now has independent signal slots for:

1. starting pitcher recent 3–5 game trend (BB%, K%, K-BB%, ERA)
2. recent batting form / last-N form
3. recent bullpen workload
4. lineup platoon split vs today's starter
5. confirmed lineup strength
6. stadium + weather
7. recent velocity trend, when a stable official/public source exposes it

Important no-fake rule:
- A missing signal is **not** replaced with a made-up league average.
- Missing signals increase `uncertainty_pp`.
- `advanced_completeness` shows how many advanced slots were actually used.
- Recent pitch velocity is often not exposed in stable pregame public tables for KBO/NPB.
  In that case `velocity_used=False`; the model does not invent a velocity change.

KBO:
- official GameCenter starters / lineup
- pitcher recent-game table for BB/K-BB
- current lineup hitter recent-10 and exact L/R situation tables when player IDs are exposed
- bullpen scheduling load proxy if exact relief innings are unavailable

NPB:
- NPB.jp announced starters and official lineup
- official recent boxscores for starter recent form and relief innings
- recent team on-base/run-form proxy from official boxscores
- exact public player L/R OPS is not assumed when unavailable

Weather:
- Open-Meteo hourly temperature/humidity/precipitation/wind
- indoor domes are neutral
- high wind/rain increases uncertainty instead of forcing a directional run adjustment
