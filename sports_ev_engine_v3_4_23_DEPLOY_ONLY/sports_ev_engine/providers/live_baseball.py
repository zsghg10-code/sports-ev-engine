from __future__ import annotations

from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import json
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

from sports_ev_engine.providers.official_baseball import canonical_english, NPB_NAME_MAP

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0 Safari/537.36"
)

KBO_GAME_LIST_URL = "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList"
KBO_SCHEDULE_BASE = "https://www.koreabaseball.com/ws/Schedule.asmx"
KBO_REFERER = "https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx"
KBO_PITCHER_STATS = "https://www.koreabaseball.com/Record/Player/PitcherBasic/Basic1.aspx"
NAVER_KBO_CALENDAR = "https://api-gw.sports.naver.com/schedule/calendar"
NAVER_KBO_GAMES = "https://api-gw.sports.naver.com/schedule/games"
NAVER_KBO_PREVIEW = "https://api-gw.sports.naver.com/schedule/games/{game_id}/preview"
NAVER_KBO_RELAY = "https://api-gw.sports.naver.com/schedule/games/{game_id}/relay"
NAVER_KBO_POLLING = "https://api-gw.sports.naver.com/schedule/games/{game_id}/game-polling"

PROVIDER_BUILD = "3.0.0"
LIVE_BASEBALL_BUILD = "3.4.20"

NPB_GAMES = "https://npb.jp/games/{year}/"
NPB_STARTERS = "https://npb.jp/announcement/starter/"
NPB_BATTING = "https://npb.jp/bis/{year}/stats/idb1_{code}.html"
NPB_PITCHING = "https://npb.jp/bis/{year}/stats/idp1_{code}.html"

KBO_EN_BY_KR = {
    "LG": "LG Twins", "한화": "Hanwha Eagles", "SSG": "SSG Landers",
    "삼성": "Samsung Lions", "NC": "NC Dinos", "KT": "KT Wiz",
    "롯데": "Lotte Giants", "KIA": "KIA Tigers", "두산": "Doosan Bears",
    "키움": "Kiwoom Heroes",
}


NPB_FULL_MAP = dict(NPB_NAME_MAP)
NPB_FULL_MAP.update({
    "福岡ソフトバンクホークス": "Fukuoka SoftBank Hawks",
    "北海道日本ハムファイターズ": "Hokkaido Nippon-Ham Fighters",
    "オリックス・バファローズ": "Orix Buffaloes",
    "東北楽天ゴールデンイーグルス": "Tohoku Rakuten Golden Eagles",
    "埼玉西武ライオンズ": "Saitama Seibu Lions",
    "千葉ロッテマリーンズ": "Chiba Lotte Marines",
    "阪神タイガース": "Hanshin Tigers",
    "横浜DeNAベイスターズ": "Yokohama DeNA BayStars",
    "読売ジャイアンツ": "Yomiuri Giants",
    "中日ドラゴンズ": "Chunichi Dragons",
    "広島東洋カープ": "Hiroshima Toyo Carp",
    "東京ヤクルトスワローズ": "Tokyo Yakult Swallows",
})

NPB_TEAM_CODE = {
    "Fukuoka SoftBank Hawks": "h", "Hokkaido Nippon-Ham Fighters": "f",
    "Orix Buffaloes": "b", "Tohoku Rakuten Golden Eagles": "e",
    "Saitama Seibu Lions": "l", "Chiba Lotte Marines": "m",
    "Hanshin Tigers": "t", "Yokohama DeNA BayStars": "db",
    "Yomiuri Giants": "g", "Chunichi Dragons": "d",
    "Hiroshima Toyo Carp": "c", "Tokyo Yakult Swallows": "s",
}


def _norm(s):
    return re.sub(r"[^a-z0-9가-힣ぁ-んァ-ヶ一-龯]+", "", str(s or "").lower())


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _num(v):
    try:
        s = str(v).replace(",", "").strip()
        if s in {"", "-", "nan", "None"}:
            return None
        return float(s)
    except Exception:
        return None


