from __future__ import annotations
import os
import requests
import pandas as pd

BASE = "https://api.the-odds-api.com/v4"


class TheOddsAPI:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("THE_ODDS_API_KEY")
        if not self.api_key:
            raise RuntimeError("THE_ODDS_API_KEY is not set")

    def sports(self):
        r = requests.get(f"{BASE}/sports", params={"apiKey": self.api_key}, timeout=30)
        r.raise_for_status()
        return r.json()

    def odds(self, sport_key: str, regions="eu", markets="h2h,spreads,totals", odds_format="decimal"):
        r = requests.get(
            f"{BASE}/sports/{sport_key}/odds",
            params={
                "apiKey": self.api_key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": odds_format,
            },
            timeout=45,
        )
        r.raise_for_status()
        return r.json(), dict(r.headers)

    @staticmethod
    def flatten(events):
        rows = []
        for e in events:
            for b in e.get("bookmakers", []):
                for m in b.get("markets", []):
                    for o in m.get("outcomes", []):
                        rows.append({
                            "event_id": e.get("id"),
                            "commence_time": e.get("commence_time"),
                            "home_team": e.get("home_team"),
                            "away_team": e.get("away_team"),
                            "bookmaker": b.get("title") or b.get("key"),
                            "bookmaker_key": b.get("key"),
                            "market": m.get("key"),
                            "selection": o.get("name"),
                            "odds": o.get("price"),
                            "point": o.get("point"),
                        })
        return pd.DataFrame(rows)
