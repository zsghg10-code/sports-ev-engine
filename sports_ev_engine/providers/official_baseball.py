
from __future__ import annotations

from io import StringIO
import math
import re
import requests
import pandas as pd

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,ja;q=0.7",
}

NPB_BATTING = {
    "C": "https://npb.jp/bis/{year}/stats/tmb_c.html",
    "P": "https://npb.jp/bis/{year}/stats/tmb_p.html",
}
NPB_PITCHING = {
    "C": "https://npb.jp/bis/{year}/stats/tmp_c.html",
    "P": "https://npb.jp/bis/{year}/stats/tmp_p.html",
}

KBO_BATTING = "https://www.koreabaseball.com/Record/Team/Hitter/Basic1.aspx"
KBO_PITCHING = "https://www.koreabaseball.com/Record/Team/Pitcher/Basic1.aspx"
KBO_RANK = "https://www.koreabaseball.com/Record/TeamRank/TeamRank.aspx"

NPB_NAME_MAP = {
    "ソフトバンク": "Fukuoka SoftBank Hawks",
    "福岡ソフトバンク": "Fukuoka SoftBank Hawks",
    "日本ハム": "Hokkaido Nippon-Ham Fighters",
    "北海道日本ハム": "Hokkaido Nippon-Ham Fighters",
    "西武": "Saitama Seibu Lions",
    "埼玉西武": "Saitama Seibu Lions",
    "オリックス": "Orix Buffaloes",
    "楽天": "Tohoku Rakuten Golden Eagles",
    "東北楽天": "Tohoku Rakuten Golden Eagles",
    "ロッテ": "Chiba Lotte Marines",
    "千葉ロッテ": "Chiba Lotte Marines",
    "阪神": "Hanshin Tigers",
    "巨人": "Yomiuri Giants",
    "読売": "Yomiuri Giants",
    "DeNA": "Yokohama DeNA BayStars",
    "横浜DeNA": "Yokohama DeNA BayStars",
    "ヤクルト": "Tokyo Yakult Swallows",
    "東京ヤクルト": "Tokyo Yakult Swallows",
    "中日": "Chunichi Dragons",
    "広島": "Hiroshima Toyo Carp",
    "広島東洋": "Hiroshima Toyo Carp",
}

KBO_NAME_MAP = {
    "LG": "LG Twins",
    "한화": "Hanwha Eagles",
    "SSG": "SSG Landers",
    "삼성": "Samsung Lions",
    "NC": "NC Dinos",
    "KT": "KT Wiz",
    "롯데": "Lotte Giants",
    "KIA": "KIA Tigers",
    "두산": "Doosan Bears",
    "키움": "Kiwoom Heroes",
}

ENGLISH_ALIASES = {
    "fukuokasoftbankhawks": "Fukuoka SoftBank Hawks",
    "softbankhawks": "Fukuoka SoftBank Hawks",
    "hokkaidonipponhamfighters": "Hokkaido Nippon-Ham Fighters",
    "nipponhamfighters": "Hokkaido Nippon-Ham Fighters",
    "saitamaseibulions": "Saitama Seibu Lions",
    "seibulions": "Saitama Seibu Lions",
    "orixbuffaloes": "Orix Buffaloes",
    "tohokurakutengoldeneagles": "Tohoku Rakuten Golden Eagles",
    "rakutengoldeneagles": "Tohoku Rakuten Golden Eagles",
    "chibalottemarines": "Chiba Lotte Marines",
    "hanshintigers": "Hanshin Tigers",
    "yomiurigiants": "Yomiuri Giants",
    "yokohamadenabaystars": "Yokohama DeNA BayStars",
    "denabaystars": "Yokohama DeNA BayStars",
    "tokyoyakultswallows": "Tokyo Yakult Swallows",
    "yakultswallows": "Tokyo Yakult Swallows",
    "chunichidragons": "Chunichi Dragons",
    "hiroshimatoyocarp": "Hiroshima Toyo Carp",
    "lgwins": "LG Twins",
    "hanwhaeagles": "Hanwha Eagles",
    "ssglanders": "SSG Landers",
    "samsunglions": "Samsung Lions",
    "ncdinos": "NC Dinos",
    "ktwiz": "KT Wiz",
    "lottegiants": "Lotte Giants",
    "kiatigers": "KIA Tigers",
    "doosanbears": "Doosan Bears",
    "kiwoomheroes": "Kiwoom Heroes",
}

