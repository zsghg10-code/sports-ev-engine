
from __future__ import annotations

from io import StringIO
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

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
    "福岡ソフトバンクホークス": "Fukuoka SoftBank Hawks",
    "日本ハム": "Hokkaido Nippon-Ham Fighters",
    "北海道日本ハム": "Hokkaido Nippon-Ham Fighters",
    "北海道日本ハムファイターズ": "Hokkaido Nippon-Ham Fighters",
    "西武": "Saitama Seibu Lions",
    "埼玉西武": "Saitama Seibu Lions",
    "埼玉西武ライオンズ": "Saitama Seibu Lions",
    "オリックス": "Orix Buffaloes",
    "オリックス・バファローズ": "Orix Buffaloes",
    "楽天": "Tohoku Rakuten Golden Eagles",
    "東北楽天": "Tohoku Rakuten Golden Eagles",
    "東北楽天ゴールデンイーグルス": "Tohoku Rakuten Golden Eagles",
    "ロッテ": "Chiba Lotte Marines",
    "千葉ロッテ": "Chiba Lotte Marines",
    "千葉ロッテマリーンズ": "Chiba Lotte Marines",
    "阪神": "Hanshin Tigers",
    "阪神タイガース": "Hanshin Tigers",
    "巨人": "Yomiuri Giants",
    "読売": "Yomiuri Giants",
    "読売ジャイアンツ": "Yomiuri Giants",
    "DeNA": "Yokohama DeNA BayStars",
    "横浜DeNA": "Yokohama DeNA BayStars",
    "横浜DeNAベイスターズ": "Yokohama DeNA BayStars",
    "ヤクルト": "Tokyo Yakult Swallows",
    "東京ヤクルト": "Tokyo Yakult Swallows",
    "東京ヤクルトスワローズ": "Tokyo Yakult Swallows",
    "中日": "Chunichi Dragons",
    "中日ドラゴンズ": "Chunichi Dragons",
    "広島": "Hiroshima Toyo Carp",
    "広島東洋": "Hiroshima Toyo Carp",
    "広島東洋カープ": "Hiroshima Toyo Carp",
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
    for v in set(ENGLISH_ALIASES.values()):
        if norm(v) == n:
            return v
    return str(name)

def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def _num(v):
    try:
        s = str(v).replace(",", "").strip()
        if s in {"", "nan", "None", "-", "－"}:
            return None
        return float(s)
    except Exception:
        return None

def _match_npb_team(text):
    t = _clean(text)
    if t in NPB_NAME_MAP:
        return NPB_NAME_MAP[t]
    # Official pages sometimes include full club names or extra whitespace.
    hits = []
    for jp, en in NPB_NAME_MAP.items():
        if jp and jp in t:
            hits.append((len(jp), en))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]

def _match_kbo_team(text):
    t = _clean(text)
    if t in KBO_NAME_MAP:
        return KBO_NAME_MAP[t]
    for ko, en in KBO_NAME_MAP.items():
        if ko and ko in t:
            return en
    return None

def _html_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        cells = [_clean(x.get_text(" ", strip=True)) for x in tr.find_all(["th", "td"])]
        if cells:
            out.append(cells)
    return out

def _header_map(rows, required):
    """
    Find a header row containing all requested tokens.
    Returns token -> column index.
    """
    for cells in rows:
        mapping = {}
        for token in required:
            for i, c in enumerate(cells):
                if c == token or token in c:
                    mapping[token] = i
                    break
        if len(mapping) == len(required):
            return mapping
    return None

