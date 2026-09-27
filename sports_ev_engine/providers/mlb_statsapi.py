
import requests, pandas as pd
BASE="https://statsapi.mlb.com/api/v1"
def schedule(date_str):
    r=requests.get(f"{BASE}/schedule",params={"sportId":1,"date":date_str,"hydrate":"probablePitcher,team"},timeout=30)
    r.raise_for_status()
    rows=[]
    for d in r.json().get("dates",[]):
        for g in d.get("games",[]):
            rows.append({
                "gamePk":g["gamePk"],"gameDate":g["gameDate"],
                "away":g["teams"]["away"]["team"]["name"],
                "home":g["teams"]["home"]["team"]["name"],
                "away_probable":g["teams"]["away"].get("probablePitcher",{}).get("fullName"),
                "home_probable":g["teams"]["home"].get("probablePitcher",{}).get("fullName"),
                "status":g["status"]["detailedState"],
            })
    return pd.DataFrame(rows)
