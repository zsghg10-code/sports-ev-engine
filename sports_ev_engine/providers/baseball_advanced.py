
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import json
import math
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

from sports_ev_engine.providers.official_baseball import canonical_english
from sports_ev_engine.providers.live_baseball import _kst_dt, _norm, _clean, _num, _npb_ip, _same_team, NPB_FULL_MAP, KBO_EN_BY_KR

PROVIDER_BUILD = "3.0.0"
BASEBALL_ADVANCED_BUILD = "3.4.20"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0 Safari/537.36"
)

KBO_PITCHER_DETAIL = "https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId={pid}"
KBO_BOXSCORE = "https://www.koreabaseball.com/ws/Schedule.asmx/GetBoxScoreScroll"
KBO_HITTER_BASIC = "https://www.koreabaseball.com/Record/Player/HitterDetail/Basic.aspx?playerId={pid}"
KBO_HITTER_SITUATION = "https://www.koreabaseball.com/Record/Player/HitterDetail/Situation.aspx?playerId={pid}"

NPB_DAY = "https://npb.jp/bis/eng/{year}/games/gm{date}.html"

# Coordinates are approximate stadium-center coordinates and are used only
# for weather-grid lookup. Roofed venues intentionally neutralize weather.
STADIUMS = {
    # KBO
    "LG Twins": {"name":"Jamsil Baseball Stadium","lat":37.5122,"lon":127.0719,"roof":"outdoor"},
    "Doosan Bears": {"name":"Jamsil Baseball Stadium","lat":37.5122,"lon":127.0719,"roof":"outdoor"},
    "Hanwha Eagles": {"name":"Daejeon Hanwha Life Ballpark","lat":36.3172,"lon":127.4291,"roof":"outdoor"},
    "SSG Landers": {"name":"Incheon SSG Landers Field","lat":37.4367,"lon":126.6932,"roof":"outdoor"},
    "Samsung Lions": {"name":"Daegu Samsung Lions Park","lat":35.8411,"lon":128.6814,"roof":"outdoor"},
    "NC Dinos": {"name":"Changwon NC Park","lat":35.2226,"lon":128.5822,"roof":"outdoor"},
    "KT Wiz": {"name":"Suwon KT Wiz Park","lat":37.2998,"lon":127.0097,"roof":"outdoor"},
    "Lotte Giants": {"name":"Sajik Baseball Stadium","lat":35.1940,"lon":129.0616,"roof":"outdoor"},
    "KIA Tigers": {"name":"Gwangju-Kia Champions Field","lat":35.1682,"lon":126.8891,"roof":"outdoor"},
    "Kiwoom Heroes": {"name":"Gocheok Sky Dome","lat":37.4982,"lon":126.8672,"roof":"indoor"},
    # NPB
    "Chiba Lotte Marines": {"name":"ZOZO Marine Stadium","lat":35.6452,"lon":140.0308,"roof":"outdoor","wind_sensitive":True},
    "Hokkaido Nippon-Ham Fighters": {"name":"ES CON FIELD HOKKAIDO","lat":42.9904,"lon":141.5497,"roof":"indoor"},
    "Fukuoka SoftBank Hawks": {"name":"Mizuho PayPay Dome","lat":33.5953,"lon":130.3622,"roof":"indoor"},
    "Orix Buffaloes": {"name":"Kyocera Dome Osaka","lat":34.6693,"lon":135.4760,"roof":"indoor"},
    "Saitama Seibu Lions": {"name":"Belluna Dome","lat":35.7686,"lon":139.4205,"roof":"semi"},
    "Tohoku Rakuten Golden Eagles": {"name":"Rakuten Mobile Park Miyagi","lat":38.2561,"lon":140.9026,"roof":"outdoor"},
    "Hanshin Tigers": {"name":"Hanshin Koshien Stadium","lat":34.7213,"lon":135.3616,"roof":"outdoor"},
    "Yomiuri Giants": {"name":"Tokyo Dome","lat":35.7056,"lon":139.7519,"roof":"indoor"},
    "Yokohama DeNA BayStars": {"name":"Yokohama Stadium","lat":35.4434,"lon":139.6401,"roof":"outdoor"},
    "Tokyo Yakult Swallows": {"name":"Meiji Jingu Stadium","lat":35.6745,"lon":139.7170,"roof":"outdoor"},
    "Chunichi Dragons": {"name":"Vantelin Dome Nagoya","lat":35.1859,"lon":136.9474,"roof":"indoor"},
    "Hiroshima Toyo Carp": {"name":"Mazda Stadium","lat":34.3928,"lon":132.4840,"roof":"outdoor"},
}

NPB_SHORT = {
    "Chiba Lotte Marines": ["Lotte","Chiba Lotte"],
    "Hokkaido Nippon-Ham Fighters": ["Nippon-Ham","Nippon Ham"],
    "Fukuoka SoftBank Hawks": ["SoftBank","Softbank"],
    "Orix Buffaloes": ["ORIX","Orix"],
    "Saitama Seibu Lions": ["Seibu"],
    "Tohoku Rakuten Golden Eagles": ["Rakuten"],
    "Hanshin Tigers": ["Hanshin"],
    "Yomiuri Giants": ["Yomiuri"],
    "Yokohama DeNA BayStars": ["DeNA","Yokohama"],
    "Tokyo Yakult Swallows": ["Yakult"],
    "Chunichi Dragons": ["Chunichi"],
    "Hiroshima Toyo Carp": ["Hiroshima"],
}


def _ip(v):
    s = str(v or "").strip().replace(" ", "")
    if not s:
        return None
    if "/" in s:
        m = re.match(r"^(\d+)?([12])/3$", s)
        if m:
            return float(m.group(1) or 0) + int(m.group(2))/3
    return _npb_ip(s)


def _ops_from_counts(ab,h,d2,d3,hr,bb,hbp=0,sf=0):
    vals=[ab,h,d2,d3,hr,bb,hbp,sf]
    if any(v is None for v in vals[:6]):
        return None
    ab=float(ab); h=float(h); d2=float(d2); d3=float(d3); hr=float(hr); bb=float(bb)
    hbp=float(hbp or 0); sf=float(sf or 0)
    if ab <= 0:
        return None
    singles=max(0.0,h-d2-d3-hr)
    tb=singles+2*d2+3*d3+4*hr
    slg=tb/ab
    denom=ab+bb+hbp+sf
    obp=(h+bb+hbp)/denom if denom>0 else 0
    return obp+slg


def _factor_from_ratio(ratio, max_move=.04, strength=.35):
    if ratio is None or ratio <= 0:
        return None
    raw=math.exp(strength*math.log(ratio))
    return max(1-max_move,min(1+max_move,raw))


