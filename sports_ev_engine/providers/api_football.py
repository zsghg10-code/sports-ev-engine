
import requests
from datetime import date, timedelta

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
        Free-plan compatible recent fixtures.
        API-Football Free plan can reject the `last` parameter, so we query
        a date window and take the most recent completed matches locally.
        """
        today=date.today()

        # National teams can have sparse schedules, so try progressively wider windows.
        windows=(120, 240, 420, 730)
        collected=[]
        seen=set()

        for days in windows:
            start=today-timedelta(days=days)
            try:
                rows=self._get("fixtures",{
                    "team":int(team_id),
                    "from":start.isoformat(),
                    "to":today.isoformat(),
                })
            except RuntimeError as e:
                # If a provider plan rejects a date window, try a smaller/fallback query.
                if not collected:
                    try:
                        rows=self._get("fixtures",{"team":int(team_id)})
                    except Exception:
                        raise e
                else:
                    rows=[]

            for fx in rows:
                fid=fx.get("fixture",{}).get("id")
                status=fx.get("fixture",{}).get("status",{}).get("short")
                goals=fx.get("goals",{})
                # Completed fixtures only; require score.
                if fid in seen:
                    continue
                if status not in {"FT","AET","PEN"}:
                    continue
                if goals.get("home") is None or goals.get("away") is None:
                    continue
                seen.add(fid)
                collected.append(fx)

            if len(collected) >= int(last):
                break

        collected.sort(
            key=lambda fx: fx.get("fixture",{}).get("timestamp",0),
            reverse=True
        )
        return collected[:int(last)]

    def fixtures_by_date(self,date_str):
        return self._get("fixtures",{"date":date_str})

    def lineups(self,fixture_id):
        return self._get("fixtures/lineups",{"fixture":int(fixture_id)})

    def injuries(self,fixture_id):
        return self._get("injuries",{"fixture":int(fixture_id)})
