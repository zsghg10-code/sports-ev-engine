
import requests
import pandas as pd

PROVIDER_BUILD = "2.9.4"

BASE = "https://api.the-odds-api.com/v4"

class TheOddsAPI:
    def __init__(self, api_key):
        if not api_key:
            raise RuntimeError("THE_ODDS_API_KEY가 없습니다.")
        self.api_key = api_key

    def sports(self, all_sports=False):
        params={"apiKey":self.api_key}
        if all_sports: params["all"]="true"
        r = requests.get(f"{BASE}/sports", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def odds(self, sport_key, regions="eu", markets="h2h,spreads,totals"):
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
        }
        r = requests.get(f"{BASE}/sports/{sport_key}/odds", params=params, timeout=45)
        r.raise_for_status()
        return r.json(), dict(r.headers)

    @staticmethod
    def flatten(events):
        rows=[]
        for e in events:
            for b in e.get("bookmakers",[]):
                for m in b.get("markets",[]):
                    # reject exchange/lay-style market keys at source
                    if m.get("key") not in {"h2h","spreads","totals"}:
                        continue
                    for o in m.get("outcomes",[]):
                        price = o.get("price")
                        if not isinstance(price,(int,float)) or price <= 1.0 or price > 50:
                            continue
                        rows.append({
                            "event_id":e.get("id"),
                            "commence_time":e.get("commence_time"),
                            "home_team":e.get("home_team"),
                            "away_team":e.get("away_team"),
                            "bookmaker":b.get("title") or b.get("key"),
                            "bookmaker_key":b.get("key"),
                            "market":m.get("key"),
                            "selection":o.get("name"),
                            "odds":float(price),
                            "point":o.get("point"),
                        })
        return pd.DataFrame(rows)
