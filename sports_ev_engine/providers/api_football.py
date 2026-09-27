
import requests
from datetime import date

BASE="https://v3.football.api-sports.io"

class APIFootball:
    def __init__(self, api_key):
        if not api_key:
            raise RuntimeError("API_FOOTBALL_KEY가 없습니다.")
        self.headers={"x-apisports-key":api_key}

    def _get(self,path,params):
        r=requests.get(f"{BASE}/{path}",headers=self.headers,params=params,timeout=45)
        r.raise_for_status()
        body=r.json()
        errors=body.get("errors")
        if errors:
            raise RuntimeError(str(errors))
        return body.get("response",[])

    def search_team(self,name):
        return self._get("teams",{"search":name})

    def recent_fixtures(self,team_id,last=6):
        """
        Free-plan compatible.
        API-Football requires `season` for team fixture queries on some plans/endpoints.
        We query current season first, then prior season if needed, and select the
        latest completed matches locally. No `last` parameter is used.
        """
        today=date.today()
        wanted=int(last)
        collected=[]
        seen=set()

        # Current and previous season are enough for recent national-team form in normal use.
        # Include next year defensively for competitions whose season label is the ending year.
        seasons=[today.year, today.year-1, today.year+1]

        for season in seasons:
            try:
                rows=self._get("fixtures",{
                    "team":int(team_id),
                    "season":int(season),
                })
            except RuntimeError as e:
                # Free data may not include a season; skip unavailable season instead of killing batch.
                msg=str(e).lower()
                if "season" in msg or "plan" in msg or "access" in msg or "coverage" in msg:
                    continue
                raise

            for fx in rows:
                fid=fx.get("fixture",{}).get("id")
                status=fx.get("fixture",{}).get("status",{}).get("short")
                goals=fx.get("goals",{})
                ts=fx.get("fixture",{}).get("timestamp",0)
                if fid in seen:
                    continue
                if status not in {"FT","AET","PEN"}:
                    continue
                if goals.get("home") is None or goals.get("away") is None:
                    continue
                seen.add(fid)
                collected.append(fx)

            if len(collected)>=wanted:
                break

        collected.sort(key=lambda fx:fx.get("fixture",{}).get("timestamp",0),reverse=True)
        return collected[:wanted]

    def fixtures_by_date(self,date_str):
        return self._get("fixtures",{"date":date_str})

    def lineups(self,fixture_id):
        return self._get("fixtures/lineups",{"fixture":int(fixture_id)})

    def injuries(self,fixture_id):
        return self._get("injuries",{"fixture":int(fixture_id)})
