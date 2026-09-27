
from __future__ import annotations
import os, requests, pandas as pd

BASE="https://v3.football.api-sports.io"

class APIFootball:
    def __init__(self, api_key: str|None=None):
        self.key=api_key or os.getenv("API_FOOTBALL_KEY")
        if not self.key:
            raise RuntimeError("API_FOOTBALL_KEY is not set")
        self.headers={"x-apisports-key":self.key}

    def fixtures(self, date: str):
        r=requests.get(f"{BASE}/fixtures",headers=self.headers,params={"date":date},timeout=30)
        r.raise_for_status()
        return r.json()["response"]

    def injuries(self, fixture_id: int):
        r=requests.get(f"{BASE}/injuries",headers=self.headers,params={"fixture":fixture_id},timeout=30)
        r.raise_for_status()
        return r.json()["response"]

    def lineups(self, fixture_id: int):
        r=requests.get(f"{BASE}/fixtures/lineups",headers=self.headers,params={"fixture":fixture_id},timeout=30)
        r.raise_for_status()
        return r.json()["response"]

    def team_statistics(self, league: int, season: int, team: int):
        r=requests.get(f"{BASE}/teams/statistics",headers=self.headers,
                       params={"league":league,"season":season,"team":team},timeout=30)
        r.raise_for_status()
        return r.json()["response"]