def _safe_float(x):
    try:return float(x)
    except:return None


def _mmdd_key(v):
    """Stable KBO log ordering regardless of whether the source table is newest-first."""
    m=re.match(r"^(\d{1,2})\.(\d{1,2})",str(v or "").strip())
    return (int(m.group(1)),int(m.group(2))) if m else (0,0)


def _kbo_team(label):
    raw=_clean(label)
    return KBO_EN_BY_KR.get(raw, raw)


def _same_kbo_team(label, team):
    return _same_team(_kbo_team(label), canonical_english(team))


def _json_table_rows(raw):
    """Return KBO GameCenter JSON-table rows as plain strings."""
    if not raw:
        return []
    try:
        obj=json.loads(raw) if isinstance(raw,str) else raw
    except Exception:
        return []
    out=[]
    for row_obj in (obj or {}).get("rows",[]) or []:
        vals=[]
        for cell in (row_obj or {}).get("row",[]) or []:
            text=_clean((cell or {}).get("Text", ""))
            vals.append("" if text in {"&nbsp;","-"} else text)
        if vals:
            out.append(vals)
    return out


class WeatherProvider:
    def __init__(self, timeout=12):
        self.timeout=timeout
        self.s=requests.Session()
        self.s.headers.update({"User-Agent":UA})
        self.cache={}

    def get(self, home_team, commence_iso):
        team=canonical_english(home_team)
        park=STADIUMS.get(team)
        if not park:
            return {"available":False,"reason":"stadium mapping unavailable"}
        if park.get("roof")=="indoor":
            return {
                "available":True,"stadium":park["name"],"roof":"indoor",
                "temperature_c":None,"wind_kmh":0.0,"gust_kmh":0.0,
                "precip_mm":0.0,"humidity":None,"run_factor":1.0,
                "uncertainty_pp":0.0,"source":"roofed stadium"
            }

        dt=datetime.fromisoformat(str(commence_iso).replace("Z","+00:00"))
        tz="Asia/Seoul" if team in {
            "LG Twins","Doosan Bears","Hanwha Eagles","SSG Landers","Samsung Lions",
            "NC Dinos","KT Wiz","Lotte Giants","KIA Tigers","Kiwoom Heroes"
        } else "Asia/Tokyo"
        local=dt.astimezone(ZoneInfo(tz))
        key=(team,local.strftime("%Y-%m-%d-%H"))
        if key in self.cache:return self.cache[key]
        try:
            r=self.s.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude":park["lat"],"longitude":park["lon"],
                    "hourly":"temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,wind_gusts_10m",
                    "timezone":tz,
                    "start_date":local.date().isoformat(),
                    "end_date":local.date().isoformat(),
                },timeout=self.timeout
            )
            r.raise_for_status(); data=r.json()
            hourly=data.get("hourly",{})
            times=hourly.get("time",[])
            if not times:raise RuntimeError("weather hourly empty")
            target=local.replace(minute=0,second=0,microsecond=0).strftime("%Y-%m-%dT%H:%M")
            idx=min(range(len(times)),key=lambda i:abs(
                datetime.fromisoformat(times[i]).replace(tzinfo=local.tzinfo).timestamp()
                - local.timestamp()
            ))
            temp=_safe_float((hourly.get("temperature_2m") or [None]*len(times))[idx])
            hum=_safe_float((hourly.get("relative_humidity_2m") or [None]*len(times))[idx])
            rain=_safe_float((hourly.get("precipitation") or [None]*len(times))[idx]) or 0.0
            wind=_safe_float((hourly.get("wind_speed_10m") or [None]*len(times))[idx]) or 0.0
            gust=_safe_float((hourly.get("wind_gusts_10m") or [None]*len(times))[idx]) or wind

            # Only temperature changes scoring mean. Wind/rain increase uncertainty unless
            # direction can be robustly determined. This prevents false precision.
            temp_move=0.0 if temp is None else max(-.025,min(.025,(temp-20.0)*.0015))
            attenuation=.45 if park.get("roof")=="semi" else 1.0
            run_factor=1.0+temp_move*attenuation
            unc=0.0
            if rain>=0.5:unc+=0.7
            if wind>=20:unc+=1.0 if park.get("wind_sensitive") else .45
            elif wind>=12:unc+=.45 if park.get("wind_sensitive") else .20
            if gust>=35:unc+=.35
            if park.get("roof")=="semi":unc*=.5
            out={
                "available":True,"stadium":park["name"],"roof":park.get("roof"),
                "temperature_c":temp,"humidity":hum,"precip_mm":rain,
                "wind_kmh":wind,"gust_kmh":gust,"run_factor":run_factor,
                "uncertainty_pp":unc,"source":"Open-Meteo"
            }
        except Exception as e:
            out={"available":False,"stadium":park["name"],"roof":park.get("roof"),"reason":str(e)}
        self.cache[key]=out
        return out