def norm(s):
    return re.sub(r"[^a-z0-9가-힣ぁ-んァ-ヶ一-龯]+", "", str(s or "").lower())

def canonical_english(name):
    n = norm(name)
    if n in ENGLISH_ALIASES:
        return ENGLISH_ALIASES[n]
    # exact canonical names
    for v in set(ENGLISH_ALIASES.values()):
        if norm(v) == n:
            return v
    return str(name)

def _flatten_columns(df):
    out = df.copy()
    cols=[]
    for c in out.columns:
        if isinstance(c, tuple):
            vals=[str(x) for x in c if str(x).lower()!="nan"]
            c=" ".join(vals)
        cols.append(str(c).strip())
    out.columns=cols
    return out

def _num(v):
    try:
        s=str(v).replace(",","").strip()
        if s in {"","nan","None","-"}:
            return None
        return float(s)
    except Exception:
        return None

def _find_table(tables, required_any, team_tokens=("팀","チーム","팀명")):
    best=None
    best_score=-1
    for df in tables:
        d=_flatten_columns(df)
        cols="|".join(d.columns)
        score=0
        if any(t in cols for t in team_tokens):
            score+=10
        for r in required_any:
            if r in cols:
                score+=3
        if score>best_score:
            best=d; best_score=score
    if best is None or best_score<10:
        raise RuntimeError("official stats table not found")
    return best