def _flag_true(v):
    """Normalize KBO API flags without treating string "0"/"N" as truthy."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) == 1
    t = str(v or "").strip().lower()
    return t in {"1", "true", "y", "yes"}


def _clock_minutes(v):
    """Parse KBO G_TM values such as 18:30, 1830, or 18.30."""
    t = str(v or "").strip()
    m = re.search(r"(?<!\d)(\d{1,2})[:.]?(\d{2})(?!\d)", t)
    if not m:
        return None
    h, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= minute <= 59):
        return None
    return h * 60 + minute


def _npb_ip(v):
    try:
        s = str(v).strip()
        if "." not in s:
            return float(s)
        whole, frac = s.split(".", 1)
        base = float(whole or 0)
        if frac == "1":
            return base + 1/3
        if frac == "2":
            return base + 2/3
        return float(s)
    except Exception:
        return None


def _same_team(a, b):
    # KBO/Naver frequently mixes Korean short names (롯데/키움/두산...) with
    # English odds-provider names. Normalize those aliases before the generic
    # canonical matcher; otherwise a published Naver lineup can be discarded
    # as a false team mismatch even though the game is correct.
    aa = KBO_EN_BY_KR.get(_clean(a), a)
    bb = KBO_EN_BY_KR.get(_clean(b), b)
    ca, cb = canonical_english(aa), canonical_english(bb)
    if ca == cb:
        return True
    na, nb = _norm(ca), _norm(cb)
    return bool(na and nb and (na == nb or na in nb or nb in na))


def _kst_dt(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul"))


def _parse_make_table(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    rows = []
    for row in (raw or {}).get("rows", []):
        rows.append([_clean(c.get("Text", "")) for c in row.get("row", [])])
    return rows


def _parse_make_table_rich(raw):
    """Same KBO table, but keep playerId embedded in anchor HTML."""
    if not raw:
        return []
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception:return []
    out=[]
    for row in (raw or {}).get("rows",[]):
        vals=[]; ids=[]
        for c in row.get("row",[]):
            raw_text=str(c.get("Text",""))
            vals.append(_clean(BeautifulSoup(raw_text,"html.parser").get_text(" ",strip=True)))
            m=re.search(r"playerId=(\d+)",raw_text,re.I)
            ids.append(m.group(1) if m else None)
        out.append((vals,ids))
    return out


def _starter_player_id(game, home=True):
    if not isinstance(game,dict):return None
    pref="B_" if home else "T_"
    for k,v in game.items():
        ku=str(k).upper()
        if not ku.startswith(pref):continue
        if ("PIT" in ku or "PITCH" in ku) and ("ID" in ku or "CODE" in ku):
            sv=str(v or "").strip()
            if sv.isdigit():return sv
    return None


def empty_context(league, note=""):
    return {
        "league": league,
        "stage": "PRE-LINEUP",
        "starter_confirmed": False,
        "lineup_confirmed": False,
        "home_starter": None,
        "away_starter": None,
        "home_starter_stats": {},
        "away_starter_stats": {},
        "home_lineup": [],
        "away_lineup": [],
        "home_lineup_strength": None,
        "away_lineup_strength": None,
        "source": f"{league} official",
        "note": note,
        "game_id": None,
        "game_url": None,
    }


class KBOOfficialLive:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self._games = {}
        self._games_cached_at = {}
        self._pitchers = None
        self._naver_calendar_cache = {}
        self._naver_games_cache = {}
        self._naver_preview_cache = {}
        self._naver_preview_cached_at = {}
        self._naver_aux_cache = {}
        self._naver_aux_cached_at = {}
        # Mutable pregame feeds must refresh while a long-running monitor stays
        # alive.  Without TTLs an empty 17:00 lineup response could remain cached
        # at 18:17 even after the official order had been published.
        self.live_cache_ttl = 45.0

    def _game_list(self, date8):
        cached_at=self._games_cached_at.get(date8,0.0)
        if date8 in self._games and (time.monotonic()-cached_at) < self.live_cache_ttl:
            return self._games[date8]
        r = self.s.post(
            KBO_GAME_LIST_URL,
            json={"leId": "1", "srId": "0,9,6", "date": date8},
            headers={"Content-Type": "application/json; charset=UTF-8", "Referer": KBO_REFERER, "User-Agent": UA},
            timeout=self.timeout,
        )
        r.raise_for_status()
        text = r.text
        cut = re.search(r"<!DOCTYPE|<html", text, re.I)
        if cut:
            text = text[:cut.start()]
        data = json.loads(text)
        games = data.get("game", [])
        self._games[date8] = games
        self._games_cached_at[date8] = time.monotonic()
        return games

    def _match_game(self, home, away, commence_iso):
        """Match by date + teams, then disambiguate same-day games by KST start time.

        `_event_reversed` records the rare case where an odds feed reverses the
        official KBO home/away orientation, so downstream starter/lineup fields
        can be remapped instead of silently attaching them to the wrong team.
        """
        event_dt = _kst_dt(commence_iso)
        date8 = event_dt.strftime("%Y%m%d")
        target_min = event_dt.hour * 60 + event_dt.minute
        games = self._game_list(date8)

        def candidates(reverse=False):
            out=[]
            for g in games:
                gh = KBO_EN_BY_KR.get(str(g.get("HOME_NM", "")).strip(), str(g.get("HOME_NM", "")))
                ga = KBO_EN_BY_KR.get(str(g.get("AWAY_NM", "")).strip(), str(g.get("AWAY_NM", "")))
                ok = (_same_team(gh, away) and _same_team(ga, home)) if reverse else (_same_team(gh, home) and _same_team(ga, away))
                if ok:
                    out.append(g)
            return out

        for reverse in (False, True):
            hits = candidates(reverse)
            if not hits:
                continue
            if len(hits) > 1:
                timed=[(_clock_minutes(g.get("G_TM")), g) for g in hits]
                valid=[(abs(m-target_min), g) for m,g in timed if m is not None]
                if valid:
                    valid.sort(key=lambda x:x[0])
                    chosen=valid[0][1]
                else:
                    chosen=hits[0]
            else:
                chosen=hits[0]
            chosen=dict(chosen)
            chosen["_event_reversed"] = bool(reverse)
            return chosen
        return None

    @staticmethod
    def _naver_headers(referer="https://m.sports.naver.com"):
        return {
            "User-Agent": UA,
            "Referer": referer,
            "Origin": "https://m.sports.naver.com",
            "Accept": "application/json, text/plain, */*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    @staticmethod
    def _walk_json(obj, path=""):
        """Yield (path, value) recursively for provider-shape tolerant parsing."""
        yield path, obj
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else str(k)
                yield from KBOOfficialLive._walk_json(v, p)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                p = f"{path}[{i}]"
                yield from KBOOfficialLive._walk_json(v, p)

    def _naver_calendar(self, date_obj):
        """Legacy Naver calendar endpoint kept as a fail-soft backup."""
        key = date_obj.strftime("%Y-%m-%d")
        if key in self._naver_calendar_cache:
            return self._naver_calendar_cache[key]
        r = self.s.get(
            NAVER_KBO_CALENDAR,
            params={
                "upperCategoryId": "kbaseball",
                "categoryIds": ",kbo,kbaseballetc,premier12,apbc",
                "date": key,
            },
            headers=self._naver_headers(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (None, 200) or data.get("success") is False:
            raise RuntimeError(f"Naver calendar error: {data.get('message')}")
        self._naver_calendar_cache[key] = data
        return data

    @staticmethod
    def _collect_game_infos(payload):
        """Find schedule game dictionaries across both old and new Naver shapes."""
        out = []
        seen = set()
        for _, value in KBOOfficialLive._walk_json(payload):
            if not isinstance(value, dict):
                continue
            gid = value.get("gameId") or value.get("game_id") or value.get("id")
            if gid is None:
                continue
            # Avoid player/team dictionaries that happen to have an id field.
            keys = {str(k).lower() for k in value.keys()}
            looks_game = (
                "gameid" in keys or "game_id" in keys or
                any(x in keys for x in ("hometeamname", "awayteamname", "gametime", "starttime", "statuscode"))
            )
            if not looks_game:
                continue
            sgid = str(gid).strip()
            if not sgid or sgid in seen:
                continue
            seen.add(sgid)
            out.append(value)
        return out

    def _naver_games(self, date_obj):
        """Modern Naver schedule endpoint; fromDate/toDate replaced old single date flow."""
        key = date_obj.strftime("%Y-%m-%d")
        if key in self._naver_games_cache:
            return self._naver_games_cache[key]
        data = {}
        try:
            r = self.s.get(
                NAVER_KBO_GAMES,
                params={"upperCategoryId": "kbaseball", "fromDate": key, "toDate": key},
                headers=self._naver_headers(), timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("success") is False:
                data = {}
        except Exception:
            data = {}
        games = self._collect_game_infos(data)
        self._naver_games_cache[key] = games
        return games

    def _naver_schedule_candidates(self, date_obj):
        """Combine the modern schedule feed and legacy calendar without duplicate gameIds."""
        out = []
        seen = set()
        for info in self._naver_games(date_obj):
            gid = str(info.get("gameId") or info.get("game_id") or info.get("id") or "").strip()
            if gid and gid not in seen:
                seen.add(gid); out.append(info)
        try:
            data = self._naver_calendar(date_obj)
        except Exception:
            data = {}
        for info in self._collect_game_infos(data):
            gid = str(info.get("gameId") or info.get("game_id") or info.get("id") or "").strip()
            if gid and gid not in seen:
                seen.add(gid); out.append(info)
        return out

    def _naver_preview(self, game_id):
        gid = str(game_id or "").strip()
        if not gid:
            return {}
        cached_at=self._naver_preview_cached_at.get(gid,0.0)
        if gid in self._naver_preview_cache and (time.monotonic()-cached_at) < self.live_cache_ttl:
            return self._naver_preview_cache[gid]
        r = self.s.get(
            NAVER_KBO_PREVIEW.format(game_id=gid),
            headers=self._naver_headers(f"https://m.sports.naver.com/game/{gid}/lineup"),
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (None, 200) or data.get("success") is False:
            return {}
        self._naver_preview_cache[gid] = data
        self._naver_preview_cached_at[gid] = time.monotonic()
        return data

    def _naver_aux(self, game_id, kind):
        gid = str(game_id or "").strip()
        if not gid:
            return {}
        key = (gid, kind)
        cached_at=self._naver_aux_cached_at.get(key,0.0)
        if key in self._naver_aux_cache and (time.monotonic()-cached_at) < self.live_cache_ttl:
            return self._naver_aux_cache[key]
        url = (NAVER_KBO_RELAY if kind == "relay" else NAVER_KBO_POLLING).format(game_id=gid)
        try:
            r = self.s.get(
                url,
                headers=self._naver_headers(f"https://m.sports.naver.com/game/{gid}/relay"),
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("success") is False:
                data = {}
        except Exception:
            data = {}
        self._naver_aux_cache[key] = data
        self._naver_aux_cached_at[key] = time.monotonic()
        return data

    @staticmethod
    def _naver_player_id(player):
        if not isinstance(player, dict):
            return None
        for key in ("playerId", "playerCode", "pcode", "playerNo", "playerNumber", "id"):
            v = player.get(key)
            if v is not None and str(v).strip():
                return str(v).strip()
        info = player.get("playerInfo") or {}
        if isinstance(info, dict):
            for key in ("playerId", "playerCode", "pcode", "id"):
                v = info.get(key)
                if v is not None and str(v).strip():
                    return str(v).strip()
        return None

    @classmethod
    def _parse_naver_players(cls, players):
        if not isinstance(players, list):
            return []
        out = []
        for player in players:
            if not isinstance(player, dict):
                continue
            order_raw = None
            for key in ("batorder", "batOrder", "battingOrder", "battingOrderNo", "batting_order", "order"):
                if player.get(key) is not None:
                    order_raw = player.get(key); break
            if order_raw is None:
                info = player.get("playerInfo") or {}
                if isinstance(info, dict):
                    for key in ("batorder", "batOrder", "battingOrder", "order"):
                        if info.get(key) is not None:
                            order_raw = info.get(key); break
            try:
                order = int(str(order_raw).strip())
            except Exception:
                continue
            if not (1 <= order <= 9):
                continue
            info = player.get("playerInfo") or {}
            if not isinstance(info, dict):
                info = {}
            name = _clean(
                player.get("playerName") or player.get("name") or player.get("pName") or
                info.get("name") or info.get("playerName")
            )
            if not name:
                continue
            out.append({
                "order": order,
                "position": _clean(player.get("positionName") or player.get("position") or player.get("pos") or info.get("positionName")),
                "name": name,
                "player_id": cls._naver_player_id(player),
                "war": None,
                "bats_throws": _clean(player.get("batsThrows") or info.get("batsThrows")),
                "backnum": _clean(player.get("backnum") or player.get("backNumber") or info.get("backnum")),
            })
        by_order = {p["order"]: p for p in out}
        return [by_order[i] for i in range(1, 10) if i in by_order]

    @classmethod
    def _parse_naver_side(cls, payload, key):
        """Backwards-compatible direct parser plus recursive aliases."""
        aliases = {
            "homeTeamLineUp": ("homeTeamLineUp", "homeTeamLineup", "homeLineUp", "homeLineup", "homeStartingLineup"),
            "awayTeamLineUp": ("awayTeamLineUp", "awayTeamLineup", "awayLineUp", "awayLineup", "awayStartingLineup"),
        }.get(key, (key,))
        if isinstance(payload, dict):
            for alias in aliases:
                block = payload.get(alias)
                if isinstance(block, dict):
                    for list_key in ("fullLineUp", "fullLineup", "lineUp", "lineup", "players", "startingPlayers"):
                        got = cls._parse_naver_players(block.get(list_key))
                        if len(got) >= 9:
                            return got
                got = cls._parse_naver_players(block)
                if len(got) >= 9:
                    return got
        side = "home" if key.lower().startswith("home") else "away"
        best = []
        for path, value in cls._walk_json(payload):
            if side not in path.lower():
                continue
            got = cls._parse_naver_players(value)
            if len(got) > len(best):
                best = got
        return best

    @classmethod
    def _extract_naver_lineups(cls, payload):
        home = cls._parse_naver_side(payload, "homeTeamLineUp")
        away = cls._parse_naver_side(payload, "awayTeamLineUp")
        if len(home) == 9 and len(away) == 9:
            return home, away
        # Some response shapes put both sides in unnamed lineup blocks. Infer side
        # only from the JSON path, never by list order alone.
        home_best, away_best = home, away
        for path, value in cls._walk_json(payload):
            got = cls._parse_naver_players(value)
            if not got:
                continue
            low = path.lower()
            if "home" in low and len(got) > len(home_best):
                home_best = got
            if "away" in low and len(got) > len(away_best):
                away_best = got
        return home_best, away_best

    @staticmethod
    def _naver_name_from_info(info, side):
        if not isinstance(info, dict):
            return ""
        pref = "home" if side == "home" else "away"
        short = "h" if side == "home" else "a"
        for key in (
            f"{pref}TeamName", f"{pref}TeamFullName", f"{pref}Name", f"{short}Name", f"{short}FullName",
            f"{pref}TeamCode", f"{short}Code",
        ):
            v = info.get(key)
            if isinstance(v, str) and _clean(v):
                return _clean(v)
        block = info.get(f"{pref}Team") or info.get(pref)
        if isinstance(block, dict):
            for key in ("name", "teamName", "fullName", "shortName", "code", "teamCode"):
                v = block.get(key)
                if v is not None and _clean(v):
                    return _clean(v)
        if isinstance(block, str):
            return _clean(block)
        return ""

    @classmethod
    def _naver_game_info(cls, payload):
        # Prefer explicit gameInfo dictionaries, otherwise the schedule item itself.
        if isinstance(payload, dict):
            gi = payload.get("gameInfo")
            if isinstance(gi, dict):
                return gi
        for path, value in cls._walk_json(payload):
            if path.lower().endswith("gameinfo") and isinstance(value, dict):
                return value
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _payload_mentions_team(cls, payload, team):
        for path, value in cls._walk_json(payload):
            if isinstance(value, str) and len(value) <= 50 and _same_team(value, team):
                return True
        return False

    @classmethod
    def _extract_naver_starter(cls, payload, side):
        aliases = ("homeStarter", "homeStartingPitcher") if side == "home" else ("awayStarter", "awayStartingPitcher")
        if isinstance(payload, dict):
            for alias in aliases:
                block = payload.get(alias)
                if isinstance(block, dict):
                    info = block.get("playerInfo") or block
                    if isinstance(info, dict):
                        name = _clean(info.get("name") or info.get("playerName"))
                        if name:
                            return name
        side_l = side.lower()
        for path, value in cls._walk_json(payload):
            low = path.lower()
            if side_l not in low or not any(tok in low for tok in ("starter", "startingpitcher", "pitcher")):
                continue
            if isinstance(value, dict):
                info = value.get("playerInfo") or value
                if isinstance(info, dict):
                    name = _clean(info.get("name") or info.get("playerName"))
                    if name:
                        return name
        return None

    def _naver_lineup(self, home, away, commence_iso):
        event_dt = _kst_dt(commence_iso)
        game_infos = self._naver_schedule_candidates(event_dt.date())
        if not game_infos:
            return {"confirmed": False, "home": [], "away": [], "reason": "Naver schedule has no KBO games"}

        target_min = event_dt.hour * 60 + event_dt.minute
        matches = []
        errors = []
        for info in game_infos:
            gid = str(info.get("gameId") or info.get("game_id") or info.get("id") or "").strip()
            if not gid:
                continue
            try:
                raw = self._naver_preview(gid)
            except Exception as e:
                raw = {}
                errors.append(f"{gid}:preview:{type(e).__name__}")
            preview = ((raw.get("result") or {}).get("previewData") or {}) if isinstance(raw, dict) else {}
            if not preview:
                preview = raw if isinstance(raw, dict) else {}
            gi = self._naver_game_info(preview)
            nh = self._naver_name_from_info(gi, "home") or self._naver_name_from_info(info, "home")
            na = self._naver_name_from_info(gi, "away") or self._naver_name_from_info(info, "away")
            reverse = None
            if nh and na:
                if _same_team(nh, home) and _same_team(na, away):
                    reverse = False
                elif _same_team(nh, away) and _same_team(na, home):
                    reverse = True
            if reverse is None:
                # Provider fields changed several times in 2026. As a last-safe
                # matcher, require both requested teams to appear in the payload.
                h_hit = self._payload_mentions_team(preview, home) or self._payload_mentions_team(info, home)
                a_hit = self._payload_mentions_team(preview, away) or self._payload_mentions_team(info, away)
                if not (h_hit and a_hit):
                    continue
                # Orientation is unknown; use known names if one side can be recovered.
                if nh and _same_team(nh, away):
                    reverse = True
                elif na and _same_team(na, home):
                    reverse = True
                else:
                    reverse = False
            gm = _clock_minutes(
                gi.get("gtime") or gi.get("gameTime") or gi.get("startTime") or
                info.get("gameTime") or info.get("startTime") or info.get("gtime")
            )
            delta = abs(gm - target_min) if gm is not None else 9999
            matches.append((delta, gid, preview, bool(reverse)))
        if not matches:
            reason = "Naver team/date match unavailable"
            if errors:
                reason += " (" + ", ".join(errors[:3]) + ")"
            return {"confirmed": False, "home": [], "away": [], "reason": reason}
        matches.sort(key=lambda x: (x[0], x[1]))
        _, gid, preview, reverse = matches[0]

        sources = [("preview", preview)]
        # If preview is lagging behind the visible Naver page, relay/game-polling
        # often already contains the published batting order. Merge side-by-side.
        for kind in ("relay", "polling"):
            aux = self._naver_aux(gid, kind)
            if aux:
                sources.append((kind, aux))

        n_home, n_away = [], []
        used = []
        for label, payload in sources:
            h, a = self._extract_naver_lineups(payload)
            if len(h) > len(n_home):
                n_home = h
                if h: used.append(f"{label}:home{len(h)}")
            if len(a) > len(n_away):
                n_away = a
                if a: used.append(f"{label}:away{len(a)}")
            if len(n_home) == 9 and len(n_away) == 9:
                break

        home_lu, away_lu = (n_away, n_home) if reverse else (n_home, n_away)
        confirmed = len(home_lu) == 9 and len(away_lu) == 9
        home_starter = self._extract_naver_starter(preview, "home")
        away_starter = self._extract_naver_starter(preview, "away")
        if reverse:
            home_starter, away_starter = away_starter, home_starter
        source_detail = "+".join(dict.fromkeys(x.split(":")[0] for x in used)) or "preview"
        return {
            "confirmed": confirmed,
            "home": home_lu,
            "away": away_lu,
            "home_war": None,
            "away_war": None,
            "home_starter": home_starter,
            "away_starter": away_starter,
            "source_game_id": gid,
            "source": f"Naver Sports public {source_detail} fallback",
            "label": "확정 (네이버스포츠 fallback)" if confirmed else "부분수집 (네이버스포츠)",
            "kind": "starting" if confirmed else "partial",
            "fallback_used": True,
            "reason": "" if confirmed else f"Naver batting orders {len(away_lu)}/9 away, {len(home_lu)}/9 home; sources={source_detail}",
        }

    def _lineup(self, game_id, season):
        """Fetch KBO official lineup using the game's actual series id first.

        KBO's GameCenter endpoint can return an empty lineup when srId is forced to
        regular-season 0 even though the game-list row uses a different series id.
        Keep the public signature stable and recover srId from the already fetched
        daily game-list cache, then fail-soft through known KBO series ids.
        """
        game_id = str(game_id)
        sr_hint = None
        for games in self._games.values():
            for g in games or []:
                if str((g or {}).get("G_ID") or "") == game_id:
                    v = (g or {}).get("SR_ID")
                    if v is not None and str(v).strip():
                        sr_hint = str(v).strip()
                    break
            if sr_hint is not None:
                break
        series_ids = []
        for v in (sr_hint, "0", "9", "6", "1", "3", "4", "5", "7", "8"):
            if v is not None and str(v) not in series_ids:
                series_ids.append(str(v))

        def parse(data, used_sr):
            try:
                confirmed = _flag_true((data.get("0") or [{}])[0].get("LINEUP_CK"))
            except Exception:
                confirmed = False

            def rows(key):
                raw = (data.get(key) or [None])[0]
                out = []
                for rr, ids in _parse_make_table_rich(raw):
                    if len(rr) < 3:
                        continue
                    try:
                        order = int(rr[0])
                    except Exception:
                        continue
                    if 1 <= order <= 9:
                        pid = ids[2] if len(ids) > 2 else None
                        out.append({"order": order, "position": rr[1], "name": rr[2],
                                    "player_id": pid,
                                    "war": _num(rr[3]) if len(rr) > 3 else None})
                by_order = {x["order"]: x for x in out}
                return [by_order[i] for i in range(1, 10) if i in by_order]

            def war(key):
                m = (data.get(key) or [{}])[0]
                vals = [_num(m.get("HITTER_12_WAR_RT")), _num(m.get("HITTER_35_WAR_RT")), _num(m.get("HITTER_69_WAR_RT"))]
                vals = [v for v in vals if v is not None]
                return sum(vals) if vals else None

            home_meta = (data.get("1") or [{}])[0] or {}
            away_meta = (data.get("2") or [{}])[0] or {}
            home_rows, away_rows = rows("3"), rows("4")
            return {
                "confirmed": confirmed,
                "home": home_rows, "away": away_rows,
                "home_war": war("1"), "away_war": war("2"),
                "home_team": _clean(home_meta.get("T_NM", "")),
                "away_team": _clean(away_meta.get("T_NM", "")),
                "source_game_id": str(home_meta.get("G_ID") or away_meta.get("G_ID") or ""),
                "series_id": used_sr,
            }

        best = None
        best_score = -1
        last_error = None
        for sr in series_ids:
            try:
                r = self.s.post(
                    f"{KBO_SCHEDULE_BASE}/GetLineUpAnalysis",
                    data={"leId": "1", "srId": sr, "seasonId": str(season), "gameId": game_id},
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": KBO_REFERER,
                        "User-Agent": UA,
                    },
                    timeout=self.timeout,
                )
                r.raise_for_status()
                out = parse(r.json(), sr)
            except Exception as e:
                last_error = e
                continue
            # Reject an explicitly different game id before ranking the response.
            source_gid = str(out.get("source_game_id") or "")
            if source_gid and source_gid != game_id:
                score = -1
            else:
                complete = len(out.get("home") or []) == 9 and len(out.get("away") or []) == 9
                score = (100 if out.get("confirmed") and complete else 50 if complete else 0) + len(out.get("home") or []) + len(out.get("away") or [])
            if score > best_score:
                best_score, best = score, out
            if score >= 118:  # confirmed + full 9x9
                return out
        if best is not None:
            return best
        if last_error is not None:
            raise last_error
        return {"confirmed": False, "home": [], "away": [], "source_game_id": game_id, "series_id": sr_hint}

    def _pitcher_table(self):
        if self._pitchers is not None:
            return self._pitchers
        out = {}
        try:
            r = self.s.get(KBO_PITCHER_STATS, timeout=self.timeout)
            r.raise_for_status()
            tables = pd.read_html(StringIO(r.text))
            df = next((d for d in tables if "선수명" in [str(c) for c in d.columns] and "ERA" in [str(c) for c in d.columns]), None)
            if df is not None:
                for _, rr in df.iterrows():
                    name = _clean(rr.get("선수명", ""))
                    if not name:
                        continue
                    so, bb = _num(rr.get("SO")), _num(rr.get("BB"))
                    out[_norm(name)] = {
                        "era": _num(rr.get("ERA")), "whip": _num(rr.get("WHIP")),
                        "strikeouts": so, "walks": bb,
                        "kbb": so / bb if so is not None and bb not in (None, 0) else None,
                        "ip": _clean(rr.get("IP", "")),
                    }
        except Exception:
            out = {}
        self._pitchers = out
        return out

    def context(self, home, away, commence_iso):
        try:
            g = self._match_game(home, away, commence_iso)
        except Exception as e:
            return empty_context("KBO", f"GameCenter 조회 실패: {e}")
        if not g:
            return empty_context("KBO", "오늘 KBO 경기와 Odds 경기명 매칭 실패")

        game_id = str(g.get("G_ID", ""))
        season = int(g.get("SEASON_ID") or (game_id[:4] if len(game_id) >= 4 else _kst_dt(commence_iso).year))
        reversed_event = bool(g.get("_event_reversed"))
        official_home_sp = _clean(g.get("B_PIT_P_NM", "")) or None
        official_away_sp = _clean(g.get("T_PIT_P_NM", "")) or None
        home_sp, away_sp = (official_away_sp, official_home_sp) if reversed_event else (official_home_sp, official_away_sp)
        starter_ok = _flag_true(g.get("START_PIT_CK")) and bool(home_sp and away_sp)

        lineup = {"confirmed": False, "home": [], "away": [], "home_war": None, "away_war": None,
                  "source": "KBO official GameCenter", "fallback_used": False}
        official_lineup_error = None
        try:
            lineup = self._lineup(game_id, season)
            lineup["source"] = "KBO official GameCenter"
            lineup["fallback_used"] = False
        except Exception as e:
            official_lineup_error = f"{type(e).__name__}: {e}"
        # Guard against a stale/wrong lineup response before accepting it as FINAL.
        source_gid=str(lineup.get("source_game_id") or "")
        if source_gid and game_id and source_gid != game_id:
            lineup["confirmed"] = False
        if reversed_event:
            lineup = dict(lineup)
            lineup["home"], lineup["away"] = lineup.get("away", []), lineup.get("home", [])
            lineup["home_war"], lineup["away_war"] = lineup.get("away_war"), lineup.get("home_war")
            lineup["home_team"], lineup["away_team"] = lineup.get("away_team"), lineup.get("home_team")
        lineup_ok = bool(lineup.get("confirmed")) and len(lineup.get("home", [])) >= 9 and len(lineup.get("away", [])) >= 9

        # KBO GameCenter occasionally publishes/serves the starter list before its
        # lineup-analysis payload is available.  Naver Sports exposes the same
        # pregame batting order through its public preview endpoint, so use it as
        # a fail-soft fallback.  It is promoted only when both batting orders 1-9
        # are complete; partial rows remain unconfirmed.
        if not lineup_ok:
            try:
                nav = self._naver_lineup(home, away, commence_iso)
            except Exception as e:
                nav = {"confirmed": False, "reason": f"Naver fallback failed: {type(e).__name__}: {e}"}
            nav_ok = bool(nav.get("confirmed")) and len(nav.get("home", [])) == 9 and len(nav.get("away", [])) == 9
            if nav_ok:
                lineup = nav
                lineup_ok = True
                if not home_sp and nav.get("home_starter"):
                    home_sp = nav.get("home_starter")
                if not away_sp and nav.get("away_starter"):
                    away_sp = nav.get("away_starter")
                starter_ok = bool(home_sp and away_sp)
            else:
                lineup["fallback_reason"] = nav.get("reason")
                if official_lineup_error:
                    lineup["official_error"] = official_lineup_error

        pmap = self._pitcher_table()
        home_stat = pmap.get(_norm(home_sp), {}) if home_sp else {}
        away_stat = pmap.get(_norm(away_sp), {}) if away_sp else {}

        stage = "FINAL" if starter_ok and lineup_ok else "LINEUP CONFIRMED" if lineup_ok else "STARTER CONFIRMED" if starter_ok else "PRE-LINEUP"
        return {
            "league": "KBO", "stage": stage,
            "starter_confirmed": starter_ok, "lineup_confirmed": lineup_ok,
            "home_starter": home_sp, "away_starter": away_sp,
            "home_starter_id": _starter_player_id(g, not reversed_event),
            "away_starter_id": _starter_player_id(g, reversed_event),
            "home_starter_stats": home_stat, "away_starter_stats": away_stat,
            "home_lineup": lineup.get("home", []), "away_lineup": lineup.get("away", []),
            "home_lineup_strength": lineup.get("home_war"), "away_lineup_strength": lineup.get("away_war"),
            "lineup_label": lineup.get("label") or ("확정 (KBO 공식)" if lineup_ok else "원본 미수집"),
            "lineup_kind": lineup.get("kind") or ("starting" if lineup_ok else None),
            "lineup_source": lineup.get("source") or "KBO official GameCenter",
            "lineup_fallback_used": bool(lineup.get("fallback_used")),
            "source": "KBO official GameCenter" + (" + Naver Sports lineup fallback" if lineup.get("fallback_used") else ""),
            "note": "; ".join(x for x in [
                "odds home/away reversed; official fields remapped" if reversed_event else "",
                "KBO official lineup unavailable; Naver Sports public preview used" if lineup.get("fallback_used") else "",
                str(lineup.get("fallback_reason") or ""),
            ] if x),
            "game_id": game_id, "game_url": None,
        }


class NPBOfficialLive:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
        self._pages = {}
        self._pages_cached_at = {}
        self._bat = {}
        self._pit = {}
        self.live_cache_ttl = 45.0

    def _get(self, url):
        cached_at=self._pages_cached_at.get(url,0.0)
        if url in self._pages and (time.monotonic()-cached_at) < self.live_cache_ttl:
            return self._pages[url]
        r = self.s.get(url, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = "utf-8"
        self._pages[url] = r.text
        self._pages_cached_at[url] = time.monotonic()
        return self._pages[url]

    def _starter_map(self, game_date=None):
        soup = BeautifulSoup(self._get(NPB_STARTERS), "html.parser")
        out = {}
        heading_tag = soup.find(string=re.compile(r"\d+月\d+日の予告先発投手"))
        if game_date:
            # The live page often lists tomorrow's pitchers while today's games
            # remain in the site navigation. Do not borrow another day's names.
            if not heading_tag or not re.search(
                rf"0?{game_date.month}月0?{game_date.day}日の予告先発投手", str(heading_tag)
            ):
                return out
        if not heading_tag:
            return out
        heading = heading_tag.find_parent(re.compile(r"^h[1-6]$"))
        if not heading:
            return out
        section = []
        # Do not stop at league sub-headings (e.g. セ・リーグ / パ・リーグ).
        # The official announced-starter page can place both leagues under one
        # date heading with h3/h4 separators. Older code stopped at the first
        # sub-heading, which could leave Pacific League starters missing while
        # Central League starters were collected successfully.
        for node in heading.next_elements:
            name = getattr(node, "name", None)
            if name == "footer":
                break
            if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                label = _clean(node.get_text(" ", strip=True))
                if node is not heading and re.search(r"\d+月\d+日の予告先発投手", label):
                    break
            section.append(node)

        # Primary DOM path: official page uses a team-logo image followed by a pitcher link.
        for img in (node for node in section if getattr(node, "name", None) == "img"):
            alt = _clean(img.get("alt", ""))
            team = NPB_FULL_MAP.get(alt)
            if not team:
                continue
            for node in img.next_elements:
                if getattr(node, "name", None) in {"footer", "h1", "h2", "h3", "h4"}:
                    break
                if getattr(node, "name", None) == "img" and NPB_FULL_MAP.get(_clean(node.get("alt", ""))):
                    break  # Next team's card; never assign its pitcher here.
                if getattr(node, "name", None) != "a":
                    continue
                cand = _clean(node.get_text(" ", strip=True))
                pid = re.search(r"/players/(\d+)\.html", str(node.get("href", "")))
                if pid and re.search(r"[\wぁ-んァ-ヶ一-龯]", cand) and cand not in NPB_FULL_MAP:
                    out[team] = {"name": cand, "player_id": pid.group(1)}
                    break
        return out

    def _english_player_name(self, player_id):
        if not player_id:return None
        try:
            html=self._get(f"https://npb.jp/bis/eng/players/{player_id}.html")
            title=BeautifulSoup(html,"html.parser").title
            name=_clean(str(title.string).split("（")[0].split("|")[0]) if title and title.string else ""
            return name if re.search(r"[a-z]",name,re.I) else None
        except Exception:return None

    def _player_from_stats(self, team, name, year):
        code=NPB_TEAM_CODE.get(team)
        if not code or not name:return None
        try:
            soup=BeautifulSoup(self._get(NPB_PITCHING.format(year=year,code=code)),"html.parser")
            needle=_norm(name)
            matches={}
            for a in soup.find_all("a",href=True):
                pid=re.search(r"/players/(\d+)\.html",a["href"])
                full=_clean(a.get_text(" ",strip=True)); key=_norm(full)
                if pid and needle and (key==needle or key.startswith(needle)):
                    matches[pid.group(1)]={"name":full,"player_id":pid.group(1)}
            return next(iter(matches.values())) if len(matches)==1 else None
        except Exception:return None

    def _schedule_starters(self, game_date, home, away):
        """Recover today's announced pitchers after the announcement page rolls over."""
        url=f"https://npb.jp/games/{game_date.year}/schedule_{game_date.month:02d}_detail.html"
        soup=BeautifulSoup(self._get(url),"html.parser")
        aliases={club:[jp for jp,en in NPB_NAME_MAP.items() if en==club] for club in (home,away)}
        target=False
        for row in soup.find_all("tr"):
            label=row.get_text(" ",strip=True)
            date_match=re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})\s*[（(]",label)
            if date_match:
                target=(int(date_match.group(1)),int(date_match.group(2)))==(game_date.month,game_date.day)
            if not target:continue
            hpos=min((label.find(jp) for jp in aliases[home] if jp in label),default=-1)
            apos=min((label.find(jp) for jp in aliases[away] if jp in label),default=-1)
            if hpos<0 or apos<0 or hpos>=apos:continue
            names=re.findall(r"先発\s*[:：]\s*([^\s　]+)",label)
            if len(names)!=2:return {}
            resolved={}
            for club,short_name in zip((home,away),names):
                # The monthly NPB schedule itself is an official source.  Do not
                # discard an announced starter merely because the player-profile
                # resolver cannot produce a unique player id (common with short
                # surnames / newly added players).  Keep the official name and
                # treat player_id resolution as enrichment only.
                entry=self._player_from_stats(club,short_name,game_date.year)
                resolved[club]=entry or {"name": short_name, "player_id": None,
                                         "source": "NPB monthly schedule official starter"}
            return resolved
        return {}

    def _score_page(self, home, away, commence_iso):
        dt = _kst_dt(commence_iso)
        index = BeautifulSoup(self._get(NPB_GAMES.format(year=dt.year)), "html.parser")
        mmdd = dt.strftime("%m%d")
        urls = []
        for a in index.find_all("a", href=True):
            href = a["href"]
            if f"/scores/{dt.year}/{mmdd}/" in href:
                u = urljoin("https://npb.jp", href)
                if u not in urls:
                    urls.append(u)
        for u in urls:
            try:
                text = BeautifulSoup(self._get(u), "html.parser").get_text(" ", strip=True)
            except Exception:
                continue
            home_hit = any(jp in text for jp,en in NPB_FULL_MAP.items() if _same_team(en, home))
            away_hit = any(jp in text for jp,en in NPB_FULL_MAP.items() if _same_team(en, away))
            if home_hit and away_hit:
                return u
        return None

    def _lineups(self, url):
        if not url:
            return {"confirmed": False, "home": [], "away": []}
        html = self._get(url)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        if not ("先発メンバー" in text or "最新のオーダー" in text):
            return {"confirmed": False, "home": [], "away": []}

        # Candidate tables containing batting-order rows 1..9.
        candidates = []
        try:
            tables = pd.read_html(StringIO(html))
        except Exception:
            tables = []
        for df in tables:
            if len(df) < 9 or df.shape[1] < 3:
                continue
            rows = []
            for _, rr in df.iterrows():
                vals = [_clean(v) for v in rr.tolist()]
                try:
                    order = int(float(vals[0]))
                except Exception:
                    continue
                if 1 <= order <= 9:
                    rows.append({"order": order, "position": vals[1], "name": vals[2]})
            if len(rows) >= 9:
                candidates.append(sorted(rows[:9], key=lambda x:x["order"]))
        if len(candidates) < 2:
            return {"confirmed": False, "home": [], "away": []}

        # NPB score pages conventionally show visitor order first and home order second.
        away, home = candidates[0], candidates[1]
        # Do not label a completed game's latest substituted order as confirmed pregame lineup.
        pregame = "試合開始前" in text or "試合前" in text
        confirmed = all({p["order"] for p in order} == set(range(1,10)) for order in (home,away))
        return {"confirmed": confirmed, "home": home, "away": away,
                "kind": "starting" if pregame else "current",
                "label": "확정(타순 1~9)" if pregame else "확인(경기중·최신 타순)"}

    def _batting(self, team, year):
        key=(team,year)
        if key in self._bat:
            return self._bat[key]
        code=NPB_TEAM_CODE.get(team)
        if not code:
            self._bat[key]=({},None); return self._bat[key]
        try:
            tables=pd.read_html(StringIO(self._get(NPB_BATTING.format(year=year,code=code))))
        except Exception:
            self._bat[key]=({},None); return self._bat[key]
        df=next((d for d in tables if {"選手","打席","長打率","出塁率"}.issubset(set(map(str,d.columns)))),None)
        out={}; weighted=[]
        if df is not None:
            for _,rr in df.iterrows():
                name=re.sub(r"^[*+]+","",_clean(rr.get("選手","")))
                pa=_num(rr.get("打席")); slg=_num(rr.get("長打率")); obp=_num(rr.get("出塁率"))
                if not name: continue
                ops=slg+obp if slg is not None and obp is not None else None
                out[_norm(name)]={"ops":ops,"pa":pa}
                if pa and pa>=30 and ops and ops>0: weighted.append((ops,pa))
        baseline=sum(v*w for v,w in weighted)/sum(w for _,w in weighted) if weighted else None
        self._bat[key]=(out,baseline); return self._bat[key]

    def _pitching(self, team, year):
        key=(team,year)
        if key in self._pit: return self._pit[key]
        code=NPB_TEAM_CODE.get(team)
        if not code: self._pit[key]={}; return {}
        try:
            tables=pd.read_html(StringIO(self._get(NPB_PITCHING.format(year=year,code=code))))
        except Exception:
            self._pit[key]={}; return {}
        df=next((d for d in tables if {"選手","防御率","投球回"}.issubset(set(map(str,d.columns)))),None)
        out={}
        if df is not None:
            for _,rr in df.iterrows():
                name=re.sub(r"^[*+]+","",_clean(rr.get("選手","")))
                if not name: continue
                ip=_npb_ip(rr.get("投球回")); h=_num(rr.get("安打")); bb=_num(rr.get("四球")); so=_num(rr.get("三振"))
                out[_norm(name)]={
                    "era":_num(rr.get("防御率")),
                    "whip":((h or 0)+(bb or 0))/ip if ip and h is not None and bb is not None else None,
                    "strikeouts":so,"walks":bb,"kbb":so/bb if so is not None and bb not in (None,0) else None,"ip":ip,
                }
        self._pit[key]=out; return out

    @staticmethod
    def _fuzzy(table,name):
        if not name: return None
        n=_norm(name)
        if n in table: return table[n]
        hits=[(k,v) for k,v in table.items() if n in k or k in n]
        if not hits:return None
        hits.sort(key=lambda kv:abs(len(kv[0])-len(n)))
        return hits[0][1]

    def _lineup_factor(self,team,lineup,year):
        table,baseline=self._batting(team,year)
        if not baseline or not lineup:return None
        weights=[1.04,1.03,1.07,1.10,1.06,1.00,.97,.94,.92]
        vals=[]
        for i,p in enumerate(sorted(lineup,key=lambda x:x.get("order",99))[:9]):
            st=self._fuzzy(table,p.get("name"))
            if st and st.get("ops") is not None: vals.append((float(st["ops"]),weights[min(i,8)]))
        if len(vals)<6:return None
        ops=sum(v*w for v,w in vals)/sum(w for _,w in vals)
        return max(.90,min(1.10,ops/baseline))

    def context(self,home,away,commence_iso):
        home=canonical_english(home); away=canonical_english(away); year=_kst_dt(commence_iso).year
        try: starters=self._starter_map(_kst_dt(commence_iso).date())
        except Exception: starters={}
        if home not in starters or away not in starters:
            try:
                for team,entry in self._schedule_starters(_kst_dt(commence_iso).date(),home,away).items():
                    starters.setdefault(team,entry)
            except Exception:pass
        try: url=self._score_page(home,away,commence_iso)
        except Exception: url=None
        try: lu=self._lineups(url)
        except Exception: lu={"confirmed":False,"home":[],"away":[]}
        lineup_ok=bool(lu.get("confirmed")) and len(lu.get("home",[]))>=9 and len(lu.get("away",[]))>=9
        home_entry=starters.get(home) or {}; away_entry=starters.get(away) or {}
        for club,side in ((home,"home"),(away,"away")):
            if (home_entry if side=="home" else away_entry).get("name") or not lineup_ok:continue
            pitchers=[p.get("name") for p in lu.get(side,[])
                      if "投" in str(p.get("position", "")) and re.search(r"[\wぁ-んァ-ヶ一-龯]", str(p.get("name", "")))]
            if len(pitchers)==1:
                entry=self._player_from_stats(club,pitchers[0],year) or {"name":pitchers[0],"player_id":None}
                if side=="home":home_entry=entry
                else:away_entry=entry
        home_sp=home_entry.get("name"); away_sp=away_entry.get("name")
        starter_ok=bool(home_sp and away_sp)
        home_p=self._fuzzy(self._pitching(home,year),home_sp) or {}
        away_p=self._fuzzy(self._pitching(away,year),away_sp) or {}
        hf=self._lineup_factor(home,lu.get("home",[]),year) if lineup_ok else None
        af=self._lineup_factor(away,lu.get("away",[]),year) if lineup_ok else None
        stage="FINAL" if starter_ok and lineup_ok else "LINEUP CONFIRMED" if lineup_ok else "STARTER CONFIRMED" if starter_ok else "PRE-LINEUP"
        return {
            "league":"NPB","stage":stage,"starter_confirmed":starter_ok,"lineup_confirmed":lineup_ok,
            "home_starter":home_sp,"away_starter":away_sp,
            "home_starter_id":home_entry.get("player_id"),"away_starter_id":away_entry.get("player_id"),
            "home_starter_english":self._english_player_name(home_entry.get("player_id")),
            "away_starter_english":self._english_player_name(away_entry.get("player_id")),
            "home_starter_stats":home_p,"away_starter_stats":away_p,
            "home_lineup":lu.get("home",[]),"away_lineup":lu.get("away",[]),
            "lineup_label":lu.get("label") if lineup_ok else "원본 미수집",
            "lineup_kind":lu.get("kind"),
            "home_lineup_strength":hf,"away_lineup_strength":af,
            "source":"NPB.jp official","note":"","game_id":None,"game_url":url,
        }


class LiveBaseballContext:
    def __init__(self, timeout=20):
        self.kbo=KBOOfficialLive(timeout)
        self.npb=NPBOfficialLive(timeout)

    def context(self,league,home,away,commence_iso):
        lg=str(league).upper()
        try:
            if lg=="KBO":
                return self.kbo.context(home,away,commence_iso)
            if lg=="NPB":
                return self.npb.context(home,away,commence_iso)
            return empty_context(lg,"unsupported league")
        except Exception as e:
            return empty_context(lg,f"공식 선발/라인업 조회 실패: {type(e).__name__}: {e}")
