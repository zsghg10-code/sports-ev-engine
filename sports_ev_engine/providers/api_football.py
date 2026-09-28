
import requests
import hashlib
import json
import time
from threading import Lock
from datetime import date, datetime, timezone

PROVIDER_BUILD = "3.0.0"

class FootballAccessError(RuntimeError):
    """Account-wide failure: stop the batch instead of repeating it for every team."""

class FootballPlanError(FootballAccessError):
    pass

BASE="https://v3.football.api-sports.io"

class APIFootball:
    _states = {}
    _lock = Lock()

    def __init__(self, api_key):
        if not api_key:
            raise RuntimeError("API_FOOTBALL_KEY가 없습니다.")
        self.headers={"x-apisports-key":api_key}
        key=hashlib.sha256(api_key.encode()).hexdigest()
        with self._lock:
            self.state=self._states.setdefault(key,{"cache":{},"next":0,"blocked":0,"last_supported":True})

    def _get(self,path,params):
        cache_key=(path,json.dumps(params,sort_keys=True))
        with self._lock:
            now=time.monotonic()
            cached=self.state["cache"].get(cache_key)
            if cached and now<cached[0]:return cached[1]
            if now<self.state["blocked"]:
                raise FootballAccessError("API-Football 요청 제한: 잠시 후 다시 실행하세요. 같은 실패 요청의 반복을 중단했습니다.")
            wait=max(0,self.state["next"]-now)
            self.state["next"]=now+wait+6.2  # At most 10/minute per key in this process.
        if wait:time.sleep(wait)
        r=requests.get(f"{BASE}/{path}",headers=self.headers,params=params,timeout=45)
        if r.status_code==429:
            try:cooldown=max(60,float(r.headers.get("Retry-After",60)))
            except (ValueError,TypeError):cooldown=60
            self.state["blocked"]=time.monotonic()+cooldown
            raise FootballAccessError("API-Football 429: 분당 또는 일일 요청 한도입니다. 대시보드의 잔여량을 확인하세요. 배치 수집을 중단했습니다.")
        if r.status_code in (401,403):
            raise FootballAccessError(f"API-Football 인증/접근 오류 ({r.status_code}): 키와 플랜 권한을 확인하세요.")
        r.raise_for_status()
        body=r.json()
        errors=body.get("errors")
        if errors:
            if isinstance(errors,dict) and "plan" in errors:
                raise FootballPlanError(str(errors["plan"]))
            if any(s in str(errors).lower() for s in ("rate", "limit", "request", "token", "access")):
                self.state["blocked"]=time.monotonic()+60
                raise FootballAccessError(f"API-Football 수집 제한: {errors}")
            raise RuntimeError(str(errors))
        result=body.get("response",[])
        ttl=86400 if path=="teams" else 900
        with self._lock:self.state["cache"][cache_key]=(time.monotonic()+ttl,result)
        return result

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

    def team_recent_fixtures(self,team_id,last=16,cutoff_iso=None):
        if self.state["last_supported"]:
            try:return self._get("fixtures",{"team":int(team_id),"last":int(last)})
            except FootballPlanError as e:
                if "last" not in str(e).lower():raise
                self.state["last_supported"]=False
        cutoff=datetime.fromisoformat(str(cutoff_iso).replace("Z","+00:00")) if cutoff_iso else datetime.now(timezone.utc)
        found={}
        for season in range(cutoff.year,cutoff.year-3,-1):
            try:fixtures=self._get("fixtures",{"team":int(team_id),"season":season})
            except FootballPlanError as e:
                raise FootballPlanError(f"API-Football 플랜이 {season} 시즌 조회를 허용하지 않습니다. 최근 A매치 분석을 중단했습니다. 제공사 응답: {e}") from e
            for fx in fixtures:
                f=fx.get("fixture",{});ts=f.get("timestamp") or 0
                if f.get("status",{}).get("short") in {"FT","AET","PEN"} and 0<ts<cutoff.timestamp():
                    found[f["id"]]=fx
            if len(found)>=last:break
        return sorted(found.values(),key=lambda x:x["fixture"]["timestamp"],reverse=True)[:last]

    def lineups(self,fixture_id):
        return self._get("fixtures/lineups",{"fixture":int(fixture_id)})

    def injuries(self,fixture_id):
        return self._get("injuries",{"fixture":int(fixture_id)})

    def fixture_statistics(self,fixture_id):
        return self._get("fixtures/statistics",{"fixture":int(fixture_id)})

    def fixture_players(self,fixture_id):
        return self._get("fixtures/players",{"fixture":int(fixture_id)})

    def team_players(self,team_id,season,max_pages=2):
        out=[]
        for page in range(1,max(1,int(max_pages))+1):
            rows=self._get("players",{"team":int(team_id),"season":int(season),"page":page})
            if not rows:break
            out.extend(rows)
            if len(rows)<20:break
        # Keep one record per player id; provider can repeat a player across competitions.
        merged={}
        for row in out:
            pid=(row.get("player") or {}).get("id")
            if pid is None:continue
            if pid not in merged:merged[pid]=row
            else:merged[pid].setdefault("statistics",[]).extend(row.get("statistics") or [])
        return list(merged.values())
