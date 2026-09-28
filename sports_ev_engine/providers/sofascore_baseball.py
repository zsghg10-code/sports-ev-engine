
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import re
import requests

API_HOSTS = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def norm(s: str) -> str:
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9가-힣ぁ-んァ-ヶ一-龯]+", "", s)
    aliases = {
        "doosanbears": "doosan",
        "lgwins": "lg",
        "kiatigers": "kia",
        "ktwiz": "kt",
        "ssglanders": "ssg",
        "samsunglions": "samsung",
        "lottegiants": "lotte",
        "hanwhaeagles": "hanwha",
        "ncdinos": "nc",
        "kiwoomheroes": "kiwoom",
        "fukuokasoftbankhawks": "softbank",
        "softbankhawks": "softbank",
        "hokkaidonipponhamfighters": "nipponham",
        "nipponhamfighters": "nipponham",
        "orixbuffaloes": "orix",
        "chibalottemarines": "chibalotte",
        "saitamaseibulions": "seibu",
        "tohokurakutengoldeneagles": "rakuten",
        "rakutengoldeneagles": "rakuten",
        "hanshintigers": "hanshin",
        "yomiurigiants": "yomiuri",
        "yokohamadenabaystars": "yokohama",
        "yokohamabaystars": "yokohama",
        "tokyoyakultswallows": "yakult",
        "yakultswallows": "yakult",
        "hiroshimacarp": "hiroshima",
        "hiroshimatoyocarp": "hiroshima",
        "chunichidragons": "chunichi",
    }
    return aliases.get(s, s)


