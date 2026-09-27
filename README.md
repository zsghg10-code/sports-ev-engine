
# Sports EV Engine v2.1

Goal: remove manual Elo/xG entry for soccer.

Pipeline:
1. The Odds API live odds
2. remove lay/exchange + malformed outliers
3. no-vig consensus (min bookmaker filter)
4. API-Football team mapping
5. automatic recent-form data
6. independent Poisson score model
7. 1X2 / Asian spread / totals probabilities
8. BE / Edge / EV / uncertainty-adjusted EV
9. A/B/C/PASS
10. 2-6 leg parlay generation

## Required Streamlit Secrets
```toml
THE_ODDS_API_KEY = "..."
API_FOOTBALL_KEY = "..."
```

## Notes
- The football model is independent from the betting market; it uses recent goals/results.
- Market consensus is shown separately.
- v2.1 does not yet automatically quantify individual-player lineup value.
