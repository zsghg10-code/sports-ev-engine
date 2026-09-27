
from __future__ import annotations
import requests, pandas as pd

BASE="https://statsapi.mlb.com/api/v1"

def schedule(date: str):
    r=requests.get(f"{BASE}/schedule",params={"sportId":1,"date":date,"hydrate":"probablePitcher,team,linescore"},timeout=20)
    r.raise_for_status()
    rows=[]
    for d in r.json().get("dates",[]):
        for g in d.get("games",[]):
            rows.append({
                "gamePk":g["gamePk"],
                "gameDate":g["gameDate"],
                "home":g["teams"]["home"]["team"]["name"],
                "away":g["teams"]["away"]["team"]["name"],
                "home_probable":g["teams"]["home"].get("probablePitcher",{}).get("fullName"),
                "away_probable":g["teams"]["away"].get("probablePitcher",{}).get("fullName"),
                "status":g["status"]["detailedState"],
            })
    return pd.DataFrame(rows)

def live_feed(game_pk: int):
    r=requests.get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",timeout=20)
    r.raise_for_status()
    return r.json()

def lineups(game_pk: int):
    js=live_feed(game_pk)
    bd=js.get("liveData",{}).get("boxscore",{}).get("teams",{})
    out={}
    for side in ("home","away"):
        team=bd.get(side,{})
        players=team.get("players",{})
        order=team.get("battingOrder",[])
        names=[]
        for pid in order:
            obj=players.get(f"ID{pid}",{})
            names.append(obj.get("person",{}).get("fullName",str(pid)))
        out[side]=names
    return out