class KBOAdvanced:
    def __init__(self, live_kbo, timeout=18):
        self.live=live_kbo
        self.timeout=timeout
        self.s=requests.Session(); self.s.headers.update({"User-Agent":UA})
        self.cache={}

    @staticmethod
    def _pid_from_game(g, home=True):
        if not isinstance(g,dict):return None
        if g.get("_event_reversed"):
            home = not home
        pref="B_" if home else "T_"
        candidates=[]
        for k,v in g.items():
            ku=str(k).upper()
            if not ku.startswith(pref):continue
            if ("PIT" in ku or "PITCH" in ku) and ("ID" in ku or "CODE" in ku or ku.endswith("_P_ID")):
                if str(v).strip().isdigit():candidates.append(str(v).strip())
        return candidates[0] if candidates else None

    @staticmethod
    def _pid_from_cell(raw_html):
        m=re.search(r"playerId=(\d+)",str(raw_html or ""),re.I)
        return m.group(1) if m else None

    def _fetch(self,url):
        if url in self.cache:return self.cache[url]
        r=self.s.get(url,timeout=self.timeout); r.raise_for_status(); r.encoding="utf-8"
        self.cache[url]=r.text
        return r.text

    def _pitcher_log(self,pid):
        if not pid:
            return [], None
        try:
            html=self._fetch(KBO_PITCHER_DETAIL.format(pid=pid))
            tables=pd.read_html(StringIO(html))
        except Exception as e:
            return [], str(e)
        df=next((d for d in tables if {"일자","IP","BB","SO","ER"}.issubset(set(map(str,d.columns)))),None)
        if df is None:
            return [], "recent pitcher table unavailable"
        rows=[]
        for _,r in df.iterrows():
            if not re.match(r"\d{2}\.\d{2}",str(r.get("일자",""))):
                continue
            rows.append(r)
        return rows, None

    @staticmethod
    def _aggregate_pitcher_rows(rows):
        if not rows:
            return {"available":False,"reason":"no pitcher games"}
        bf=sum(_num(r.get("TBF")) or 0 for r in rows)
        bb=sum(_num(r.get("BB")) or 0 for r in rows)
        so=sum(_num(r.get("SO")) or 0 for r in rows)
        er=sum(_num(r.get("ER")) or 0 for r in rows)
        runs=sum(_num(r.get("R")) or 0 for r in rows)
        hits=sum(_num(r.get("H")) or 0 for r in rows)
        hr=sum(_num(r.get("HR")) or 0 for r in rows)
        np=sum(_num(r.get("NP")) or 0 for r in rows)
        ip=sum(_ip(r.get("IP")) or 0 for r in rows)
        return {
            "available":True,"games":len(rows),"ip":ip,"bb":bb,"so":so,"er":er,"r":runs,
            "h":hits,"hr":hr,"bf":bf,"np":np or None,
            "era":9*er/ip if ip else None,
            "bb_pct":bb/bf if bf else None,"k_pct":so/bf if bf else None,
            "kbb_pct":(so-bb)/bf if bf else None,
        }

    def pitcher_recent(self,pid,n=5):
        rows,err=self._pitcher_log(pid)
        if err:
            return {"available":False,"reason":err}
        rows=sorted(rows,key=lambda r:_mmdd_key(r.get("일자")))[-int(n):]
        out=self._aggregate_pitcher_rows(rows)
        if not out.get("available"):
            out["reason"]="no recent pitcher games"
            return out
        try:
            html=self._fetch(KBO_PITCHER_DETAIL.format(pid=pid))
            text=BeautifulSoup(html,"html.parser").get_text(" ",strip=True)
            m=re.search(r"포지션\s*:\s*투수\(([^)]+)\)",text)
            hand="L" if m and "좌투" in m.group(1) else "R" if m and "우투" in m.group(1) else None
        except Exception:
            hand=None
        out.update({"hand":hand,"velocity_delta_kmh":None,
                    "velocity_status":"official pregame source does not expose recent velocity"})
        return out

    def pitcher_vs_opponent(self,pid,opponent,n=10):
        """Current-season starter results specifically against today's opposing team."""
        rows,err=self._pitcher_log(pid)
        if err:
            return {"available":False,"reason":err,"opponent":canonical_english(opponent)}
        if not rows or "상대" not in set(map(str,rows[0].index)):
            return {"available":False,"reason":"opponent column unavailable","opponent":canonical_english(opponent)}
        matched=[r for r in rows if _same_kbo_team(r.get("상대"),opponent)]
        matched=sorted(matched,key=lambda r:_mmdd_key(r.get("일자")))[-int(n):]
        out=self._aggregate_pitcher_rows(matched)
        out["opponent"]=canonical_english(opponent)
        out["sample_scope"]="current-season recent-game table"
        if not out.get("available"):
            out["reason"]="no current-season starts/appearances vs opponent in published log"
        return out

    def team_recent(self,team,commence_iso,n=10,max_days=35):
        """Exact recent team runs from the KBO GameCenter game list."""
        dt=_kst_dt(commence_iso)
        rows=[]
        for days in range(1,max_days+1):
            date8=(dt-timedelta(days=days)).strftime("%Y%m%d")
            try: games=self.live._game_list(date8)
            except Exception: games=[]
            for g in games:
                hn=_kbo_team(g.get("HOME_NM","")); an=_kbo_team(g.get("AWAY_NM",""))
                if not (_same_team(hn,team) or _same_team(an,team)):
                    continue
                hs=_num(g.get("B_SCORE_CN")); aas=_num(g.get("T_SCORE_CN"))
                if hs is None or aas is None:
                    continue
                is_home=_same_team(hn,team)
                rows.append({"date":date8,"rf":hs if is_home else aas,"ra":aas if is_home else hs,
                             "opponent":an if is_home else hn,"game_id":g.get("G_ID")})
                if len(rows)>=int(n):
                    break
            if len(rows)>=int(n):
                break
        if not rows:
            return {"available":False,"reason":"recent KBO scores unavailable"}
        return {"available":True,"games":len(rows),
                "runs_for_per_game":sum(x["rf"] for x in rows)/len(rows),
                "runs_against_per_game":sum(x["ra"] for x in rows)/len(rows),
                "raw":rows}

    def _boxscore_pitchers(self,g,team):
        gid=str(g.get("G_ID") or "").strip()
        if not gid:
            return []
        season=int(g.get("SEASON_ID") or gid[:4])
        sr=int(g.get("SR_ID") or 0)
        try:
            r=self.s.post(KBO_BOXSCORE,data={"leId":1,"srId":sr,"seasonId":season,"gameId":gid},
                          headers={"Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
                                   "X-Requested-With":"XMLHttpRequest",
                                   "Referer":"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx",
                                   "User-Agent":UA},timeout=self.timeout)
            r.raise_for_status(); obj=r.json()
        except Exception:
            return []
        arr=obj.get("arrPitcher") or []
        hn=_kbo_team(g.get("HOME_NM","")); an=_kbo_team(g.get("AWAY_NM",""))
        idx=1 if _same_team(hn,team) else 0 if _same_team(an,team) else None
        if idx is None or len(arr)<=idx:
            return []
        rows=_json_table_rows((arr[idx] or {}).get("table"))
        out=[]
        for i,row in enumerate(rows):
            if len(row)<9:
                continue
            out.append({"name":row[0],"entry":row[1],"ip":_ip(row[6]),"bf":_num(row[7]),"np":_num(row[8]),
                        "h":_num(row[10]) if len(row)>10 else None,"hr":_num(row[11]) if len(row)>11 else None,
                        "bb":_num(row[12]) if len(row)>12 else None,"so":_num(row[13]) if len(row)>13 else None,
                        "r":_num(row[14]) if len(row)>14 else None,"er":_num(row[15]) if len(row)>15 else None,
                        "_idx":i})
        return out

    def bullpen_exact(self,team,commence_iso,days=3):
        dt=_kst_dt(commence_iso)
        total_ip=0.0; total_np=0.0; apps=0; game_days=0; names={}
        checked_games=0; failed_boxes=0; used_yesterday=False
        for back in range(1,int(days)+1):
            date8=(dt-timedelta(days=back)).strftime("%Y%m%d")
            try: games=self.live._game_list(date8)
            except Exception: games=[]
            day_used=False
            for g in games:
                hn=_kbo_team(g.get("HOME_NM","")); an=_kbo_team(g.get("AWAY_NM",""))
                if not (_same_team(hn,team) or _same_team(an,team)):
                    continue
                if _num(g.get("B_SCORE_CN")) is None or _num(g.get("T_SCORE_CN")) is None:
                    continue
                checked_games+=1
                pits=self._boxscore_pitchers(g,team)
                if not pits:
                    failed_boxes+=1; continue
                # KBO box score is ordered by appearance; first row is normally the starter.
                rel=[p for p in pits if not ("선발" in str(p.get("entry") or ""))]
                if len(rel)==len(pits) and rel:
                    rel=rel[1:]
                if not rel:
                    continue
                day_used=True
                for q in rel:
                    ip=float(q.get("ip") or 0); np=float(q.get("np") or 0)
                    total_ip+=ip; total_np+=np; apps+=1
                    name=q.get("name") or "?"
                    d=names.setdefault(name,{"appearances":0,"pitches":0.0,"ip":0.0})
                    d["appearances"]+=1; d["pitches"]+=np; d["ip"]+=ip
            if day_used:
                game_days+=1
                if back==1: used_yesterday=True
        if checked_games and failed_boxes < checked_games:
            # A parsed complete game with zero relief appearances is valid exact
            # evidence of a rested bullpen; do not replace it with a fatigue proxy.
            consecutive=max(0,game_days-1)
            score=max(0,min(1,total_np/180.0 + .035*max(0,apps-6) + .08*int(used_yesterday) + .05*consecutive))
            top=sorted(({"name":k,**v} for k,v in names.items()),key=lambda x:(x["pitches"],x["appearances"]),reverse=True)[:6]
            return {"available":True,"exact":True,"games_last3":checked_games,"days_used":game_days,
                    "relief_ip_last3":total_ip,"total_relief_pitches":total_np,"reliever_apps_last3":apps,
                    "used_yesterday":used_yesterday,"score":score,"top_relief_usage":top,
                    "source":"KBO GameCenter GetBoxScoreScroll"}
        out=self.schedule_load(team,commence_iso)
        out["reason"]="exact KBO boxscore bullpen usage unavailable; schedule proxy used"
        return out

    def hitter_detail(self,pid,starter_hand):
        if not pid:return {}
        out={"available":False}
        try:
            basic=self._fetch(KBO_HITTER_BASIC.format(pid=pid))
            tables=pd.read_html(StringIO(basic))
            recent=next((d for d in tables if {"일자","AB","H","2B","3B","HR","BB","HBP"}.issubset(set(map(str,d.columns)))),None)
            season_ops=None
            # basic page has a season line and OPS in a separate table
            for d in tables:
                if "OPS" in set(map(str,d.columns)):
                    for _,r in d.iterrows():
                        v=_num(r.get("OPS"))
                        if v is not None and 0<=v<=2:
                            season_ops=v;break
                    if season_ops is not None:break
            recent_ops=None
            if recent is not None:
                rr=[r for _,r in recent.iterrows() if re.match(r"\d{1,2}\.\d{1,2}",str(r.get("일자","")))]
                rr=sorted(rr,key=lambda r:_mmdd_key(r.get("일자")))[-10:]
                if rr:
                    sums={k:sum(_num(r.get(k)) or 0 for r in rr) for k in ["AB","H","2B","3B","HR","BB","HBP"]}
                    recent_ops=_ops_from_counts(sums["AB"],sums["H"],sums["2B"],sums["3B"],sums["HR"],sums["BB"],sums["HBP"],0)
        except Exception:
            season_ops=recent_ops=None

        split_ops=None; split_ab=0
        if starter_hand in {"L","R"}:
            try:
                sit=self._fetch(KBO_HITTER_SITUATION.format(pid=pid))
                tabs=pd.read_html(StringIO(sit))
                wanted="좌투수" if starter_hand=="L" else "우투수"
                for d in tabs:
                    if "구분" not in set(map(str,d.columns)):continue
                    hit=d[d["구분"].astype(str).str.contains(wanted,na=False)]
                    if hit.empty:continue
                    r=hit.iloc[0]
                    split_ab=_num(r.get("AB")) or 0
                    split_ops=_ops_from_counts(
                        _num(r.get("AB")),_num(r.get("H")),_num(r.get("2B")),_num(r.get("3B")),
                        _num(r.get("HR")),_num(r.get("BB")),_num(r.get("HBP")),0
                    )
                    break
            except Exception:
                pass
        return {"available":bool(season_ops or recent_ops or split_ops),
                "season_ops":season_ops,"recent10_ops":recent_ops,"split_ops":split_ops,"split_ab":split_ab}

    def schedule_load(self,team,commence_iso):
        """Fallback bullpen-load proxy when exact reliever logs are unavailable."""
        dt=_kst_dt(commence_iso)
        games=0; yesterday=False; consecutive=0
        for days in (1,2,3):
            d=(dt-timedelta(days=days)).strftime("%Y%m%d")
            try: rows=self.live._game_list(d)
            except Exception: rows=[]
            played=False
            for g in rows:
                hn=str(g.get("HOME_NM","")); an=str(g.get("AWAY_NM",""))
                if _same_team(hn,team) or _same_team(an,team):
                    played=True; games+=1;break
            if days==1:yesterday=played
            if played and days<=2:consecutive+=1
        score=min(1.0,.22*games+.15*int(yesterday)+.10*max(0,consecutive-1))
        return {"available":games>0,"exact":False,"games_last3":games,"score":score,
                "note":"schedule-based bullpen fatigue proxy; exact relief innings unavailable"}

    def enrich(self,home,away,commence_iso,ctx,recent_n=10):
        result={"source":"KBO official advanced","notes":[]}
        try:g=self.live._match_game(home,away,commence_iso)
        except Exception:g=None
        # The live GameCenter parser already resolves the starter IDs from
        # the scheduled game. Reuse them when the advanced lookup has no match.
        home_pid=self._pid_from_game(g,True) or ctx.get("home_starter_id")
        away_pid=self._pid_from_game(g,False) or ctx.get("away_starter_id")
        result["home_starter_recent"]=self.pitcher_recent(home_pid,5)
        result["away_starter_recent"]=self.pitcher_recent(away_pid,5)
        # Today's starter vs today's opposing TEAM.  This is not generic head-to-head.
        result["home_starter_vs_opponent"]=self.pitcher_vs_opponent(home_pid,away,10)
        result["away_starter_vs_opponent"]=self.pitcher_vs_opponent(away_pid,home,10)
        result["home_recent"]=self.team_recent(home,commence_iso,recent_n)
        result["away_recent"]=self.team_recent(away,commence_iso,recent_n)

        # Lineup hitter IDs can be supplied by v2.6 live parser. If not, skip cleanly.
        home_lu=ctx.get("home_lineup",[]) or []; away_lu=ctx.get("away_lineup",[]) or []
        hhand=(result["away_starter_recent"] or {}).get("hand")
        ahand=(result["home_starter_recent"] or {}).get("hand")

        def lineup_metrics(players,opponent_hand):
            ids=[p.get("player_id") for p in players if p.get("player_id")]
            if not ids:return {"available":False,"reason":"lineup player ids unavailable"}
            details=[]
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs={ex.submit(self.hitter_detail,pid,opponent_hand):pid for pid in ids[:9]}
                for f in as_completed(futs):
                    try:details.append(f.result())
                    except Exception:pass
            rec=[d.get("recent10_ops") for d in details if d.get("recent10_ops")]
            seas=[d.get("season_ops") for d in details if d.get("season_ops")]
            spl=[(d.get("split_ops"),d.get("split_ab") or 0) for d in details if d.get("split_ops")]
            split=sum(v*max(1,w) for v,w in spl)/sum(max(1,w) for _,w in spl) if spl else None
            return {
                "available":bool(details),"players":len(details),
                "recent10_ops":sum(rec)/len(rec) if rec else None,
                "season_ops":sum(seas)/len(seas) if seas else None,
                "split_ops":split,
                "split_exact":bool(split),
            }

        result["home_lineup_form"]=lineup_metrics(home_lu,hhand)
        result["away_lineup_form"]=lineup_metrics(away_lu,ahand)
        result["home_bullpen"]=self.bullpen_exact(home,commence_iso)
        result["away_bullpen"]=self.bullpen_exact(away,commence_iso)
        return result


