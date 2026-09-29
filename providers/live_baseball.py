from __future__ import annotations

from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import json
import re

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
NAVER_KBO_PREVIEW = "https://api-gw.sports.naver.com/schedule/games/{game_id}/preview"

PROVIDER_BUILD = "3.0.0"

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
    ca, cb = canonical_english(a), canonical_english(b)
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
        self._pitchers = None
        self._naver_calendar_cache = {}
        self._naver_preview_cache = {}

    def _game_list(self, date8):
        if date8 in self._games:
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

    def _naver_calendar(self, date_obj):
        key = date_obj.strftime("%Y-%m-%d")
        if key in self._naver_calendar_cache:
            return self._naver_calendar_cache[key]
        headers = {
            "User-Agent": UA,
            "Referer": "https://m.sports.naver.com",
            "Origin": "https://m.sports.naver.com",
            "Accept": "application/json, text/plain, */*",
        }
        r = self.s.get(
            NAVER_KBO_CALENDAR,
            params={
                "upperCategoryId": "kbaseball",
                "categoryIds": ",kbo,kbaseballetc,premier12,apbc",
                "date": key,
            },
            headers=headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200 or not data.get("success", False):
            raise RuntimeError(f"Naver calendar error: {data.get('message')}")
        self._naver_calendar_cache[key] = data
        return data

    def _naver_preview(self, game_id):
        gid = str(game_id or "").strip()
        if not gid:
            return {}
        if gid in self._naver_preview_cache:
            return self._naver_preview_cache[gid]
        headers = {
            "User-Agent": UA,
            "Referer": f"https://m.sports.naver.com/game/{gid}/lineup",
            "Origin": "https://m.sports.naver.com",
            "Accept": "application/json, text/plain, */*",
        }
        r = self.s.get(NAVER_KBO_PREVIEW.format(game_id=gid), headers=headers, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200 or not data.get("success", False):
            return {}
        self._naver_preview_cache[gid] = data
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
    def _parse_naver_side(cls, preview_data, key):
        block = preview_data.get(key) or {}
        players = block.get("fullLineUp") or []
        out = []
        for player in players:
            if not isinstance(player, dict) or "batorder" not in player:
                continue
            try:
                order = int(str(player.get("batorder")).strip())
            except Exception:
                continue
            if not (1 <= order <= 9):
                continue
            name = _clean(player.get("playerName") or (player.get("playerInfo") or {}).get("name"))
            if not name:
                continue
            out.append({
                "order": order,
                "position": _clean(player.get("positionName") or player.get("position")),
                "name": name,
                "player_id": cls._naver_player_id(player),
                "war": None,
                "bats_throws": _clean(player.get("batsThrows")),
                "backnum": _clean(player.get("backnum")),
            })
        # Do not promote duplicate/incomplete batting orders to confirmed.
        by_order = {p["order"]: p for p in out}
        return [by_order[i] for i in range(1, 10) if i in by_order]

    def _naver_lineup(self, home, away, commence_iso):
        event_dt = _kst_dt(commence_iso)
        data = self._naver_calendar(event_dt.date())
        date_key = event_dt.strftime("%Y-%m-%d")
        game_infos = []
        for day in ((data.get("result") or {}).get("dates") or []):
            if str(day.get("ymd")) == date_key:
                game_infos.extend(day.get("gameInfos") or [])
        if not game_infos:
            return {"confirmed": False, "home": [], "away": [], "reason": "Naver schedule has no KBO games"}

        target_min = event_dt.hour * 60 + event_dt.minute
        matches = []
        for info in game_infos:
            gid = info.get("gameId")
            if not gid:
                continue
            try:
                raw = self._naver_preview(gid)
            except Exception:
                continue
            preview = ((raw.get("result") or {}).get("previewData") or {})
            gi = preview.get("gameInfo") or {}
            nh = _clean(gi.get("hName") or gi.get("hFullName") or info.get("homeTeamName") or info.get("homeTeamCode"))
            na = _clean(gi.get("aName") or gi.get("aFullName") or info.get("awayTeamName") or info.get("awayTeamCode"))
            reverse = False
            if _same_team(nh, home) and _same_team(na, away):
                reverse = False
            elif _same_team(nh, away) and _same_team(na, home):
                reverse = True
            else:
                continue
            gm = _clock_minutes(gi.get("gtime") or info.get("gameTime") or info.get("startTime"))
            delta = abs(gm - target_min) if gm is not None else 9999
            matches.append((delta, str(gid), preview, reverse))
        if not matches:
            return {"confirmed": False, "home": [], "away": [], "reason": "Naver team/date match unavailable"}
        matches.sort(key=lambda x: (x[0], x[1]))
        _, gid, preview, reverse = matches[0]

        n_home = self._parse_naver_side(preview, "homeTeamLineUp")
        n_away = self._parse_naver_side(preview, "awayTeamLineUp")
        home_lu, away_lu = (n_away, n_home) if reverse else (n_home, n_away)
        confirmed = len(home_lu) == 9 and len(away_lu) == 9
        home_starter = _clean(((preview.get("homeStarter") or {}).get("playerInfo") or {}).get("name")) or None
        away_starter = _clean(((preview.get("awayStarter") or {}).get("playerInfo") or {}).get("name")) or None
        if reverse:
            home_starter, away_starter = away_starter, home_starter
        return {
            "confirmed": confirmed,
            "home": home_lu,
            "away": away_lu,
            "home_war": None,
            "away_war": None,
            "home_starter": home_starter,
            "away_starter": away_starter,
            "source_game_id": gid,
            "source": "Naver Sports public preview fallback",
            "label": "확정 (네이버스포츠 fallback)" if confirmed else "부분수집 (네이버스포츠)",
            "kind": "starting" if confirmed else "partial",
            "fallback_used": True,
            "reason": "" if confirmed else f"Naver batting orders {len(away_lu)}/9 away, {len(home_lu)}/9 home",
        }

    def _lineup(self, game_id, season):
        r = self.s.post(
            f"{KBO_SCHEDULE_BASE}/GetLineUpAnalysis",
            data={"leId": "1", "srId": "0", "seasonId": str(season), "gameId": str(game_id)},
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
        data = r.json()
        try:
            confirmed = _flag_true((data.get("0") or [{}])[0].get("LINEUP_CK"))
        except Exception:
            confirmed = False

        def rows(key):
            raw = (data.get(key) or [None])[0]
            out = []
            for rr,ids in _parse_make_table_rich(raw):
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
            return sorted(out, key=lambda x: x["order"])

        def war(key):
            m = (data.get(key) or [{}])[0]
            vals = [_num(m.get("HITTER_12_WAR_RT")), _num(m.get("HITTER_35_WAR_RT")), _num(m.get("HITTER_69_WAR_RT"))]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None

        home_meta=(data.get("1") or [{}])[0] or {}
        away_meta=(data.get("2") or [{}])[0] or {}
        return {
            "confirmed": confirmed, "home": rows("3"), "away": rows("4"),
            "home_war": war("1"), "away_war": war("2"),
            "home_team": _clean(home_meta.get("T_NM", "")),
            "away_team": _clean(away_meta.get("T_NM", "")),
            "source_game_id": str(home_meta.get("G_ID") or away_meta.get("G_ID") or ""),
        }

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
        self._bat = {}
        self._pit = {}

    def _get(self, url):
        if url not in self._pages:
            r = self.s.get(url, timeout=self.timeout)
            r.raise_for_status()
            r.encoding = "utf-8"
            self._pages[url] = r.text
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