class SofaScoreBaseball:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.last_error = None
        self.last_host = None

    def _get(self, path):
        errors=[]
        for base in API_HOSTS:
            try:
                r = self.s.get(base + path, timeout=self.timeout)
                r.raise_for_status()
                self.last_host = base
                self.last_error = None
                return r.json()
            except Exception as e:
                errors.append(f"{base}: {type(e).__name__}: {e}")
        self.last_error = " | ".join(errors)
        raise RuntimeError(self.last_error)

    def scheduled_events(self, date_iso: str):
        data = self._get(f"/sport/baseball/scheduled-events/{date_iso}")
        return data.get("events", [])

    def unique_tournaments(self):
        data = self._get("/sport/baseball/unique-tournaments")
        # response shape differs by release
        return data.get("uniqueTournaments") or data.get("tournaments") or []

    def seasons(self, tournament_id: int):
        data = self._get(f"/unique-tournament/{int(tournament_id)}/seasons")
        return data.get("seasons", [])

    def resolve_league(self, league: str, target_year: int | None = None):
        """
        Discover NPB/KBO directly from SofaScore's baseball tournament list.
        This avoids depending on the daily schedule endpoint just to learn league/season IDs.
        """
        lg = str(league).upper()
        tournaments = self.unique_tournaments()
        candidates=[]
        for t in tournaments:
            name=str(t.get("name",""))
            slug=str(t.get("slug",""))
            cat=t.get("category") or {}
            cat_name=str(cat.get("name",""))
            flag=str(cat.get("flag",""))
            n=norm(" ".join([name,slug,cat_name,flag]))
            score=0
            if lg=="NPB":
                if "npb" in n: score+=100
                if "japan" in n or flag.lower() in {"jp","japan"}: score+=20
                if "professional" in n and "baseball" in n: score+=10
            elif lg=="KBO":
                if "kbo" in n: score+=100
                if "korea" in n or flag.lower() in {"kr","south-korea","korea"}: score+=20
                if "league" in n: score+=5
            if score:
                candidates.append((score,t))
        if not candidates:
            return None
        candidates.sort(key=lambda x:x[0],reverse=True)
        tournament=candidates[0][1]
        tid=tournament.get("id")
        seasons=self.seasons(int(tid)) if tid is not None else []
        if not seasons:
            return {"tournament":tournament,"season":None}

        target_year = int(target_year or datetime.now(timezone.utc).year)

        def season_score(s):
            sc=0
            name=str(s.get("name",""))
            year=s.get("year")
            if year is not None:
                try:
                    y=int(year)
                    if y==target_year: sc+=100
                    sc -= min(abs(y-target_year),50)
                except Exception:
                    pass
            if str(target_year) in name: sc+=80
            # Prefer current-ish seasons
            if s.get("id") is not None: sc+=0.001*float(s["id"])
            return sc
        seasons=sorted(seasons,key=season_score,reverse=True)
        return {"tournament":tournament,"season":seasons[0]}

    def event(self, event_id: int):
        data = self._get(f"/event/{int(event_id)}")
        return data.get("event", data)

    def lineups(self, event_id: int):
        try:
            return self._get(f"/event/{int(event_id)}/lineups")
        except Exception:
            return {}

    def statistics(self, event_id: int):
        try:
            return self._get(f"/event/{int(event_id)}/statistics")
        except Exception:
            return {}

    def team_season_stats(self, team_id: int, tournament_id: int, season_id: int):
        try:
            return self._get(
                f"/team/{int(team_id)}/unique-tournament/{int(tournament_id)}/"
                f"season/{int(season_id)}/statistics/overall"
            )
        except Exception:
            return {}

    def player_season_stats(self, player_id: int, tournament_id: int, season_id: int):
        try:
            return self._get(
                f"/player/{int(player_id)}/unique-tournament/{int(tournament_id)}/"
                f"season/{int(season_id)}/statistics/overall"
            )
        except Exception:
            return {}

    def tournament_last_events(self, tournament_id: int, season_id: int, pages=6):
        out=[]
        seen=set()
        for page in range(max(1,int(pages))):
            try:
                data=self._get(
                    f"/unique-tournament/{int(tournament_id)}/season/{int(season_id)}/events/last/{page}"
                )
            except Exception:
                break
            for e in data.get("events",[]):
                eid=e.get("id")
                if eid not in seen:
                    seen.add(eid); out.append(e)
            if not data.get("hasNextPage"):
                break
        return out

    def team_last_events(self, team_id: int, pages=2):
        out=[]; seen=set()
        for page in range(max(1,int(pages))):
            try:
                data=self._get(f"/team/{int(team_id)}/events/last/{page}")
            except Exception:
                break
            for e in data.get("events",[]):
                eid=e.get("id")
                if eid not in seen:
                    seen.add(eid); out.append(e)
            if not data.get("hasNextPage"):
                break
        return out

    def search_all(self, query: str):
        try:
            data=self._get(f"/search/all?q={quote(str(query))}")
            return data.get("results",[]) or data.get("searchResults",[]) or []
        except Exception:
            return []

    def find_team_id(self, team_name: str):
        target=norm(team_name)
        best=None; best_score=-1
        for item in self.search_all(team_name):
            ent=item.get("entity") or item.get("team") or item
            if not isinstance(ent,dict):
                continue
            # some search responses wrap type/entity differently
            name=str(ent.get("name",""))
            slug=str(ent.get("slug",""))
            typ=str(item.get("type") or ent.get("type") or "").lower()
            eid=ent.get("id")
            if eid is None: continue
            n=norm(name or slug)
            score=0
            if n==target: score=100
            elif target in n or n in target: score=70
            if "team" in typ: score+=10
            # verify baseball when sport data exists
            sport=(ent.get("sport") or {}).get("slug")
            if sport=="baseball": score+=20
            if score>best_score:
                best_score=score; best=eid
        return best if best_score>=60 else None

    def recent_events_fallback(self, league_hint: str, days=35, end_date=None):
        end=end_date or datetime.now(timezone.utc).date()
        hint=str(league_hint).upper()
        rows=[]
        for i in range(int(days)):
            d=(end-timedelta(days=i)).isoformat()
            try:
                events=self.scheduled_events(d)
            except Exception:
                continue
            for e in events:
                t=e.get("tournament",{})
                u=t.get("uniqueTournament",{}) or {}
                cat=t.get("category",{}) or u.get("category",{}) or {}
                text=" ".join([
                    str(t.get("name","")),
                    str(t.get("slug","")),
                    str(u.get("name","")),
                    str(u.get("slug","")),
                    str(cat.get("name","")),
                    str(cat.get("flag","")),
                ])
                nt=norm(text)
                if hint=="KBO" and ("kbo" in nt or ("korea" in nt and "baseball" in nt)):
                    rows.append(e)
                elif hint=="NPB" and ("npb" in nt or ("japan" in nt and "baseball" in nt)):
                    rows.append(e)
        return rows

    @staticmethod
    def event_meta(e):
        t=e.get("tournament",{})
        u=t.get("uniqueTournament",{}) or {}
        season=e.get("season",{}) or {}
        return {
            "event_id":e.get("id"),
            "home_name":(e.get("homeTeam") or {}).get("name"),
            "away_name":(e.get("awayTeam") or {}).get("name"),
            "home_id":(e.get("homeTeam") or {}).get("id"),
            "away_id":(e.get("awayTeam") or {}).get("id"),
            "tournament_id":u.get("id") or t.get("id"),
            "tournament_name":u.get("name") or t.get("name"),
            "season_id":season.get("id"),
            "start_ts":e.get("startTimestamp"),
            "status":(e.get("status") or {}).get("type"),
            "home_score":((e.get("homeScore") or {}).get("current")
                          if isinstance(e.get("homeScore"),dict) else None),
            "away_score":((e.get("awayScore") or {}).get("current")
                          if isinstance(e.get("awayScore"),dict) else None),
        }

    def find_event(self, home_name: str, away_name: str, commence_iso: str):
        dt=datetime.fromisoformat(str(commence_iso).replace("Z","+00:00")).astimezone(timezone.utc)
        candidates=[]
        for offset in (-1,0,1):
            d=(dt.date()+timedelta(days=offset)).isoformat()
            try:
                candidates.extend(self.scheduled_events(d))
            except Exception:
                pass
        hq,aq=norm(home_name),norm(away_name)
        best=None; best_score=-1
        for e in candidates:
            m=self.event_meta(e)
            h,a=norm(m["home_name"]),norm(m["away_name"])
            score=0
            if h==hq: score+=3
            elif hq in h or h in hq: score+=2
            if a==aq: score+=3
            elif aq in a or a in aq: score+=2
            if h==aq and a==hq: score=max(score,2)
            if score>best_score:
                best_score=score; best=e
        return best if best_score>=4 else None


