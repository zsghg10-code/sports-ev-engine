from __future__ import annotations
import pandas as pd
from sports_ev_engine.core.odds import devig_power


def build_market_consensus(df: pd.DataFrame) -> pd.DataFrame:
    """Build no-vig consensus from multiple bookmakers.

    Required columns: event_id, home_team, away_team, commence_time,
    bookmaker, market, selection, odds, point.
    """
    if df.empty:
        return pd.DataFrame()

    x = df.copy()
    x["point_key"] = x["point"].apply(lambda v: "" if pd.isna(v) else str(v))
    x["market_id"] = x["market"].astype(str) + "|" + x["point_key"]

    parts = []
    for (_, _, bm), g in x.groupby(["event_id", "market_id", "bookmaker"]):
        if len(g) < 2:
            continue
        odds = g["odds"].astype(float).tolist()
        probs = devig_power(odds)
        gg = g.copy()
        gg["fair_prob"] = probs
        parts.append(gg)

    if not parts:
        return pd.DataFrame()

    y = pd.concat(parts, ignore_index=True)
    out = (
        y.groupby(
            ["event_id", "home_team", "away_team", "commence_time", "market_id", "selection"],
            as_index=False,
        )
        .agg(
            consensus_prob=("fair_prob", "median"),
            best_odds=("odds", "max"),
            median_odds=("odds", "median"),
            books=("bookmaker", "nunique"),
        )
    )
    out["best_be"] = 1.0 / out["best_odds"]
    out["market_edge_pp"] = (out["consensus_prob"] - out["best_be"]) * 100.0
    out["market_ev"] = out["consensus_prob"] * out["best_odds"] - 1.0
    return out.sort_values(["market_ev", "market_edge_pp"], ascending=False)
