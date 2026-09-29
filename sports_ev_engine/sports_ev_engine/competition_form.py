
from __future__ import annotations
import re, unicodedata
from datetime import datetime, timezone

def norm_name(s):
    s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower()
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    s=re.sub(r"\s+"," ",s).strip()
    aliases={
        "republic of ireland":"ireland",
        "korea republic":"south korea",
        "korea dpr":"north korea",
        "czech republic":"czechia",
    }
    return aliases.get(s,s)

def _score_name(query, candidate):
    q=norm_name(query); c=norm_name(candidate)
    if q==c: return 100
    score=0
    qset=set(q.split()); cset=set(c.split())
    if q in c or c in q: score+=45
    score += 8*len(qset & cset)
    return score

def resolve_league(search_results, desired_name):
    if not search_results:
        return None
    scored=[]
    for item in search_results:
        league=item.get("league",{})
        score=_score_name(desired_name, league.get("name",""))
        if norm_name(league.get("name",""))==norm_name(desired_name):
            score+=100
        # World competitions fit international leagues.
        if str(item.get("country",{}).get("name","")).lower()=="world":
            score+=5
        scored.append((score,item))
    scored.sort(key=lambda x:x[0], reverse=True)
    return scored[0][1] if scored else None

def available_seasons(league_item):
    seasons=[]
    for s in league_item.get("seasons",[]):
        try:
            seasons.append(int(s["year"]))
        except Exception:
            pass
    return sorted(set(seasons), reverse=True)

def completed_fixture(fx):
    st=fx.get("fixture",{}).get("status",{}).get("short")
    goals=fx.get("goals",{})
    return st in {"FT","AET","PEN"} and goals.get("home") is not None and goals.get("away") is not None

def fixture_before(fx, cutoff_iso):
    if not cutoff_iso:
        return True
    try:
        cutoff=datetime.fromisoformat(str(cutoff_iso).replace("Z","+00:00"))
        dt=datetime.fromtimestamp(int(fx.get("fixture",{}).get("timestamp",0)), tz=timezone.utc)
        return dt < cutoff.astimezone(timezone.utc)
    except Exception:
        return True

def team_recent_form_from_pool(fixtures, team_name, cutoff_iso=None, recent_n=6, decay=0.86):
    tn=norm_name(team_name)
    matches=[]
    for fx in fixtures:
        if not completed_fixture(fx) or not fixture_before(fx, cutoff_iso):
            continue
        teams=fx.get("teams",{})
        home=teams.get("home",{}).get("name","")
        away=teams.get("away",{}).get("name","")
        if norm_name(home)==tn or norm_name(away)==tn:
            matches.append(fx)
    matches.sort(key=lambda fx:fx.get("fixture",{}).get("timestamp",0), reverse=True)
    matches=matches[:int(recent_n)]
    if not matches:
        return None

    gf=ga=pts=w=0.0
    for i,fx in enumerate(matches):
        weight=decay**i
        teams=fx["teams"]; goals=fx["goals"]
        home=norm_name(teams["home"]["name"]); away=norm_name(teams["away"]["name"])
        hg=goals["home"]; ag=goals["away"]
        if home==tn:
            tgf,tga=hg,ag
        else:
            tgf,tga=ag,hg
        gf+=weight*tgf
        ga+=weight*tga
        pts+=weight*(3 if tgf>tga else 1 if tgf==tga else 0)
        w+=weight
    return {"gf":gf/w,"ga":ga/w,"ppg":pts/w,"matches":len(matches)}

def build_competition_pool(api, league_name, target_year):
    """
    Resolve competition once, then fetch current + previous available season.
    This is dramatically cheaper and more reliable than per-team recent fixture calls.
    """
    results=api.search_leagues(league_name)
    item=resolve_league(results, league_name)
    if not item:
        raise RuntimeError(f"competition mapping failed: {league_name}")

    league_id=item["league"]["id"]
    seasons=available_seasons(item)
    # prefer target year, then nearest earlier season
    ordered=[]
    if int(target_year) in seasons:
        ordered.append(int(target_year))
    for s in seasons:
        if s not in ordered and s <= int(target_year):
            ordered.append(s)
    for s in seasons:
        if s not in ordered:
            ordered.append(s)

    picked=ordered[:2] if ordered else [int(target_year)]
    pool=[]
    used=[]
    for season in picked:
        try:
            rows=api.league_fixtures(league_id, season)
        except Exception:
            continue
        if rows:
            pool.extend(rows)
            used.append(season)

    return {
        "league_id": league_id,
        "league_name": item["league"]["name"],
        "seasons": used,
        "fixtures": pool,
    }
