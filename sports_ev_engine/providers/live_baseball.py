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
        date8 = _kst_dt(commence_iso).strftime("%Y%m%d")
        games = self._game_list(date8)
        for reverse in (False, True):
            for g in games:
                gh = KBO_EN_BY_KR.get(str(g.get("HOME_NM", "")).strip(), str(g.get("HOME_NM", "")))
                ga = KBO_EN_BY_KR.get(str(g.get("AWAY_NM", "")).strip(), str(g.get("AWAY_NM", "")))
                if not reverse and _same_team(gh, home) and _same_team(ga, away):
                    return g
                if reverse and _same_team(gh, away) and _same_team(ga, home):
                    return g
        return None

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
            confirmed = bool((data.get("0") or [{}])[0].get("LINEUP_CK"))
        except Exception:
            confirmed = False

        def rows(key):
            raw = (data.get(key) or [None])[0]
            out = []
            for rr in _parse_make_table(raw):
                if len(rr) < 3:
                    continue
                try:
                    order = int(rr[0])
                except Exception:
                    continue
                if 1 <= order <= 9:
                    out.append({"order": order, "position": rr[1], "name": rr[2], "war": _num(rr[3]) if len(rr) > 3 else None})
            return sorted(out, key=lambda x: x["order"])

        def war(key):
            m = (data.get(key) or [{}])[0]
            vals = [_num(m.get("HITTER_12_WAR_RT")), _num(m.get("HITTER_35_WAR_RT")), _num(m.get("HITTER_69_WAR_RT"))]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None

        return {"confirmed": confirmed, "home": rows("3"), "away": rows("4"), "home_war": war("1"), "away_war": war("2")}

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
        home_sp = _clean(g.get("B_PIT_P_NM", "")) or None
        away_sp = _clean(g.get("T_PIT_P_NM", "")) or None
        starter_ok = bool(g.get("START_PIT_CK")) and bool(home_sp and away_sp)

        lineup = {"confirmed": False, "home": [], "away": [], "home_war": None, "away_war": None}
        try:
            lineup = self._lineup(game_id, season)
        except Exception:
            pass
        lineup_ok = bool(lineup.get("confirmed")) and len(lineup.get("home", [])) >= 9 and len(lineup.get("away", [])) >= 9

        pmap = self._pitcher_table()
        home_stat = pmap.get(_norm(home_sp), {}) if home_sp else {}
        away_stat = pmap.get(_norm(away_sp), {}) if away_sp else {}

        stage = "FINAL" if starter_ok and lineup_ok else "LINEUP CONFIRMED" if lineup_ok else "STARTER CONFIRMED" if starter_ok else "PRE-LINEUP"
        return {
            "league": "KBO", "stage": stage,
            "starter_confirmed": starter_ok, "lineup_confirmed": lineup_ok,
            "home_starter": home_sp, "away_starter": away_sp,
            "home_starter_stats": home_stat, "away_starter_stats": away_stat,
            "home_lineup": lineup.get("home", []), "away_lineup": lineup.get("away", []),
            "home_lineup_strength": lineup.get("home_war"), "away_lineup_strength": lineup.get("away_war"),
            "source": "KBO official GameCenter", "note": "", "game_id": game_id, "game_url": None,
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

    def _starter_map(self):
        soup = BeautifulSoup(self._get(NPB_STARTERS), "html.parser")
        out = {}

        # Primary DOM path: official page uses a team-logo image followed by a pitcher link.
        for img in soup.find_all("img"):
            alt = _clean(img.get("alt", ""))
            team = NPB_FULL_MAP.get(alt)
            if not team:
                continue
            parent = img.find_parent("a") or img.parent
            node = parent
            pitcher = None
            for _ in range(8):
                node = node.find_next() if node is not None else None
                if node is None:
                    break
                if getattr(node, "name", None) == "a":
                    cand = _clean(node.get_text(" ", strip=True))
                    if cand and cand not in NPB_FULL_MAP and not re.search(r"\d{1,2}:\d{2}|球場|ドーム|スタジアム", cand):
                        pitcher = cand
                        break
            if pitcher:
                out[team] = pitcher

        # Fallback for layouts where team names are visible text rather than img alt.
        strings = [_clean(x) for x in soup.stripped_strings]
        for i, label in enumerate(strings):
            team = NPB_FULL_MAP.get(label)
            if not team or team in out:
                continue
            for cand in strings[i+1:i+8]:
                if cand in NPB_FULL_MAP:
                    break
                if not cand or re.search(r"\d{1,2}:\d{2}|球場|ドーム|スタジアム|月|日", cand):
                    continue
                if any(w in cand for w in ["予告先発", "投手", "公示", "更新", "一覧"]):
                    continue
                if len(cand) <= 24:
                    out[team] = cand
                    break
        return out

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
        confirmed = pregame and len(home) >= 9 and len(away) >= 9
        return {"confirmed": confirmed, "home": home, "away": away}

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
        try: starters=self._starter_map()
        except Exception: starters={}
        home_sp=starters.get(home); away_sp=starters.get(away)
        starter_ok=bool(home_sp and away_sp)
        try: url=self._score_page(home,away,commence_iso)
        except Exception: url=None
        try: lu=self._lineups(url)
        except Exception: lu={"confirmed":False,"home":[],"away":[]}
        lineup_ok=bool(lu.get("confirmed")) and len(lu.get("home",[]))>=9 and len(lu.get("away",[]))>=9
        home_p=self._fuzzy(self._pitching(home,year),home_sp) or {}
        away_p=self._fuzzy(self._pitching(away,year),away_sp) or {}
        hf=self._lineup_factor(home,lu.get("home",[]),year) if lineup_ok else None
        af=self._lineup_factor(away,lu.get("away",[]),year) if lineup_ok else None
        stage="FINAL" if starter_ok and lineup_ok else "LINEUP CONFIRMED" if lineup_ok else "STARTER CONFIRMED" if starter_ok else "PRE-LINEUP"
        return {
            "league":"NPB","stage":stage,"starter_confirmed":starter_ok,"lineup_confirmed":lineup_ok,
            "home_starter":home_sp,"away_starter":away_sp,
            "home_starter_stats":home_p,"away_starter_stats":away_p,
            "home_lineup":lu.get("home",[]),"away_lineup":lu.get("away",[]),
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