def extract_lineup_players(payload):
    result={"home":[],"away":[],"confirmed":False}
    if not isinstance(payload,dict):
        return result
    result["confirmed"]=bool(payload.get("confirmed",False))
    for side in ("home","away"):
        block=payload.get(side) or {}
        players=block.get("startingLineup") or block.get("players") or block.get("starters") or []
        out=[]
        for item in players:
            p=item.get("player",item) if isinstance(item,dict) else {}
            if not isinstance(p,dict): continue
            out.append({
                "id":p.get("id"),
                "name":p.get("name"),
                "position":(item.get("position") if isinstance(item,dict) else None) or p.get("position"),
                "starter":item.get("starter",True) if isinstance(item,dict) else True,
                "order":item.get("order") if isinstance(item,dict) else None,
                "raw":item,
            })
        result[side]=out
    return result


def find_starting_pitcher(players):
    if not players: return None
    for p in players:
        pos=str(p.get("position") or "").upper()
        raw=p.get("raw") or {}
        if pos in {"P","SP","PITCHER"} or "PITCH" in pos:
            return p
        if raw.get("pitcher") is True or raw.get("startingPitcher") is True:
            return p
    return None


def _walk_numeric(obj,out=None,prefix=""):
    if out is None: out={}
    if isinstance(obj,dict):
        for k,v in obj.items():
            key=f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v,(int,float)) and not isinstance(v,bool):
                out[key.lower()]=float(v)
            else:
                _walk_numeric(v,out,key)
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            _walk_numeric(v,out,f"{prefix}[{i}]")
    return out


def extract_pitcher_metrics(payload):
    nums=_walk_numeric(payload)
    def pick(*patterns):
        for pat in patterns:
            pat=pat.lower()
            for k,v in nums.items():
                if k.endswith(pat) or pat in k:
                    return v
        return None
    era=pick("earnedrunaverage","era")
    whip=pick("whip")
    k=pick("strikeouts","strikeout")
    bb=pick("walks","basesonballs","walk")
    ip=pick("inningspitched","innings")
    apps=pick("appearances","gamesplayed","games")
    kbb=None
    if k is not None and bb not in (None,0):
        kbb=k/bb
    return {"era":era,"whip":whip,"strikeouts":k,"walks":bb,
            "innings":ip,"appearances":apps,"kbb":kbb}
