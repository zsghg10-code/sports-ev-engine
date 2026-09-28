
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import requests

BASE = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SportsEVEngine/2.4)",
    "Accept": "application/json,text/plain,*/*",
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
        "hanshintigers": "hanshin",
        "yomiurigiants": "yomiuri",
        "yokohamadenabaystars": "yokohama",
        "tokyoyakultswallows": "yakult",
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

    def _get(self, path):
        r = self.s.get(BASE + path, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def scheduled_events(self, date_iso: str):
        data = self._get(f"/sport/baseball/scheduled-events/{date_iso}")
        return data.get("events", [])

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

    def tournament_last_events(self, tournament_id: int, season_id: int, pages=3):
        out = []
        for page in range(max(1, int(pages))):
            try:
                data = self._get(
                    f"/unique-tournament/{int(tournament_id)}/season/{int(season_id)}/events/last/{page}"
                )
            except Exception:
                break
            events = data.get("events", [])
            out.extend(events)
            if not data.get("hasNextPage"):
                break
        return out

    def recent_events_fallback(self, league_hint: str, days=24, end_date=None):
        """
        Fallback when tournament history endpoint is unavailable.
        Scans daily baseball schedules and filters tournament name.
        """
        end = end_date or datetime.now(timezone.utc).date()
        hint = norm(league_hint)
        rows = []
        for i in range(int(days)):
            d = (end - timedelta(days=i)).isoformat()
            try:
                events = self.scheduled_events(d)
            except Exception:
                continue
            for e in events:
                t = e.get("tournament", {})
                u = t.get("uniqueTournament", {})
                text = " ".join([
                    str(t.get("name","")),
                    str(u.get("name","")),
                    str(u.get("slug","")),
                ])
                nt = norm(text)
                if hint == "kbo" and "kbo" in nt:
                    rows.append(e)
                elif hint == "npb" and ("npb" in nt or "professionalbaseball" in nt):
                    rows.append(e)
        return rows

    @staticmethod
    def event_meta(e):
        t = e.get("tournament", {})
        u = t.get("uniqueTournament", {}) or {}
        season = e.get("season", {}) or {}
        return {
            "event_id": e.get("id"),
            "home_name": (e.get("homeTeam") or {}).get("name"),
            "away_name": (e.get("awayTeam") or {}).get("name"),
            "home_id": (e.get("homeTeam") or {}).get("id"),
            "away_id": (e.get("awayTeam") or {}).get("id"),
            "tournament_id": u.get("id") or t.get("id"),
            "tournament_name": u.get("name") or t.get("name"),
            "season_id": season.get("id"),
            "start_ts": e.get("startTimestamp"),
            "status": (e.get("status") or {}).get("type"),
            "home_score": ((e.get("homeScore") or {}).get("current")
                           if isinstance(e.get("homeScore"), dict) else None),
            "away_score": ((e.get("awayScore") or {}).get("current")
                           if isinstance(e.get("awayScore"), dict) else None),
        }

    def find_event(self, home_name: str, away_name: str, commence_iso: str):
        dt = datetime.fromisoformat(str(commence_iso).replace("Z","+00:00")).astimezone(timezone.utc)
        candidates = []
        for offset in (-1, 0, 1):
            d = (dt.date() + timedelta(days=offset)).isoformat()
            try:
                candidates.extend(self.scheduled_events(d))
            except Exception:
                pass

        hq, aq = norm(home_name), norm(away_name)
        best = None
        best_score = -1
        for e in candidates:
            m = self.event_meta(e)
            h, a = norm(m["home_name"]), norm(m["away_name"])
            score = 0
            if h == hq: score += 3
            elif hq in h or h in hq: score += 2
            if a == aq: score += 3
            elif aq in a or a in aq: score += 2
            # account for reversed provider naming only as weak fallback
            if norm(m["home_name"]) == aq and norm(m["away_name"]) == hq:
                score = max(score, 2)
            if score > best_score:
                best_score = score
                best = e
        return best if best_score >= 4 else None


def extract_lineup_players(payload):
    """
    Tolerates several SofaScore lineup response shapes.
    Returns {'home': [...], 'away': [...], 'confirmed': bool}.
    """
    result = {"home": [], "away": [], "confirmed": False}
    if not isinstance(payload, dict):
        return result
    result["confirmed"] = bool(payload.get("confirmed", False))

    for side in ("home", "away"):
        block = payload.get(side) or {}
        players = (
            block.get("startingLineup")
            or block.get("players")
            or block.get("starters")
            or []
        )
        out = []
        for item in players:
            p = item.get("player", item) if isinstance(item, dict) else {}
            if not isinstance(p, dict):
                continue
            out.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "position": item.get("position") if isinstance(item, dict) else None or p.get("position"),
                "starter": item.get("starter", True) if isinstance(item, dict) else True,
                "order": item.get("order") if isinstance(item, dict) else None,
                "raw": item,
            })
        result[side] = out
    return result


def find_starting_pitcher(players):
    if not players:
        return None
    for p in players:
        pos = str(p.get("position") or "").upper()
        raw = p.get("raw") or {}
        name = str(p.get("name") or "")
        if pos in {"P", "SP", "PITCHER"} or "pitch" in pos.lower():
            return p
        if raw.get("pitcher") is True or raw.get("startingPitcher") is True:
            return p
    return None


def _walk_numeric(obj, out=None, prefix=""):
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k,v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (int,float)) and not isinstance(v,bool):
                out[key.lower()] = float(v)
            else:
                _walk_numeric(v,out,key)
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            _walk_numeric(v,out,f"{prefix}[{i}]")
    return out


def extract_pitcher_metrics(payload):
    nums = _walk_numeric(payload)
    def pick(*patterns):
        for pat in patterns:
            pat = pat.lower()
            for k,v in nums.items():
                if k.endswith(pat) or pat in k:
                    return v
        return None

    era = pick("earnedrunaverage","era")
    whip = pick("whip")
    k = pick("strikeouts","strikeout")
    bb = pick("walks","basesonballs","walk")
    ip = pick("inningspitched","innings")
    apps = pick("appearances","gamesplayed","games")
    kbb = None
    if k is not None and bb not in (None,0):
        kbb = k/bb
    return {
        "era":era, "whip":whip, "strikeouts":k, "walks":bb,
        "innings":ip, "appearances":apps, "kbb":kbb,
    }