class OfficialBaseballStats:
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.last_source = None
        self.last_error = None
        self.last_status = None

    def _html(self, url):
        try:
            r = self.s.get(url, timeout=self.timeout)
            self.last_status = r.status_code
            r.raise_for_status()
            # Both NPB and KBO official pages are currently UTF-8. Force it so
            # Japanese/Korean header text cannot be destroyed by a bad guess.
            r.encoding = "utf-8"
            self.last_source = url
            self.last_error = None
            return r.text
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            raise RuntimeError(f"official page fetch failed: {self.last_error}")

    # ---------- NPB ----------
    def _npb_batting_rows(self, html, label):
        rows = _html_rows(html)
        header = _header_map(rows, ["チーム", "試合", "得点"])
        result = {}

        for cells in rows:
            team = next((_match_npb_team(c) for c in cells if _match_npb_team(c)), None)
            if not team:
                continue

            if header:
                try:
                    g = _num(cells[header["試合"]])
                    runs = _num(cells[header["得点"]])
                except Exception:
                    g = runs = None
            else:
                # Official NPB batting layout:
                # team, AVG, G, PA, AB, R, ...
                g = _num(cells[2]) if len(cells) > 2 else None
                runs = _num(cells[5]) if len(cells) > 5 else None

            if g and runs is not None:
                result[team] = {"games": g, "runs": runs}

        if len(result) < 5:
            raise RuntimeError(
                f"NPB {label} batting parse failed: mapped {len(result)} teams "
                f"(source={self.last_source}, status={self.last_status})"
            )
        return result

    def _npb_pitching_rows(self, html, label):
        rows = _html_rows(html)
        header = _header_map(rows, ["チーム", "防御率", "試合", "勝利", "敗北", "失点"])
        result = {}

        for cells in rows:
            team = next((_match_npb_team(c) for c in cells if _match_npb_team(c)), None)
            if not team:
                continue

            if header:
                try:
                    era = _num(cells[header["防御率"]])
                    g = _num(cells[header["試合"]])
                    wins = _num(cells[header["勝利"]])
                    losses = _num(cells[header["敗北"]])
                    ra = _num(cells[header["失点"]])
                except Exception:
                    era = g = wins = losses = ra = None
            else:
                # Official NPB pitching layout:
                # team, ERA, G, W, L, SV, HLD, HP, CG, SHO, BB0, WPCT,
                # BF, IP, H, HR, BB, IBB, HBP, SO, WP, BK, R, ER
                era = _num(cells[1]) if len(cells) > 1 else None
                g = _num(cells[2]) if len(cells) > 2 else None
                wins = _num(cells[3]) if len(cells) > 3 else None
                losses = _num(cells[4]) if len(cells) > 4 else None
                ra = _num(cells[22]) if len(cells) > 22 else None

            if g and ra is not None:
                wpct = None
                if wins is not None and losses is not None and wins + losses > 0:
                    wpct = wins / (wins + losses)
                result[team] = {
                    "games": g, "runs_allowed": ra, "era": era,
                    "wins": wins, "losses": losses, "win_pct": wpct,
                }

        if len(result) < 5:
            raise RuntimeError(
                f"NPB {label} pitching parse failed: mapped {len(result)} teams "
                f"(source={self.last_source}, status={self.last_status})"
            )
        return result

    def npb(self, year):
        final = {}
        for league in ("C", "P"):
            label = "Central" if league == "C" else "Pacific"
            batting = self._npb_batting_rows(
                self._html(NPB_BATTING[league].format(year=year)), label
            )
            pitching = self._npb_pitching_rows(
                self._html(NPB_PITCHING[league].format(year=year)), label
            )

            for team, b in batting.items():
                p = pitching.get(team)
                if not p:
                    continue
                g = b["games"]
                pg = p["games"] or g
                final[team] = {
                    "team": team,
                    "games": int(g),
                    "runs_per_game": b["runs"] / g,
                    "runs_allowed_per_game": p["runs_allowed"] / pg,
                    "era": p.get("era"),
                    "whip": None,
                    "win_pct": p.get("win_pct"),
                    "recent10_win_pct": None,
                    "source": "NPB.jp",
                }

        if len(final) < 10:
            raise RuntimeError(
                f"NPB official stats incomplete ({len(final)} teams). "
                f"Last source={self.last_source}; last error={self.last_error}"
            )
        return final

    # ---------- KBO ----------
    @staticmethod
    def _recent10_pct(text):
        s = str(text)
        m = re.search(r"(\d+)승(?:(\d+)무)?(\d+)패", s)
        if not m:
            return None
        w = int(m.group(1)); d = int(m.group(2) or 0); l = int(m.group(3))
        n = w + d + l
        return (w + 0.5 * d) / n if n else None

    def _kbo_table(self, html, required, kind):
        rows = _html_rows(html)
        header = _header_map(rows, required)
        if not header:
            # Keep one pandas fallback, but no longer depend on it.
            try:
                tables = pd.read_html(StringIO(html))
                for df in tables:
                    cols = [str(c).strip() for c in df.columns]
                    if all(any(tok == c or tok in c for c in cols) for tok in required):
                        return df
            except Exception:
                pass
            raise RuntimeError(
                f"KBO {kind} header not found (source={self.last_source}, status={self.last_status})"
            )

        records = []
        for cells in rows:
            team = next((_match_kbo_team(c) for c in cells if _match_kbo_team(c)), None)
            if not team:
                continue
            rec = {"team": team}
            ok = True
            for token, idx in header.items():
                if idx >= len(cells):
                    ok = False
                    break
                rec[token] = cells[idx]
            if ok:
                records.append(rec)

        if len(records) < 8:
            raise RuntimeError(
                f"KBO {kind} parse failed: mapped {len(records)} teams "
                f"(source={self.last_source}, status={self.last_status})"
            )
        return records

    def kbo(self):
        batting = self._kbo_table(
            self._html(KBO_BATTING), ["팀명", "G", "R"], "batting"
        )
        pitching = self._kbo_table(
            self._html(KBO_PITCHING), ["팀명", "ERA", "G", "R", "WHIP"], "pitching"
        )
        rank = self._kbo_table(
            self._html(KBO_RANK), ["팀명", "승률", "최근10경기"], "standings"
        )

        pmap = {r["team"]: r for r in pitching}
        rmap = {r["team"]: r for r in rank}
        final = {}

        for b in batting:
            team = b["team"]
            p = pmap.get(team)
            if not p:
                continue
            g = _num(b.get("G"))
            runs = _num(b.get("R"))
            pg = _num(p.get("G")) or g
            ra = _num(p.get("R"))
            if not g or runs is None or not pg or ra is None:
                continue

            rr = rmap.get(team, {})
            final[team] = {
                "team": team,
                "games": int(g),
                "runs_per_game": runs / g,
                "runs_allowed_per_game": ra / pg,
                "era": _num(p.get("ERA")),
                "whip": _num(p.get("WHIP")),
                "win_pct": _num(rr.get("승률")),
                "recent10_win_pct": self._recent10_pct(rr.get("최근10경기")),
                "source": "KBO official",
            }

        if len(final) < 8:
            raise RuntimeError(
                f"KBO official stats incomplete ({len(final)} teams). "
                f"Last source={self.last_source}; last error={self.last_error}"
            )
        return final

    def load(self, league, year):
        lg = str(league).upper()
        if lg == "NPB":
            return self.npb(year)
        if lg == "KBO":
            return self.kbo()
        raise ValueError(f"unsupported league: {league}")
