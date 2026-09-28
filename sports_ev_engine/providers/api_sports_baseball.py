
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re
import requests

BASE = "https://v1.baseball.api-sports.io"


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


def _first_number(*vals):
    for v in vals:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            if v is not None and str(v).strip() != "":
                return float(v)
        except Exception:
            pass
    return None


class APISportsBaseball:
    """
    Stable KBO/NPB history provider for Streamlit Cloud.

    Authentication:
      x-apisports-key

    The same API-Sports account key can be used for the baseball API when the
    account has Baseball access. API_BASEBALL_KEY is preferred, with the app
    falling back to API_FOOTBALL_KEY.
    """

    def __init__(self, api_key: str, timeout: int = 25):
        if not api_key:
            raise ValueError("API-Sports key is missing")
        self.api_key = api_key
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            "x-apisports-key": api_key,
            "Accept": "application/json",
            "User-Agent": "SportsEVEngine/2.4.2",
        })
        self.last_error = None
        self.last_headers = {}
        self._league_cache = {}
        self._games_cache = {}
        self._team_ids = {}
        self.last_resolved = None

    def _get(self, path: str, params=None):
        try:
            r = self.s.get(BASE + path, params=params or {}, timeout=self.timeout)
            self.last_headers = {str(k).lower(): v for k, v in r.headers.items()}
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            raise RuntimeError(self.last_error)

        errors = data.get("errors")
        if errors:
            if isinstance(errors, dict):
                msg = "; ".join(f"{k}: {v}" for k, v in errors.items() if v)
            else:
                msg = str(errors)
            if msg.strip():
                self.last_error = msg
                raise RuntimeError(msg)

        self.last_error = None
        return data.get("response", data.get("data", []))

    def remaining_requests(self):
        for k in (
            "x-ratelimit-requests-remaining",
            "x-requests-remaining",
            "x-ratelimit-remaining",
        ):
            if k in self.last_headers:
                return self.last_headers[k]
        return None

    @staticmethod
    def _league_name(item):
        return str(item.get("name") or (item.get("league") or {}).get("name") or "")

    @staticmethod
    def _league_id(item):
        return item.get("id") or (item.get("league") or {}).get("id")

    @staticmethod
    def _seasons(item):
        seasons = item.get("seasons") or []
        if not seasons:
            s = (item.get("league") or {}).get("season")
            if s:
                seasons = [{"season": s}]
        return seasons

    def resolve_league(self, league: str, target_year: int | None = None):
        lg = str(league).upper()
        year = int(target_year or datetime.now(timezone.utc).year)
        cache_key = (lg, year)
        if cache_key in self._league_cache:
            return self._league_cache[cache_key]

        country = "Japan" if lg == "NPB" else "South-Korea"
        queries = [
            {"search": lg, "season": year},
            {"country": country, "season": year},
            {"season": year},
            {"search": lg},
        ]
        candidates = []
        last_exc = None
        for q in queries:
            try:
                rows = self._get("/leagues", q)
                if isinstance(rows, dict):
                    rows = [rows]
                if rows:
                    candidates.extend(rows)
                if candidates:
                    break
            except Exception as e:
                last_exc = e

        if not candidates:
            raise RuntimeError(self.last_error or str(last_exc or "No league rows returned"))

        def score(item):
            n = norm(self._league_name(item))
            country_obj = item.get("country") or {}
            country_text = norm(
                str(country_obj.get("name") if isinstance(country_obj, dict) else country_obj)
            )
            s = 0
            if lg == "NPB":
                if n == "npb": s += 200
                elif "npb" in n: s += 150
                if "japan" in country_text: s += 30
                if "minor" in n: s -= 100
            else:
                if n == "kbo": s += 200
                elif "kbo" in n: s += 150
                if "korea" in country_text: s += 30
                if "futures" in n: s -= 100
            seasons = self._seasons(item)
            for se in seasons:
                sy = se.get("season", se.get("year"))
                try:
                    if int(sy) == year:
                        s += 80
                except Exception:
                    pass
            return s

        best = max(candidates, key=score)
        if score(best) < 100:
            raise RuntimeError(f"{lg} league not found in API-Sports Baseball")

        league_id = self._league_id(best)
        seasons = self._seasons(best)
        season_year = year
        if seasons:
            scored = []
            for se in seasons:
                sy = se.get("season", se.get("year"))
                try:
                    syi = int(str(sy)[:4])
                except Exception:
                    continue
                sc = 100 if syi == year else -abs(syi - year)
                if se.get("current") is True:
                    sc += 20
                scored.append((sc, syi))
            if scored:
                scored.sort(reverse=True)
                season_year = scored[0][1]

        resolved = {
            "tournament": {
                "id": league_id,
                "name": self._league_name(best) or lg,
            },
            "season": {
                "id": season_year,
                "year": season_year,
                "name": str(season_year),
            },
            "raw": best,
        }
        self.last_resolved = (league_id, season_year)
        self._league_cache[cache_key] = resolved
        return resolved

    @staticmethod
    def _status_short(g):
        st = g.get("status")
        if isinstance(st, dict):
            return str(st.get("short") or st.get("type") or st.get("long") or "")
        return str(st or "")

    @staticmethod
    def _team_obj(g, side):
        teams = g.get("teams") or {}
        t = teams.get(side) or {}
        if not t:
            t = g.get(f"{side}_team") or {}
        return t if isinstance(t, dict) else {"name": str(t)}

    @staticmethod
    def _score_total(g, side):
        scores = g.get("scores") or {}
        s = scores.get(side)
        if isinstance(s, dict):
            return _first_number(s.get("total"), s.get("runs"), s.get("score"), s.get("current"))
        if s is not None:
            return _first_number(s)

        s2 = g.get(f"{side}_score")
        if isinstance(s2, dict):
            return _first_number(s2.get("total"), s2.get("runs"), s2.get("current"))
        return _first_number(s2)

    @staticmethod
    def _timestamp(g):
        ts = g.get("timestamp")
        if ts:
            try:
                return int(ts)
            except Exception:
                pass

        date_val = g.get("date")
        time_val = g.get("time")
        if isinstance(date_val, str):
            text = date_val
            if time_val and "T" not in text:
                text = f"{date_val}T{time_val}"
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except Exception:
                pass
        return 0

    def _convert_game(self, g, league_id=None, season=None):
        league = g.get("league") or {}
        home = self._team_obj(g, "home")
        away = self._team_obj(g, "away")
        hs = self._score_total(g, "home")
        aws = self._score_total(g, "away")
        st = self._status_short(g).upper()

        if st in {"FT", "FINISHED", "FINAL", "AOT"} or "FINISH" in st or "FINAL" in st:
            status_type = "finished"
        elif st in {"NS", "NOT STARTED", "SCHEDULED"}:
            status_type = "notstarted"
        else:
            status_type = st.lower() or "unknown"

        lid = league.get("id") or league_id
        lname = league.get("name") or ""
        lseason = league.get("season") or season
        return {
            "id": g.get("id"),
            "startTimestamp": self._timestamp(g),
            "status": {"type": status_type},
            "homeTeam": {
                "id": home.get("id"),
                "name": home.get("name"),
            },
            "awayTeam": {
                "id": away.get("id"),
                "name": away.get("name"),
            },
            "homeScore": {"current": hs},
            "awayScore": {"current": aws},
            "tournament": {
                "id": lid,
                "name": lname,
                "uniqueTournament": {
                    "id": lid,
                    "name": lname,
                },
            },
            "season": {
                "id": lseason,
                "year": lseason,
                "name": str(lseason or ""),
            },
            "_api_sports_raw": g,
        }

    def games_for_league(self, league_id: int, season: int):
        key = (int(league_id), int(season))
        if key in self._games_cache:
            return self._games_cache[key]
        rows = self._get("/games", {"league": int(league_id), "season": int(season)})
        if isinstance(rows, dict):
            rows = [rows]
        converted = [self._convert_game(g, league_id, season) for g in rows]
        self._games_cache[key] = converted
        for e in converted:
            h = e.get("homeTeam") or {}
            a = e.get("awayTeam") or {}
            if h.get("name") and h.get("id"):
                self._team_ids[norm(h["name"])] = h["id"]
            if a.get("name") and a.get("id"):
                self._team_ids[norm(a["name"])] = a["id"]
        return converted

    # Compatibility methods expected by auto_baseball.py
    def event_meta(self, e):
        t = e.get("tournament") or {}
        u = t.get("uniqueTournament") or {}
        s = e.get("season") or {}
        return {
            "event_id": e.get("id"),
            "home_name": (e.get("homeTeam") or {}).get("name"),
            "away_name": (e.get("awayTeam") or {}).get("name"),
            "home_id": (e.get("homeTeam") or {}).get("id"),
            "away_id": (e.get("awayTeam") or {}).get("id"),
            "tournament_id": u.get("id") or t.get("id"),
            "tournament_name": u.get("name") or t.get("name"),
            "season_id": s.get("id") or s.get("year"),
            "start_ts": e.get("startTimestamp"),
            "status": (e.get("status") or {}).get("type"),
            "home_score": (e.get("homeScore") or {}).get("current"),
            "away_score": (e.get("awayScore") or {}).get("current"),
        }

    def tournament_last_events(self, tournament_id: int, season_id: int, pages=8):
        return self.games_for_league(int(tournament_id), int(season_id))

    def recent_events_fallback(self, league_hint: str, days=35, end_date=None):
        resolved = self.resolve_league(league_hint, datetime.now(timezone.utc).year)
        tid = resolved["tournament"]["id"]
        sy = resolved["season"]["year"]
        return self.games_for_league(tid, sy)

    def find_event(self, home_name: str, away_name: str, commence_iso: str):
        # Do not spend an extra request. Match from the already-cached season pool.
        hq, aq = norm(home_name), norm(away_name)
        for rows in self._games_cache.values():
            for e in rows:
                h = norm((e.get("homeTeam") or {}).get("name"))
                a = norm((e.get("awayTeam") or {}).get("name"))
                if h == hq and a == aq:
                    return e
        return None

    def find_team_id(self, team_name: str):
        target = norm(team_name)
        if target in self._team_ids:
            return self._team_ids[target]
        # Usually unnecessary because all league games have already populated IDs.
        return None

    def team_last_events(self, team_id: int, pages=3):
        if not self.last_resolved:
            return []
        lid, season = self.last_resolved
        all_games = self.games_for_league(lid, season)
        out = []
        for e in all_games:
            hi = (e.get("homeTeam") or {}).get("id")
            ai = (e.get("awayTeam") or {}).get("id")
            if str(team_id) in {str(hi), str(ai)}:
                out.append(e)
        out.sort(key=lambda x: int(x.get("startTimestamp") or 0), reverse=True)
        return out[:40]

    # API-Sports Baseball v1 does not currently expose a reliable starting-lineup
    # feed comparable to the former SofaScore dependency. Keep these methods so
    # the model degrades safely instead of inventing lineup data.
    def lineups(self, event_id: int):
        return {}

    def player_season_stats(self, player_id: int, tournament_id: int, season_id: int):
        return {}