class OfficialBaseballStats:
    def __init__(self, timeout=25):
        self.timeout=timeout
        self.s=requests.Session()
        self.s.headers.update(HEADERS)
        self.last_source=None
        self.last_error=None

    def _tables(self, url):
        try:
            r=self.s.get(url,timeout=self.timeout)
            r.raise_for_status()
            self.last_source=url
            self.last_error=None
            return pd.read_html(StringIO(r.text))
        except Exception as e:
            self.last_error=f"{type(e).__name__}: {e}"
            raise RuntimeError(self.last_error)

    def npb(self, year):
        rows={}
        for league in ("C","P"):
            bt=self._tables(NPB_BATTING[league].format(year=year))
            pt=self._tables(NPB_PITCHING[league].format(year=year))
            b=_find_table(bt,["得点","試合","打率"])
            p=_find_table(pt,["失点","防御率","勝利","敗北"])

            # locate columns dynamically
            team_b=next(c for c in b.columns if "チーム" in c)
            g_b=next(c for c in b.columns if "試合" in c)
            r_b=next(c for c in b.columns if "得点" in c)

            team_p=next(c for c in p.columns if "チーム" in c)
            g_p=next(c for c in p.columns if "試合" in c)
            ra_p=next(c for c in p.columns if "失点" in c)
            era_p=next(c for c in p.columns if "防御率" in c)
            w_p=next((c for c in p.columns if "勝利" in c),None)
            l_p=next((c for c in p.columns if "敗北" in c),None)

            pmap={}
            for _,rr in p.iterrows():
                jp=str(rr.get(team_p,"")).strip()
                en=NPB_NAME_MAP.get(jp)
                if not en:
                    continue
                pmap[en]=rr

            for _,rr in b.iterrows():
                jp=str(rr.get(team_b,"")).strip()
                en=NPB_NAME_MAP.get(jp)
                if not en:
                    continue
                g=_num(rr.get(g_b))
                runs=_num(rr.get(r_b))
                pr=pmap.get(en)
                if not g or runs is None or pr is None:
                    continue
                pg=_num(pr.get(g_p)) or g
                ra=_num(pr.get(ra_p))
                era=_num(pr.get(era_p))
                wins=_num(pr.get(w_p)) if w_p else None
                losses=_num(pr.get(l_p)) if l_p else None
                wpct=None
                if wins is not None and losses is not None and wins+losses>0:
                    wpct=wins/(wins+losses)
                rows[en]={
                    "team":en,
                    "games":int(g),
                    "runs_per_game":runs/g,
                    "runs_allowed_per_game":ra/pg if ra is not None and pg else None,
                    "era":era,
                    "win_pct":wpct,
                    "recent10_win_pct":None,
                    "source":"NPB.jp",
                }
        if len(rows)<10:
            raise RuntimeError(f"NPB official stats incomplete ({len(rows)} teams)")
        return rows

    @staticmethod
    def _recent10_pct(text):
        # e.g. 7승1무2패
        s=str(text)
        m=re.search(r"(\d+)승(?:(\d+)무)?(\d+)패",s)
        if not m:
            return None
        w=int(m.group(1)); d=int(m.group(2) or 0); l=int(m.group(3))
        n=w+d+l
        return (w+0.5*d)/n if n else None

    def kbo(self):
        bt=self._tables(KBO_BATTING)
        pt=self._tables(KBO_PITCHING)
        rt=self._tables(KBO_RANK)
        b=_find_table(bt,["R","G","AVG"])
        p=_find_table(pt,["ERA","G","R","WHIP"])
        rank=_find_table(rt,["최근10경기","승률","경기"])

        team_b=next(c for c in b.columns if "팀명" in c or c=="팀명")
        g_b=next(c for c in b.columns if c=="G" or c.endswith(" G"))
        r_b=next(c for c in b.columns if c=="R" or c.endswith(" R"))

        team_p=next(c for c in p.columns if "팀명" in c or c=="팀명")
        g_p=next(c for c in p.columns if c=="G" or c.endswith(" G"))
        r_p=next(c for c in p.columns if c=="R" or c.endswith(" R"))
        era_p=next(c for c in p.columns if "ERA" in c)
        whip_p=next((c for c in p.columns if "WHIP" in c),None)

        team_r=next(c for c in rank.columns if "팀명" in c)
        wpct_r=next((c for c in rank.columns if "승률" in c),None)
        recent_r=next((c for c in rank.columns if "최근10경기" in c),None)

        pmap={}
        for _,rr in p.iterrows():
            ko=str(rr.get(team_p,"")).strip()
            en=KBO_NAME_MAP.get(ko)
            if en:
                pmap[en]=rr

        rmap={}
        for _,rr in rank.iterrows():
            ko=str(rr.get(team_r,"")).strip()
            en=KBO_NAME_MAP.get(ko)
            if en:
                rmap[en]=rr

        rows={}
        for _,rr in b.iterrows():
            ko=str(rr.get(team_b,"")).strip()
            en=KBO_NAME_MAP.get(ko)
            if not en:
                continue
            g=_num(rr.get(g_b)); runs=_num(rr.get(r_b))
            pr=pmap.get(en)
            if not g or runs is None or pr is None:
                continue
            pg=_num(pr.get(g_p)) or g
            ra=_num(pr.get(r_p))
            era=_num(pr.get(era_p))
            whip=_num(pr.get(whip_p)) if whip_p else None
            rrk=rmap.get(en)
            wpct=_num(rrk.get(wpct_r)) if rrk is not None and wpct_r else None
            r10=self._recent10_pct(rrk.get(recent_r)) if rrk is not None and recent_r else None
            rows[en]={
                "team":en,
                "games":int(g),
                "runs_per_game":runs/g,
                "runs_allowed_per_game":ra/pg if ra is not None and pg else None,
                "era":era,
                "whip":whip,
                "win_pct":wpct,
                "recent10_win_pct":r10,
                "source":"KBO official",
            }
        if len(rows)<8:
            raise RuntimeError(f"KBO official stats incomplete ({len(rows)} teams)")
        return rows

    def load(self, league, year):
        lg=str(league).upper()
        if lg=="NPB":
            return self.npb(year)
        if lg=="KBO":
            return self.kbo()
        raise ValueError(f"unsupported league: {league}")
