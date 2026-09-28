# v2.9.1 — REVIEW 표시 및 국가대표 경로 수정

최신 설치·변경 내용은 REVIEW_FIX_KO.md를 읽으세요. 2.9.0 기능을 포함한 전체 패키지입니다.

아래는 이전 버전 안내입니다.

# Sports EV Engine v2.9.0 — 무료 자동 A매치

현재 설치·사용법: **AUTO_AMATCH_KO.md**.
CSV 없이 공개 기록을 자동 조회합니다. 불충분한 경기에는 분석 보류 사유를 표시합니다.
기존 Odds API 키만 필요합니다. xG/결장은 자동 수집하지 않습니다.
ZIP 전체 반영 후 Reboot하고 v2.9.0을 확인하세요.

```bash
pip install -r requirements.txt
streamlit run app.py
python -m unittest discover -s tests -v
```

아래는 이전 버전 안내입니다. v2.8.0의 CSV 화면 안내는 현재 기본 화면에 적용되지 않습니다.

# Sports EV Engine v2.8.0 — 무료 A매치 기록 모드

설치·입력 안내는 FREE_AMATCH_KO.md와 PATCH_NOTES_KO.md를 먼저 읽으세요.

- 기본 A매치 모드는 API-Football 키 없이 공개 친선전 기록과 선택적 CSV를 사용합니다.
- 자동 배당 조회에는 기존 THE_ODDS_API_KEY가 필요하며 해당 서비스 한도는 그대로 적용됩니다.
- 90분 결과 CSV로 예선·네이션스리그 등 최근 기록을 추가할 수 있습니다.
- xG, 라인업, 결장은 자동 수집하지 않습니다. 출처가 있는 사용자 CSV만 사용합니다.
- API-Football은 선택 모드로 유지합니다. 유료 시즌 접근 제한을 우회하지 않습니다.
- A매치 무료 모드는 대화형 탭에 적용됩니다. 기존 클럽 축구와 모니터링 워커는 API-Football 경로를 유지합니다.
- 검증: 6개 테스트 통과(무료 모드 Streamlit UI 포함, 배당 입력은 모의 데이터).
- 공개 원본 다운로드/파싱은 실제로 확인했습니다. 사용자 배포 서버·실제 API 키로 실행한 결과는 확인하지 않았습니다.

```bash
pip install -r requirements.txt
streamlit run app.py
python -m unittest discover -s tests -v
```

## 이전 기능 및 버전 설명 (현재 무료 모드는 위 안내 우선)

# Sports EV Engine v2.7.3 — Soccer A-matches + KBO/NPB + 20K Monitor

v2.7.3 adds API-Football request pacing (6.2 seconds between uncached requests
per key in this process), successful-response caching, and a batch stop on plan
or rate-limit failures. A denied `last` query switches to permitted team/season
queries. If the current season is denied too, the app reports that limitation;
this patch does not unlock paid seasons. It also normalizes Turkey/Türkiye
and excludes club Champions League qualifying from national-team discovery.
The Odds API quota and API-Football quota are independent.

The Streamlit entry point is `app.py` at the repository root. Upload the
contents of this ZIP to the repository root, preserving the
`sports_ev_engine/` subdirectory. The heading and BUILD caption in the app
must show v2.7.3 after deployment.

v2.7.3 corrects the post-start lineup label: a valid latest order is shown as
confirmed current order, not as an unconfirmed pregame lineup. Once the scheduled
start time passes, the game is excluded from this pregame EV/parlay model;
current scores and live prices need a separate live model.

Upload ALL files, including the sports_ev_engine folder, then Reboot the Streamlit
app. The UI checks the build of all changed provider modules and refuses to
run when old modules remain. The app must show "수집 모듈 v2.7.3 확인 완료".

A-match discovery requests the full provider catalog, displays inactive events
as unavailable, recognizes Euro qualifiers and CONCACAF Nations League, and
supports scanning all active senior international competitions. Unlisted
friendlies cannot be fetched from this odds provider; no unsupported sport keys
or odds are invented.

NPB v2.7.3 reconciles Japanese announced starters with the official English
player profile, corrects visitor/home order in recent boxscores, and attempts
team OPS from official Japanese game batting outcomes. It only reports a
recent OPS when at least five complete game boxscores can be resolved.
The starter announcement must match the game's date and link to a player
profile. When that page advances to tomorrow, the date-specific official
schedule is used and each abbreviated pitcher name is resolved uniquely
against that team's official player records. Navigation text such as `>>`
cannot count as a confirmed starter.
Missing starter K-BB% or team OPS holds the stage at DATA PARTIAL and excludes
that game from automatic parlays. The screen shows the per-game missing-data
reason; it does not substitute an OBP proxy for OPS.

The new `🌍 축구 A매치` tab lists currently active senior national-team
competitions from The Odds API. It fetches each country's recent fixtures
across competitions with API-Football, then runs the soccer probability/EV
model. At least three completed matches per country in the last three years
are required. Name mismatches and missing data are shown as excluded games.
International estimates use a smaller home adjustment and more uncertainty;
they cannot receive an A grade and are kept out of the existing club-soccer
automatic parlay. Availability depends on both API keys, bookmaker coverage,
and the provider's senior national-team records.

The baseball tab now calls `AdvancedBaseballSignals.collect()` for each game,
passes its output through `analyze_official_event()`, and displays signal
availability and the resulting uncertainty. Automatic parlays require FINAL
status and HIGH data quality by default.

Live KBO/NPB/Open-Meteo retrieval requires network access from the deployed
environment; this package was verified offline with synthetic inputs. The
UI lists available signals per event. Do not interpret a missing signal as a
measured league-average result. KBO bullpen load is schedule based and does
not report actual reliever pitches; NPB player handedness splits and recent
pitch velocity remain unavailable from the wired public sources.

The version history below documents earlier builds; older source descriptions
are historical and do not describe the active v2.7.3 provider.

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
