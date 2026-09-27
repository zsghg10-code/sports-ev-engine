
import requests

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
        return self._get("fixtures",{"team":int(team_id),"last":int(last)})

    def fixtures_by_date(self,date_str):
        return self._get("fixtures",{"date":date_str})

    def lineups(self,fixture_id):
        return self._get("fixtures/lineups",{"fixture":int(fixture_id)})

    def injuries(self,fixture_id):
        return self._get("injuries",{"fixture":int(fixture_id)})