class NPBRecent:
    def __init__(self, timeout=15, live=None):
        self.timeout=timeout
        self.live=live
        self.s=requests.Session();self.s.headers.update({"User-Agent":UA,"Accept-Language":"en,ja;q=0.8"})
        self.cache={}

    def _get(self,url):
        if url in self.cache:return self.cache[url]
        r=self.s.get(url,timeout=self.timeout);r.raise_for_status();r.encoding="utf-8"
        self.cache[url]=r.text;return r.text

    def _daily_links(self,date):
        url=NPB_DAY.format(year=date.year,date=date.strftime("%Y%m%d"))
        try:html=self._get(url)
        except Exception:return []
        soup=BeautifulSoup(html,"html.parser");out=[]
        for a in soup.find_all("a",href=True):
            href=a["href"]
            if re.search(r"(?:^|/)s\d{8}\d+\.html$",href):
                u=urljoin(url,href)
                if u not in out:out.append(u)
        # Broad fallback used by NPB current pages.
        if not out:
            for a in soup.find_all("a",href=True):
                href=a["href"]
                if re.search(r"(?:^|/)s"+date.strftime("%Y%m%d")+r"\d+\.html$",href):
                    u=urljoin(url,href)
                    if u not in out:out.append(u)
        return out

    def _contains_team(self,text,team):
        return any(x.lower() in text.lower() for x in NPB_SHORT.get(canonical_english(team),[canonical_english(team)]))

    def recent_game_urls(self,team,commence_iso,n=10,max_days=32):
        dt=_kst_dt(commence_iso).date();out=[]
        for d in range(1,max_days+1):
            day=dt-timedelta(days=d)
            for u in self._daily_links(day):
                try:text=BeautifulSoup(self._get(u),"html.parser").get_text(" ",strip=True)
                except Exception:continue
                if self._contains_team(text,team):
                    out.append((day,u))
                    if len(out)>=n:return out
        return out

    @staticmethod
    def _candidate_tables(html,kind):
        try:tabs=pd.read_html(StringIO(html))
        except Exception:return []
        out=[]
        req={"AB","H","BB","SO"} if kind=="bat" else {"IP","BF","H","BB","SO","ER"}
        for d in tabs:
            cols=set(str(c).strip() for c in d.columns)
            if req.issubset(cols):out.append(d)
        return out

    def parse_team_game(self,url,team):
        try:html=self._get(url)
        except Exception as e:return {"available":False,"reason":str(e)}
        text=BeautifulSoup(html,"html.parser").get_text(" ",strip=True)
        bats=self._candidate_tables(html,"bat"); pits=self._candidate_tables(html,"pit")
        # NPB boxscores list VISITOR then HOME, whereas the title lists HOME vs VISITOR.
        teamc=canonical_english(team); short=NPB_SHORT.get(teamc,[teamc])[0]
        title=(BeautifulSoup(html,"html.parser").title.string if BeautifulSoup(html,"html.parser").title else "")
        parts=re.split(r"\s+vs\.?\s+",str(title),maxsplit=1,flags=re.I)
        if len(parts)!=2:return {"available":False,"reason":"boxscore home/visitor mapping missing","url":url}
        home_hit=self._contains_team(parts[0],teamc)
        away_hit=self._contains_team(parts[1].split("|")[0],teamc)
        if home_hit==away_hit:return {"available":False,"reason":"boxscore team mapping ambiguous","url":url}
        idx=1 if home_hit else 0
        # Identify the opposing club from the box-score title for starter-vs-team history.
        opponent=None
        other_part=parts[1].split("|")[0] if home_hit else parts[0]
        for club,aliases in NPB_SHORT.items():
            if club==teamc: continue
            if any(a.lower() in other_part.lower() for a in aliases):
                opponent=club; break
        bat=bats[idx] if len(bats)>idx else None
        pit=pits[idx] if len(pits)>idx else None

        batting={}
        if bat is not None:
            for c in ["AB","H","BB","HP","SO"]:
                batting[c]=pd.to_numeric(bat[c],errors="coerce").fillna(0).sum() if c in bat.columns else 0
            den=batting["AB"]+batting["BB"]+batting.get("HP",0)
            batting["obp_proxy"]=(batting["H"]+batting["BB"]+batting.get("HP",0))/den if den else None

        pitchers=[]
        if pit is not None:
            for _,r in pit.iterrows():
                name=_clean(r.iloc[0]) if len(r)>0 else ""
                ip=_ip(r.get("IP"))
                if not name or ip is None:continue
                bf=_num(r.get("BF"));bb=_num(r.get("BB"));so=_num(r.get("SO"));er=_num(r.get("ER"))
                pitchers.append({"name":name,"ip":ip,"bf":bf,"bb":bb,"so":so,"er":er})
        return {"available":bool(batting or pitchers),"batting":batting,"pitchers":pitchers,"url":url,"opponent":opponent}

    def _official_box_url(self, day, english_url, team):
        """Resolve the regular-season Japanese play-by-play page for exact XBH."""
        html=self._get(english_url)
        title=BeautifulSoup(html,"html.parser").title
        parts=re.split(r"\s+vs\.?\s+",title.get_text(" ",strip=True) if title else "",maxsplit=1,flags=re.I)
        if len(parts)!=2:return None
        opponents=[c for c in NPB_SHORT if any(x.lower() in parts[0].lower() for x in NPB_SHORT[c]) or any(x.lower() in parts[1].lower() for x in NPB_SHORT[c])]
        if len(opponents)!=2 or canonical_english(team) not in opponents:return None
        schedule=f"https://npb.jp/games/{day.year}/schedule_{day.month:02d}_detail.html"
        soup=BeautifulSoup(self._get(schedule),"html.parser")
        hits=[]
        for a in soup.find_all("a",href=True):
            href=urljoin(schedule,a["href"])
            if f"/scores/{day.year}/{day:%m%d}/" not in href:continue
            parent=a.find_parent("tr") or a.find_parent("li") or a.parent
            label=parent.get_text(" ",strip=True) if parent else ""
            if all(any(jp in label for jp,en in NPB_FULL_MAP.items() if en==club) for club in opponents):
                box=href.split("#")[0].split("?")[0]
                box=box if box.endswith("/box.html") else box.rstrip("/").removesuffix("/index.html")+"/box.html"
                hits.append(box)
        return hits[0] if len(set(hits))==1 else None

    def _box_batting(self,day,url,team):
        try:
            box_url=self._official_box_url(day,url,team)
            if not box_url:return None
            soup=BeautifulSoup(self._get(box_url),"html.parser")
            club=canonical_english(team)
            # The h3 game title includes both clubs; only the h4 immediately
            # above a club's batting table identifies the table owner.
            headings=[h for h in soup.find_all("h4")
                      if any(jp in h.get_text(" ",strip=True) for jp,en in NPB_FULL_MAP.items() if en==club)]
            if len(headings)!=1:return None
            table=headings[0].find_next("table")
            if table is None:return None
            header=[_clean(c.get_text(" ",strip=True)) for c in table.find("tr").find_all(["th","td"])]
            ab_i=header.index("打数"); h_i=header.index("安打")
            outcome_start=header.index("盗塁")+1
            counts={"ab":0,"h":0,"bb":0,"hp":0,"sf":0,"d2":0,"d3":0,"hr":0}
            for tr in table.find_all("tr")[1:]:
                cells=[_clean(c.get_text(" ",strip=True)) for c in tr.find_all(["th","td"],recursive=False)]
                if len(cells)<=max(ab_i,h_i,outcome_start) or "チーム計" in " ".join(cells):continue
                ab=_num(cells[ab_i]); hits=_num(cells[h_i])
                if ab is None or hits is None:continue
                counts["ab"]+=ab;counts["h"]+=hits
                for play in cells[outcome_start:]:
                    play=re.sub(r"\s+", "", play)
                    if not play or play=="-":continue
                    if "四球" in play or "敬遠" in play:counts["bb"]+=1
                    if "死球" in play:counts["hp"]+=1
                    if "犠飛" in play or "犠フ" in play:counts["sf"]+=1
                    if "本" in play:counts["hr"]+=1
                    elif "３" in play:counts["d3"]+=1
                    elif "２" in play:counts["d2"]+=1
            if counts["ab"]<20 or sum(counts[k] for k in ("d2","d3","hr"))>counts["h"]:return None
            return counts
        except Exception:return None

    def team_recent(self,team,commence_iso,n=10):
        urls=self.recent_game_urls(team,commence_iso,n=n)
        games=[self.parse_team_game(u,team) for _,u in urls]
        games=[g for g in games if g.get("available")]
        obps=[g.get("batting",{}).get("obp_proxy") for g in games if g.get("batting",{}).get("obp_proxy") is not None]
        exact=[self._box_batting(day,u,team) for day,u in urls]
        exact=[c for c in exact if c]
        sums={k:sum(c[k] for c in exact) for k in ("ab","h","d2","d3","hr","bb","hp","sf")}
        ops=_ops_from_counts(sums["ab"],sums["h"],sums["d2"],sums["d3"],sums["hr"],sums["bb"],sums["hp"],sums["sf"]) if len(exact)>=5 else None
        return {"available":bool(games),"games":len(games),
                "obp_proxy":sum(obps)/len(obps) if obps else None,
                "recent10_ops":ops,"ops_games":len(exact),"raw":games}

    @staticmethod
    def _same_pitcher(japanese, english, box_name):
        target=_norm(english or japanese)
        found=_norm(re.sub(r",\s*\([^)]*\)","",box_name or ""))
        # English player profile: "Togo, Shosei"; boxscore: "Togo, (W)".
        surname=_norm((english or "").split(",")[0])
        return bool(target and found and (target==found or target in found or found in target or (surname and (found==surname or found.startswith(surname)))))

    def starter_recent(self,team,starter,commence_iso,n=5,english_name=None):
        if not starter:return {"available":False,"reason":"starter unavailable"}
        if not english_name and re.search(r"[ぁ-んァ-ヶ一-龯]",starter):
            return {"available":False,"reason":"Japanese starter has no verified English player identity"}
        urls=self.recent_game_urls(team,commence_iso,n=15,max_days=45)
        rows=[]
        for _,u in urls:
            g=self.parse_team_game(u,team)
            pits=g.get("pitchers") or []
            if not pits:continue
            p=pits[0]
            if self._same_pitcher(starter,english_name,p.get("name")):
                rows.append(p)
                if len(rows)>=n:break
        if not rows:return {"available":False,"reason":"recent starter boxscores unavailable",
                            "velocity_delta_kmh":None,"velocity_status":"official boxscore has no pitch velocity"}
        bf=sum(x.get("bf") or 0 for x in rows);bb=sum(x.get("bb") or 0 for x in rows)
        so=sum(x.get("so") or 0 for x in rows);er=sum(x.get("er") or 0 for x in rows);ip=sum(x.get("ip") or 0 for x in rows)
        return {"available":True,"games":len(rows),"ip":ip,"bb":bb,"so":so,"er":er,"bf":bf,
                "era":9*er/ip if ip else None,"bb_pct":bb/bf if bf else None,
                "k_pct":so/bf if bf else None,"kbb_pct":(so-bb)/bf if bf else None,
                "velocity_delta_kmh":None,"velocity_status":"NPB public boxscore does not expose recent pitch velocity"}

    def starter_vs_opponent(self,team,starter,opponent,commence_iso,n=5,english_name=None):
        if not starter:
            return {"available":False,"reason":"starter unavailable","opponent":canonical_english(opponent)}
        if not english_name and re.search(r"[ぁ-んァ-ヶ一-龯]",starter):
            return {"available":False,"reason":"Japanese starter has no verified English player identity","opponent":canonical_english(opponent)}
        urls=self.recent_game_urls(team,commence_iso,n=40,max_days=120)
        rows=[]
        target=canonical_english(opponent)
        for _,u in urls:
            g=self.parse_team_game(u,team)
            if not _same_team(g.get("opponent"),target):
                continue
            pits=g.get("pitchers") or []
            if not pits: continue
            p=pits[0]
            if self._same_pitcher(starter,english_name,p.get("name")):
                rows.append(p)
                if len(rows)>=int(n): break
        if not rows:
            return {"available":False,"reason":"no recent starts vs opponent in official boxscores","opponent":target}
        bf=sum(x.get("bf") or 0 for x in rows);bb=sum(x.get("bb") or 0 for x in rows)
        so=sum(x.get("so") or 0 for x in rows);er=sum(x.get("er") or 0 for x in rows);ip=sum(x.get("ip") or 0 for x in rows)
        return {"available":True,"games":len(rows),"ip":ip,"bb":bb,"so":so,"er":er,"bf":bf,
                "era":9*er/ip if ip else None,"bb_pct":bb/bf if bf else None,
                "k_pct":so/bf if bf else None,"kbb_pct":(so-bb)/bf if bf else None,
                "opponent":target,"sample_scope":"recent official boxscores"}

    def bullpen(self,team,commence_iso):
        urls=self.recent_game_urls(team,commence_iso,n=3,max_days=6)
        total_ip=0;apps=0;consec=0;parsed_games=0
        for _,u in urls:
            g=self.parse_team_game(u,team)
            pits=g.get("pitchers") or []
            if not g.get("available") or not pits:
                continue
            parsed_games+=1
            rel=pits[1:] if len(pits)>1 else []
            if rel:
                apps+=len(rel);total_ip+=sum(p.get("ip") or 0 for p in rel);consec+=1
        if not urls or parsed_games==0:
            return {"available":False,"exact":False,"reason":"recent NPB bullpen boxscores unavailable"}
        # high score around 8+ relief innings over last three successfully parsed team games
        score=max(0,min(1,total_ip/9.0 + .05*max(0,apps-9)))
        return {"available":True,"exact":True,"games_last3":parsed_games,"relief_ip_last3":total_ip,"reliever_apps_last3":apps,"score":score}

    def enrich(self,home,away,commence_iso,ctx,recent_n=10):
        hrecent=self.team_recent(home,commence_iso,recent_n)
        arecent=self.team_recent(away,commence_iso,recent_n)
        year=_kst_dt(commence_iso).year
        def season_ops(team):
            try:return self.live._batting(canonical_english(team),year)[1] if self.live else None
            except Exception:return None
        return {
            "source":"NPB official recent boxscores",
            "home_recent":hrecent,"away_recent":arecent,
            "home_starter_recent":self.starter_recent(home,ctx.get("home_starter"),commence_iso,5,ctx.get("home_starter_english")),
            "away_starter_recent":self.starter_recent(away,ctx.get("away_starter"),commence_iso,5,ctx.get("away_starter_english")),
            "home_starter_vs_opponent":self.starter_vs_opponent(home,ctx.get("home_starter"),away,commence_iso,5,ctx.get("home_starter_english")),
            "away_starter_vs_opponent":self.starter_vs_opponent(away,ctx.get("away_starter"),home,commence_iso,5,ctx.get("away_starter_english")),
            "home_bullpen":self.bullpen(home,commence_iso),
            "away_bullpen":self.bullpen(away,commence_iso),
            # Exact public NPB L/R OPS split was not found in stable official tables.
            "home_lineup_form":{"available":hrecent.get("recent10_ops") is not None,"recent10_ops":hrecent.get("recent10_ops"),"season_ops":season_ops(home),"ops_games":hrecent.get("ops_games"),"split_exact":False,"reason":"recent team OPS; lineup-specific split unavailable"},
            "away_lineup_form":{"available":arecent.get("recent10_ops") is not None,"recent10_ops":arecent.get("recent10_ops"),"season_ops":season_ops(away),"ops_games":arecent.get("ops_games"),"split_exact":False,"reason":"recent team OPS; lineup-specific split unavailable"},
        }


