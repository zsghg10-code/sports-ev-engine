
# Sports EV Engine

A modular betting-value research system for **soccer, MLB, and NFL**.

It is designed around the pipeline:

**live odds → no-vig market probability → independent sport model → lineup/injury adjustment → BE/Edge/EV → uncertainty adjustment → correlation-aware parlay search → final ranking**

## What is actually automatic

### Ready now
- Decimal odds / BE / de-vig
- Push-aware Asian totals and handicaps
- EV, uncertainty-adjusted EV, Kelly sizing
- Soccer Poisson score-distribution model
- MLB feature-based run/win model
- NFL EPA/success-rate margin/win model
- Candidate ranking
- 2/3/4/5/6-leg parlay search
- Same-event-leg exclusion by default
- The Odds API connector
- API-Football connector
- MLB StatsAPI connector
- Open-Meteo weather connector

### Requires API key / licensed feed
- Real-time bookmaker odds: `THE_ODDS_API_KEY`
- Soccer fixture / lineup / injury feed: `API_FOOTBALL_KEY`

### Requires a historical/advanced-data source to be genuinely production-grade
The formulas included here are transparent initial models, not a claim of a profitable calibrated model.
For real money use, fit/validate the constants using historical closing lines and outcomes.

Recommended:
- Soccer: event-level xG, shots, big chances, set pieces, PPDA, lineups
- MLB: Statcast/Savant, xFIP/FIP, K-BB%, pitch velocity, bullpen workload, handedness splits
- NFL: nflverse play-by-play, EPA/play, success rate, pressure, OL/QB/injury adjustments

## Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

## Run the batch pipeline

```bash
python run_daily.py
```

Input:
`data/model_candidates.csv`

Output:
- `outputs/ranked_bets.csv`
- `outputs/parlays.csv`

## Candidate CSV schema

Required:
- `event_id`
- `selection`
- `odds`
- `model_win_prob`

Optional:
- `push_prob`
- `uncertainty_pp`
- `group`

Example:
```csv
event_id,selection,odds,model_win_prob,push_prob,uncertainty_pp
oviedo-sporting,Under 2,1.94,0.46,0.27,3
```

For Under 2:
- 0–1 goals = win
- exactly 2 = push
- 3+ = loss

The engine evaluates the true EV rather than using `1/odds` alone.

## Asian handicap example

For Germany -1:
- win by 2+ = win
- win by exactly 1 = push
- draw/loss = loss

The soccer model builds a full score matrix and can price this directly.

## Model philosophy

The market is **not** treated as ground truth.
The market no-vig probability is tracked separately from the independent model probability.

This allows you to see:
- model probability
- market consensus probability
- bookmaker BE probability
- edge vs offered price
- disagreement with market
- uncertainty-adjusted EV

## Important limitation

No program can create a trustworthy "true probability" merely by reading odds.
The quality comes from:
1. the sports data,
2. feature engineering,
3. backtesting,
4. calibration,
5. lineup/injury freshness.

The project intentionally refuses to fabricate unavailable lineup or injury data.
