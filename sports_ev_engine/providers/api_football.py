
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

    def search_leagues(self, name):
        return self._get("leagues", {"search": name})

    def league_fixtures(self, league_id, season):
        return self._get("fixtures", {
            "league": int(league_id),
            "season": int(season),
        })

    def search_team(self,name):
        return self._get("teams",{"search":name})

    def fixtures_by_date(self,date_str):
        return self._get("fixtures",{"date":date_str})

    def lineups(self,fixture_id):
        return self._get("fixtures/lineups",{"fixture":int(fixture_id)})

    def injuries(self,fixture_id):
        return self._get("injuries",{"fixture":int(fixture_id)})