class AdvancedBaseballSignals:
    def __init__(self, live_context, timeout=15):
        self.live=live_context
        self.weather=WeatherProvider(timeout)
        self.kbo=KBOAdvanced(live_context.kbo,timeout)
        self.npb=NPBRecent(timeout,live_context.npb)

    @staticmethod
    def _starter_factor(recent,season):
        if not recent or not recent.get("available"):return None
        fac=1.0
        re=recent.get("era");se=(season or {}).get("era")
        if re is not None and se not in (None,0):
            fac*=max(.96,min(1.04,(float(re)/float(se))**.12))
        kbb=recent.get("kbb_pct")
        if kbb is not None:
            # better K-BB% -> suppress opponent offense
            fac*=max(.97,min(1.03,1-(float(kbb)-.12)*.20))
        vd=recent.get("velocity_delta_kmh")
        if vd is not None:
            fac*=max(.97,min(1.03,1-float(vd)*.012))
        return max(.94,min(1.06,fac))

    @staticmethod
    def _starter_vs_opponent_factor(matchup, season):
        """Shrink starter-vs-team history hard; small samples must never dominate."""
        if not matchup or not matchup.get("available"):
            return None
        me=matchup.get("era"); se=(season or {}).get("era")
        ip=float(matchup.get("ip") or 0)
        if me is None or se in (None,0) or ip<=0:
            return None
        reliability=max(.15,min(1.0,ip/24.0))
        ratio=max(.40,min(2.50,float(me)/float(se)))
        raw=math.exp(.16*reliability*math.log(ratio))
        return max(.955,min(1.045,raw))

    @staticmethod
    def _recent_runs_factor(recent, season_for, opponent_recent=None, opponent_season_ra=None):
        if not recent or not recent.get("available") or not season_for:
            return None
        rf=recent.get("runs_for_per_game")
        if rf is None or float(season_for)<=0:
            return None
        ratios=[max(.35,min(2.50,float(rf)/float(season_for)))]
        if opponent_recent and opponent_recent.get("available") and opponent_season_ra:
            ora=opponent_recent.get("runs_against_per_game")
            if ora is not None and float(opponent_season_ra)>0:
                ratios.append(max(.35,min(2.50,float(ora)/float(opponent_season_ra))))
        ratio=math.exp(sum(math.log(x) for x in ratios)/len(ratios))
        return _factor_from_ratio(ratio,.045,.28)

    @staticmethod
    def _lineup_form_factor(d):
        if not d or not d.get("available"):return None
        r=d.get("recent10_ops");s=d.get("season_ops")
        return _factor_from_ratio(r/s if r and s else None,.04,.35)

    @staticmethod
    def _split_factor(d):
        if not d or not d.get("available"):return None
        sp=d.get("split_ops");s=d.get("season_ops")
        return _factor_from_ratio(sp/s if sp and s else None,.04,.30)

    def collect(self,league,home,away,commence_iso,base_ctx,recent_n=10,team_stats=None):
        lg=str(league).upper()
        if lg=="KBO":
            data=self.kbo.enrich(home,away,commence_iso,base_ctx,recent_n)
        else:
            data=self.npb.enrich(home,away,commence_iso,base_ctx,recent_n)

        weather=self.weather.get(home,commence_iso)
        data["weather"]=weather

        # Convert raw signals into conservative scoring multipliers.
        hsf=self._starter_factor(data.get("home_starter_recent"),base_ctx.get("home_starter_stats"))
        asf=self._starter_factor(data.get("away_starter_recent"),base_ctx.get("away_starter_stats"))
        hsv=self._starter_vs_opponent_factor(data.get("home_starter_vs_opponent"),base_ctx.get("home_starter_stats"))
        asv=self._starter_vs_opponent_factor(data.get("away_starter_vs_opponent"),base_ctx.get("away_starter_stats"))
        hform=self._lineup_form_factor(data.get("home_lineup_form"))
        aform=self._lineup_form_factor(data.get("away_lineup_form"))
        hsplit=self._split_factor(data.get("home_lineup_form"))
        asplit=self._split_factor(data.get("away_lineup_form"))

        # Before lineups are posted, exact recent KBO team scores still provide a measured form signal.
        ts=team_stats or {}
        hs=ts.get(canonical_english(home)) or {}; aws=ts.get(canonical_english(away)) or {}
        hrun=self._recent_runs_factor(data.get("home_recent"),hs.get("runs_per_game"),data.get("away_recent"),aws.get("runs_allowed_per_game"))
        arun=self._recent_runs_factor(data.get("away_recent"),aws.get("runs_per_game"),data.get("home_recent"),hs.get("runs_allowed_per_game"))
        if hrun is not None:
            hform=math.exp((.60*math.log(hform)+.40*math.log(hrun))) if hform is not None else hrun
        if arun is not None:
            aform=math.exp((.60*math.log(aform)+.40*math.log(arun))) if aform is not None else arun

        # NPB recent boxscore OBP proxy is supporting evidence.  Preserve the
        # stronger recent OPS + runs signal already computed above and blend this
        # only as a small nudge; older code overwrote hform/aform completely.
        if lg=="NPB":
            hr=(data.get("home_recent") or {}).get("obp_proxy")
            ar=(data.get("away_recent") or {}).get("obp_proxy")
            if hr is not None and ar is not None:
                avg=(hr+ar)/2
                if avg>0:
                    hobp=max(.97,min(1.03,1+(hr/avg-1)*.20))
                    aobp=max(.97,min(1.03,1+(ar/avg-1)*.20))
                    hform=math.exp(.80*math.log(hform)+.20*math.log(hobp)) if hform is not None else hobp
                    aform=math.exp(.80*math.log(aform)+.20*math.log(aobp)) if aform is not None else aobp

        home_bp=data.get("home_bullpen") or {}
        away_bp=data.get("away_bullpen") or {}
        hbp=home_bp.get("score") if home_bp.get("available") else None
        abp=away_bp.get("score") if away_bp.get("available") else None
        # A fatigued opponent bullpen increases your offense. Exact pitch-count logs get full weight;
        # schedule-only proxy is deliberately half-strength.
        hcap=.04 if home_bp.get("exact") else .02
        acap=.04 if away_bp.get("exact") else .02
        home_vs_bullpen=1+min(acap,acap*float(abp)) if abp is not None else None
        away_vs_bullpen=1+min(hcap,hcap*float(hbp)) if hbp is not None else None

        wf=weather.get("run_factor") if weather.get("available") else None

        components={
            "home_recent_form_factor":hform,
            "away_recent_form_factor":aform,
            "home_split_factor":hsplit,
            "away_split_factor":asplit,
            # home starter affects AWAY offense and vice versa
            "home_starter_recent_factor":hsf,
            "away_starter_recent_factor":asf,
            # starter history vs today's opponent affects the OPPOSING offense
            "home_starter_vs_opponent_factor":hsv,
            "away_starter_vs_opponent_factor":asv,
            "home_vs_bullpen_factor":home_vs_bullpen,
            "away_vs_bullpen_factor":away_vs_bullpen,
            "weather_factor":wf,
        }
        data["components"]=components

        statuses={
            "recent_form": bool(hform is not None and aform is not None),
            "starter_recent": bool(hsf is not None and asf is not None),
            "starter_vs_opponent": bool(hsv is not None and asv is not None),
            "velocity": bool(
                (data.get("home_starter_recent") or {}).get("velocity_delta_kmh") is not None
                and (data.get("away_starter_recent") or {}).get("velocity_delta_kmh") is not None
            ),
            "bullpen": bool(hbp is not None and abp is not None),
            "bullpen_exact": bool(home_bp.get("exact") and away_bp.get("exact")),
            "split": bool(hsplit is not None and asplit is not None),
            "weather": bool(weather.get("available")),
            "lineup": bool(base_ctx.get("lineup_confirmed")),
        }
        data["statuses"]=statuses
        used=sum(statuses.values())
        data["advanced_used"]=used
        data["advanced_total"]=len(statuses)
        data["advanced_completeness"]=used/len(statuses)
        # Missing factors increase uncertainty, never silently become neutral confidence.
        data["extra_uncertainty_pp"]=(len(statuses)-used)*.30 + float(weather.get("uncertainty_pp") or 0)
        if lg=="KBO" and statuses.get("bullpen_exact"):
            data.setdefault("notes",[]).append("KBO 불펜: 최근 3일 GameCenter 공식 박스스코어의 구원투수 IP/투구수 실제 반영")
        elif lg=="KBO" and statuses.get("bullpen"):
            data.setdefault("notes",[]).append("KBO 불펜 정확 박스스코어 실패: 최근 일정 대리지표를 절반 가중치로 사용")
        if statuses.get("starter_vs_opponent"):
            data.setdefault("notes",[]).append("양 선발의 오늘 상대팀 상대 기록을 소표본 축소 후 반영")
        return data
