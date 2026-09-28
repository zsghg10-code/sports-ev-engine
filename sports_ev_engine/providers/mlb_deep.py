from __future__ import annotations

"""High-detail MLB context layer for Sports EV Engine.

Design goals:
- Use real MLB/Statcast/market observations only; never backfill missing fields with fake league averages.
- Keep every signal auditable and fail-soft.
- Avoid double-counting: low-confidence/context-only signals are exposed without always moving expected runs.
- Persist lineup/market observations so changes can be detected between analyses.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET
import hashlib
import json
import math
import re

import pandas as pd
import requests

SAVANT_BASE = "https://baseballsavant.mlb.com"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

_SWING_CODES = {"S", "W", "T", "F", "X", "D", "E"}
_MISS_CODES = {"S", "W", "T"}
_CONTACT_CODES = {"F", "X", "D", "E"}

_PITCH_NAME = {
    "FF":"4-Seam Fastball","SI":"Sinker","FC":"Cutter","CH":"Changeup","FS":"Split-Finger",
    "FO":"Forkball","CU":"Curveball","KC":"Knuckle Curve","CS":"Slow Curve","SL":"Slider",
    "ST":"Sweeper","SV":"Slurve","KN":"Knuckleball","EP":"Eephus","FA":"Other",
}

def _pitch_key(v):
    x=str(v or "").strip()
    if x in _PITCH_NAME:return _norm(_PITCH_NAME[x])
    return _norm(x)


def _num(v):
    try:
        if v in (None, "", "-", "--", "null"):
            return None
        x = float(str(v).replace("%", "").replace(",", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _namekey(s):
    toks=re.findall(r"[a-z0-9]+",str(s or "").lower())
    return "".join(sorted(toks))


def _date(v):
    try:
        return pd.Timestamp(v).date()
    except Exception:
        return None


def _utc(v):
    try:
        t = pd.Timestamp(v)
        return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
    except Exception:
        return None


def _clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


def _pct01(v):
    """Normalize percent-looking values to 0-1 where possible."""
    x = _num(v)
    if x is None:
        return None
    return x / 100.0 if abs(x) > 1.5 else x


def _pick_col(df: pd.DataFrame, *candidates):
    if df is None or df.empty:
        return None
    norm = {_norm(c): c for c in df.columns}
    for candidate in candidates:
        n = _norm(candidate)
        if n in norm:
            return norm[n]
    # fuzzy suffix/contains for Savant's occasionally decorated headers
    for candidate in candidates:
        n = _norm(candidate)
        hits = [orig for k, orig in norm.items() if n and (n in k or k in n)]
        if len(hits) == 1:
            return hits[0]
    return None


def _weighted_mean(values, weights):
    pairs = [(float(v), float(w)) for v, w in zip(values, weights) if v is not None and w is not None and float(w) > 0]
    if not pairs:
        return None
    den = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / den if den else None


def _haversine_miles(lat1, lon1, lat2, lon2):
    vals = [_num(x) for x in (lat1, lon1, lat2, lon2)]
    if any(x is None for x in vals):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, vals)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.7613 * 2 * math.asin(math.sqrt(a))


def _event_key(row):
    point = row.get("point")
    point_s = "" if point is None or (isinstance(point, float) and math.isnan(point)) else f"{float(point):g}"
    return f"{row.get('market')}|{row.get('selection')}|{point_s}"


class SavantClient:
    """Small, cached Baseball Savant client.

    Statcast's public CSV endpoints occasionally change column labels or time out.
    Every parser therefore uses fuzzy column lookup and returns MISSING rather than
    inventing values.
    """

    def __init__(self, session=None, timeout=18):
        self.s = session or requests.Session()
        self.timeout = timeout
        self.cache = {}

    def _csv(self, url, params=None):
        key = (url, tuple((k, tuple(v) if isinstance(v, (list, tuple)) else v) for k, v in sorted((params or {}).items())))
        if key in self.cache:
            return self.cache[key].copy()
        r = self.s.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        text = r.text.strip()
        if not text:
            df = pd.DataFrame()
        else:
            df = pd.read_csv(StringIO(text))
        self.cache[key] = df
        return df.copy()

    def custom(self, season: int, player_type: str):
        selections = (
            "pa,k_percent,bb_percent,woba,xwoba,barrel_batted_rate,hard_hit_percent,"
            "whiff_percent,swing_percent,z_swing_percent,oz_swing_percent,oz_swing_miss_percent,"
            "in_zone_percent,gb_percent,fb_percent,babip,home_run,gb,fb"
        )
        params = {
            "year": int(season), "type": player_type, "min": 1, "r": "no",
            "selections": selections, "chart": "false", "csv": "true",
        }
        return self._csv(f"{SAVANT_BASE}/leaderboard/custom", params)

    def arsenal(self, season: int, player_type: str):
        params = {
            "type": player_type, "year": int(season), "min": 1,
            "minPitches": 1, "csv": "true",
        }
        return self._csv(f"{SAVANT_BASE}/leaderboard/pitch-arsenal-stats", params)

    def raw_player(self, *, player_id: int, player_type: str, start_date, end_date):
        lookup = "pitchers_lookup[]" if player_type == "pitcher" else "batters_lookup[]"
        params = {
            "all": "true", "type": "details", "hfGT": "R|PO|", "player_type": player_type,
            "game_date_gt": str(start_date), "game_date_lt": str(end_date),
            lookup: str(int(player_id)), "min_pitches": 0, "min_results": 0,
            "group_by": "name", "sort_col": "pitches", "sort_order": "desc",
        }
        return self._csv(f"{SAVANT_BASE}/statcast_search/csv", params)

    def raw_batters(self, *, player_ids, start_date, end_date):
        ids = [str(int(x)) for x in player_ids if x]
        if not ids:
            return pd.DataFrame()
        # requests serializes repeated list values into repeated batters_lookup[] params.
        params = [
            ("all", "true"), ("type", "details"), ("hfGT", "R|PO|"), ("player_type", "batter"),
            ("game_date_gt", str(start_date)), ("game_date_lt", str(end_date)),
            ("min_pitches", "0"), ("min_results", "0"), ("group_by", "name"),
        ] + [("batters_lookup[]", x) for x in ids]
        key = ("raw_batters", tuple(params))
        if key in self.cache:
            return self.cache[key].copy()
        r = self.s.get(f"{SAVANT_BASE}/statcast_search/csv", params=params, timeout=self.timeout)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text)) if r.text.strip() else pd.DataFrame()
        self.cache[key] = df
        return df.copy()

    @staticmethod
    def player_row(df, player_id=None, name=None):
        if df is None or df.empty:
            return None
        id_col = _pick_col(df, "player_id", "playerid", "id")
        if id_col and player_id is not None:
            vals = pd.to_numeric(df[id_col], errors="coerce")
            hit = df[vals == int(player_id)]
            if not hit.empty:
                return hit.iloc[0]
        name_col = _pick_col(df, "player_name", "player", "name", "last_name, first_name")
        if name_col and name:
            nn = _norm(name); nk=_namekey(name)
            hit = df[df[name_col].map(lambda v: (_norm(v)==nn or nn in _norm(v) or _norm(v) in nn or _namekey(v)==nk))]
            if len(hit) == 1:
                return hit.iloc[0]
        return None

    @staticmethod
    def profile_from_row(row):
        if row is None:
            return {"available": False, "reason": "Statcast leaderboard row unavailable"}
        def val(*names, pct=False):
            for name in names:
                for col in row.index:
                    if _norm(name) == _norm(col) or _norm(name) in _norm(col):
                        x = _num(row[col])
                        if x is not None:
                            return x / 100 if pct and abs(x) > 1.5 else x
            return None
        return {
            "available": True,
            "pa_or_bf": val("pa", "bf"),
            "k_pct": val("k_percent", "k%", pct=True),
            "bb_pct": val("bb_percent", "bb%", pct=True),
            "woba": val("woba"),
            "xwoba": val("xwoba"),
            "barrel_pct": val("barrel_batted_rate", "barrel%", pct=True),
            "hardhit_pct": val("hard_hit_percent", "hard hit %", pct=True),
            "whiff_pct": val("whiff_percent", "whiff %", pct=True),
            "swing_pct": val("swing_percent", "swing %", pct=True),
            "zone_swing_pct": val("z_swing_percent", "zone swing %", pct=True),
            "chase_pct": val("oz_swing_percent", "out of zone swing %", pct=True),
            "chase_miss_pct": val("oz_swing_miss_percent", "out of zone swing & miss", pct=True),
            "zone_pct": val("in_zone_percent", "in zone %", pct=True),
            "gb_pct": val("gb_percent", "gb%", pct=True),
            "fb_pct": val("fb_percent", "fb%", pct=True),
            "babip": val("babip"),
            "home_runs": val("home_run", "home runs", "hr"),
            "fly_balls": val("fb", "fly balls"),
            "source": "Baseball Savant custom leaderboard",
        }

    def player_profile(self, season, player_type, player_id=None, name=None):
        try:
            df = self.custom(season, player_type)
            row = self.player_row(df, player_id, name)
            return self.profile_from_row(row)
        except Exception as e:
            return {"available": False, "reason": f"Statcast custom unavailable: {e}"}

    def player_arsenal(self, season, player_type, player_id=None, name=None):
        try:
            df = self.arsenal(season, player_type)
        except Exception as e:
            return {"available": False, "reason": f"pitch arsenal unavailable: {e}", "rows": []}
        if df.empty:
            return {"available": False, "reason": "pitch arsenal empty", "rows": []}
        id_col = _pick_col(df, "player_id", "playerid", "id")
        name_col = _pick_col(df, "player_name", "player", "name")
        hit = pd.DataFrame()
        if id_col and player_id is not None:
            vals = pd.to_numeric(df[id_col], errors="coerce")
            hit = df[vals == int(player_id)]
        if hit.empty and name_col and name:
            nn = _norm(name); nk=_namekey(name)
            hit = df[df[name_col].map(lambda v: (_norm(v)==nn or nn in _norm(v) or _norm(v) in nn or _namekey(v)==nk))]
        if hit.empty:
            return {"available": False, "reason": "pitch arsenal player row unavailable", "rows": []}
        pitch_col = _pick_col(hit, "pitch", "pitch type", "pitch_type")
        usage_col = _pick_col(hit, "%", "usage", "pitch %", "pitch_percent")
        rv_col = _pick_col(hit, "rv/100", "run value / 100 pitches", "run_value_per_100")
        whiff_col = _pick_col(hit, "whiff %", "whiff_percent")
        xwoba_col = _pick_col(hit, "xwoba")
        hard_col = _pick_col(hit, "hard hit %", "hardhit_percent")
        rows = []
        for _, r in hit.iterrows():
            usage = _num(r.get(usage_col)) if usage_col else None
            if usage is not None and usage > 1.5:
                usage /= 100
            wh = _num(r.get(whiff_col)) if whiff_col else None
            if wh is not None and wh > 1.5:
                wh /= 100
            hh = _num(r.get(hard_col)) if hard_col else None
            if hh is not None and hh > 1.5:
                hh /= 100
            rows.append({
                "pitch_type": str(r.get(pitch_col) if pitch_col else ""),
                "usage": usage, "rv100": _num(r.get(rv_col)) if rv_col else None,
                "whiff_pct": wh, "xwoba": _num(r.get(xwoba_col)) if xwoba_col else None,
                "hardhit_pct": hh,
            })
        rows = [x for x in rows if x["pitch_type"]]
        # This is explicitly a proxy, not FanGraphs/PitchingBot Stuff+.
        usable = [x for x in rows if x.get("usage") is not None]
        proxy_vals = []
        proxy_w = []
        for x in usable:
            score = 0.0
            used = 0
            if x.get("whiff_pct") is not None:
                score += (x["whiff_pct"] - .25) * 2.0; used += 1
            if x.get("xwoba") is not None:
                score += (.320 - x["xwoba"]) * 2.0; used += 1
            if x.get("rv100") is not None:
                score += _clamp(x["rv100"] / 8.0, -.5, .5); used += 1
            if used:
                x["quality_proxy"]=score/used
                proxy_vals.append(x["quality_proxy"])
                proxy_w.append(x["usage"])
        return {
            "available": bool(rows), "rows": rows,
            "stuff_proxy": _weighted_mean(proxy_vals, proxy_w),
            "note": "arsenal quality proxy from Savant pitch-type RV/100, Whiff%, xwOBA; not official Stuff+",
            "source": "Baseball Savant pitch arsenal",
        }


def pitch_discipline_from_pbp(base, game_pks, pitcher_id=None):
    pitches = []
    for gp in (game_pks or [])[-5:]:
        try:
            data = base._get(f"/game/{int(gp)}/playByPlay")
        except Exception:
            continue
        for play in data.get("allPlays", []):
            matchup = play.get("matchup") or {}
            if pitcher_id and int(((matchup.get("pitcher") or {}).get("id") or -1)) != int(pitcher_id):
                continue
            for ev in play.get("playEvents", []):
                if not ev.get("isPitch"):
                    continue
                details = ev.get("details") or {}
                pdx = ev.get("pitchData") or {}
                code = str(details.get("code") or "")
                zone = _num(pdx.get("zone"))
                ptype = ((details.get("type") or {}).get("code") or "")
                pitches.append({
                    "code": code, "zone": zone, "pitch_type": ptype,
                    "speed": _num(pdx.get("startSpeed")),
                    "spin": _num(((pdx.get("breaks") or {}).get("spinRate"))),
                })
    if not pitches:
        return {"available": False, "reason": "recent pitch-by-pitch unavailable"}
    swings = sum(1 for p in pitches if p["code"] in _SWING_CODES)
    misses = sum(1 for p in pitches if p["code"] in _MISS_CODES)
    contacts = sum(1 for p in pitches if p["code"] in _CONTACT_CODES)
    zone_p = [p for p in pitches if p["zone"] is not None]
    in_zone = [p for p in zone_p if 1 <= p["zone"] <= 9]
    out_zone = [p for p in zone_p if not (1 <= p["zone"] <= 9)]
    chase = sum(1 for p in out_zone if p["code"] in _SWING_CODES)
    by_type = {}
    for p in pitches:
        pt = p["pitch_type"] or "UNK"
        d = by_type.setdefault(pt, {"pitches": 0, "swings": 0, "misses": 0, "speed": []})
        d["pitches"] += 1
        d["swings"] += int(p["code"] in _SWING_CODES)
        d["misses"] += int(p["code"] in _MISS_CODES)
        if p["speed"] is not None:
            d["speed"].append(p["speed"])
    mix = []
    for pt, d in by_type.items():
        mix.append({
            "pitch_type": pt, "usage": d["pitches"] / len(pitches),
            "whiff_pct": d["misses"] / d["swings"] if d["swings"] else None,
            "velo": sum(d["speed"]) / len(d["speed"]) if d["speed"] else None,
        })
    return {
        "available": True, "pitches": len(pitches),
        "whiff_pct": misses / swings if swings else None,
        "contact_pct": contacts / swings if swings else None,
        "zone_pct": len(in_zone) / len(zone_p) if zone_p else None,
        "chase_pct": chase / len(out_zone) if out_zone else None,
        "pitch_mix_recent": sorted(mix, key=lambda x: x["usage"], reverse=True),
        "source": "MLB Stats API play-by-play",
    }


def statcast_raw_batted_ball(df: pd.DataFrame):
    if df is None or df.empty:
        return {"available": False, "reason": "Statcast raw rows unavailable"}
    ev_col = _pick_col(df, "events")
    ls_col = _pick_col(df, "launch_speed")
    lsa_col = _pick_col(df, "launch_speed_angle")
    bb_col = _pick_col(df, "bb_type")
    if not ev_col and not ls_col:
        return {"available": False, "reason": "Statcast batted-ball columns unavailable"}
    bbe = df[pd.to_numeric(df[ls_col], errors="coerce").notna()].copy() if ls_col else pd.DataFrame()
    n = len(bbe)
    hard = None
    barrel = None
    gb = None
    fb = None
    if n:
        ls = pd.to_numeric(bbe[ls_col], errors="coerce")
        hard = float((ls >= 95).mean())
        if lsa_col:
            lsa = pd.to_numeric(bbe[lsa_col], errors="coerce")
            barrel = float((lsa == 6).mean()) if lsa.notna().any() else None
        if bb_col:
            types = bbe[bb_col].astype(str).str.lower()
            gb = float((types == "ground_ball").mean())
            fb = float(types.isin(["fly_ball", "popup"]).mean())
    # BABIP + HR/FB from terminal PA events.
    events = df[ev_col].dropna().astype(str).str.lower() if ev_col else pd.Series(dtype=str)
    if not events.empty:
        # one events marker per PA; dedupe repeated null-less event rows defensively by pitch/PA if columns exist
        pa = df[df[ev_col].notna()].copy()
        at_col = _pick_col(pa, "at_bat_number")
        game_col = _pick_col(pa, "game_pk")
        if at_col and game_col:
            pa = pa.drop_duplicates([game_col, at_col], keep="last")
        ev = pa[ev_col].astype(str).str.lower()
        hits = ev.isin(["single", "double", "triple", "home_run"]).sum()
        hrs = (ev == "home_run").sum()
        ks = ev.isin(["strikeout", "strikeout_double_play"]).sum()
        sf = (ev == "sac_fly").sum()
        abs_ = (~ev.isin(["walk", "hit_by_pitch", "sac_bunt", "sac_fly", "catcher_interf"])).sum()
        denom = abs_ - ks - hrs + sf
        babip = (hits - hrs) / denom if denom > 0 else None
        fly = int(((bbe[bb_col].astype(str).str.lower().isin(["fly_ball", "popup"])).sum()) if n and bb_col else 0)
        hrfb = hrs / fly if fly else None
    else:
        babip = None; hrfb = None; hrs = None
    return {
        "available": bool(n or not events.empty), "bbe": n,
        "hardhit_pct": hard, "barrel_pct": barrel, "gb_pct": gb, "fb_pct": fb,
        "babip": babip, "hr_fb": hrfb, "home_runs": hrs,
        "source": "Baseball Savant pitch-level Statcast",
    }


def lineup_hash(lineup):
    ids = [(str(x.get("player_id") or x.get("name") or ""), str(x.get("order") or "")) for x in (lineup or [])]
    if not ids:
        return None
    return hashlib.sha256(json.dumps(ids, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


class JSONState:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:
            return {}

    def save(self, obj):
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass


class MLBDeepContext:
    def __init__(self, base_provider, timeout=18, data_dir="data"):
        self.base = base_provider
        self.timeout = timeout
        self.s = base_provider.s
        self.savant = SavantClient(self.s, timeout=timeout)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.venue_cache = {}
        self.live_cache = {}

    def _venue(self, venue_id):
        if not venue_id:
            return {}
        if int(venue_id) in self.venue_cache:
            return self.venue_cache[int(venue_id)]
        try:
            data = self.base._get(f"/venues/{int(venue_id)}")
            v = (data.get("venues") or [{}])[0]
            loc = v.get("location") or {}
            c = loc.get("defaultCoordinates") or {}
            out = {
                "id": venue_id, "name": v.get("name"),
                "lat": _num(c.get("latitude")), "lon": _num(c.get("longitude")),
                "roof_type": str((v.get("fieldInfo") or {}).get("roofType") or ""),
                "timezone": str(((v.get("timeZone") or {}).get("id")) or ((loc.get("timeZone") or {}).get("id")) or ""),
            }
        except Exception:
            out = {}
        self.venue_cache[int(venue_id)] = out
        return out

    def statcast_pitcher(self, pitcher_id, pitcher_name, season, kickoff, recent_game_pks):
        season_profile = self.savant.player_profile(season, "pitcher", pitcher_id, pitcher_name)
        discipline = pitch_discipline_from_pbp(self.base, recent_game_pks, pitcher_id)
        arsenal = self.savant.player_arsenal(season, "pitcher", pitcher_id, pitcher_name)
        recent_raw = {"available": False, "reason": "not attempted"}
        # Season regression anchors come from the compact Savant custom leaderboard;
        # only the recent 35-day pitch-level window is downloaded here.
        season_raw = {
            "available": bool(season_profile.get("available")),
            "hardhit_pct": season_profile.get("hardhit_pct"),
            "barrel_pct": season_profile.get("barrel_pct"),
            "gb_pct": season_profile.get("gb_pct"),
            "fb_pct": season_profile.get("fb_pct"),
            "babip": season_profile.get("babip"),
            "hr_fb": (season_profile.get("home_runs")/season_profile.get("fly_balls")
                      if season_profile.get("home_runs") is not None and season_profile.get("fly_balls") not in (None,0) else None),
            "source": "Baseball Savant custom leaderboard",
        }
        try:
            end = (_utc(kickoff) - timedelta(days=1)).date()
            start_recent = end - timedelta(days=35)
            rdf = self.savant.raw_player(player_id=pitcher_id, player_type="pitcher", start_date=start_recent, end_date=end)
            recent_raw = statcast_raw_batted_ball(rdf)
        except Exception as e:
            recent_raw = {"available": False, "reason": f"recent Statcast raw unavailable: {e}"}
        usage_change=[]; usage_quality_delta=None
        if discipline.get("available") and arsenal.get("available"):
            sea={_pitch_key(x.get("pitch_type")):x.get("usage") for x in arsenal.get("rows",[]) if x.get("usage") is not None}
            quality={_pitch_key(x.get("pitch_type")):x.get("quality_proxy") for x in arsenal.get("rows",[]) if x.get("quality_proxy") is not None}
            recent_q=[]; recent_w=[]; season_q=[]; season_w=[]
            for r in discipline.get("pitch_mix_recent",[]):
                k=_pitch_key(r.get("pitch_type")); base=sea.get(k)
                if base is not None:
                    usage_change.append({"pitch_type":r.get("pitch_type"),"recent_usage":r.get("usage"),"season_usage":base,"delta_pp":(r.get("usage")-base)*100})
                if quality.get(k) is not None:
                    recent_q.append(quality[k]); recent_w.append(r.get("usage") or 0)
            for k,u in sea.items():
                if quality.get(k) is not None:
                    season_q.append(quality[k]); season_w.append(u or 0)
            rq=_weighted_mean(recent_q,recent_w); sq=_weighted_mean(season_q,season_w)
            if rq is not None and sq is not None: usage_quality_delta=rq-sq
            usage_change=sorted(usage_change,key=lambda x:abs(x.get("delta_pp") or 0),reverse=True)
        return {
            "available": bool(season_profile.get("available") or discipline.get("available") or arsenal.get("available")),
            "season": season_profile, "recent_discipline": discipline, "arsenal": arsenal,
            "usage_change":usage_change,"usage_quality_delta":usage_quality_delta,
            "recent_batted_ball": recent_raw, "season_batted_ball": season_raw,
            "source": "Baseball Savant + MLB Stats API play-by-play",
        }

    def starter_workload(self, pitcher_payload, kickoff):
        recent = (pitcher_payload or {}).get("recent") or {}
        starts = recent.get("starts") or []
        season_info = (pitcher_payload or {}).get("season") or {}
        if not starts:
            return {"available": False, "reason": "starter pitch-count logs unavailable"}
        ko = _utc(kickoff)
        last_date = _utc(starts[-1].get("date")) if starts[-1].get("date") else None
        rest_days = None
        if ko is not None and last_date is not None:
            rest_days = (ko.date() - last_date.date()).days - 1
        pitch_counts = [_num(x.get("pitches")) for x in starts if _num(x.get("pitches")) is not None]
        last_pitches = pitch_counts[-1] if pitch_counts else None
        avg3 = sum(pitch_counts[-3:]) / len(pitch_counts[-3:]) if pitch_counts else None
        curr_ip = _num(season_info.get("innings"))
        prior_ip = _num(season_info.get("prior_innings"))
        workload_ratio = curr_ip / prior_ip if curr_ip is not None and prior_ip not in (None, 0) else None
        risk = 0.0
        if rest_days is not None and rest_days <= 3:
            risk += .018
        if last_pitches is not None and last_pitches >= 105:
            risk += .012
        if avg3 is not None and avg3 >= 100:
            risk += .008
        if workload_ratio is not None and workload_ratio >= 1.25:
            risk += .012
        return {
            "available": True, "rest_days": rest_days, "last_pitches": last_pitches,
            "avg_pitches_last3": avg3, "season_innings": curr_ip, "prior_season_innings": prior_ip,
            "workload_ratio": workload_ratio, "run_factor_against": 1 + min(.045, risk),
            "source": "MLB Stats API pitcher game logs",
        }

    def bullpen_exact(self, team_id, kickoff, lookback_days=3):
        ko = _utc(kickoff)
        if ko is None:
            return {"available": False, "reason": "kickoff unavailable"}
        start = (ko - timedelta(days=lookback_days)).date().isoformat()
        end = (ko - timedelta(days=1)).date().isoformat()
        try:
            sched = self.base._get("/schedule", {"sportId": 1, "teamId": int(team_id), "startDate": start, "endDate": end})
        except Exception as e:
            return {"available": False, "reason": str(e)}
        relievers = {}
        game_count = 0
        dates_used = []
        for d in sched.get("dates", []):
            game_date = d.get("date")
            for g in d.get("games", []):
                if (g.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                gp = g.get("gamePk")
                if not gp:
                    continue
                try:
                    box = self.base._get(f"/game/{int(gp)}/boxscore")
                except Exception:
                    continue
                side = None
                for s in ("home", "away"):
                    if int((((box.get("teams") or {}).get(s) or {}).get("team") or {}).get("id") or -1) == int(team_id):
                        side = s; break
                if not side:
                    continue
                team = (box.get("teams") or {}).get(side) or {}
                pids = team.get("pitchers") or []
                players = team.get("players") or {}
                if len(pids) < 2:
                    continue
                game_count += 1; dates_used.append(game_date)
                for pid in pids[1:]:
                    p = players.get(f"ID{pid}", {})
                    stat = ((p.get("stats") or {}).get("pitching") or {})
                    pitches = _num(stat.get("numberOfPitches"))
                    if pitches is None:
                        pitches = _num(stat.get("pitchesThrown"))
                    ip = stat.get("inningsPitched")
                    # Convert 0.1/0.2 notation safely.
                    try:
                        s_ip = str(ip)
                        if "." in s_ip:
                            a, b = s_ip.split(".", 1)
                            ipf = float(a) + (1/3 if b == "1" else 2/3 if b == "2" else float("0." + b))
                        else:
                            ipf = float(s_ip)
                    except Exception:
                        ipf = 0.0
                    ent = relievers.setdefault(int(pid), {"id": int(pid), "name": (p.get("person") or {}).get("fullName"), "pitches": 0.0, "ip": 0.0, "appearances": 0, "dates": []})
                    ent["pitches"] += pitches or 0
                    ent["ip"] += ipf
                    ent["appearances"] += 1
                    ent["dates"].append(game_date)
        rows = list(relievers.values())
        for r in rows:
            ds = sorted({x for x in r["dates"] if x})
            r["back_to_back"] = any((_date(ds[i]) - _date(ds[i-1])).days == 1 for i in range(1, len(ds))) if len(ds) >= 2 else False
        total_pitches = sum(r["pitches"] for r in rows)
        relievers_20 = sum(1 for r in rows if r["pitches"] >= 20)
        relievers_30 = sum(1 for r in rows if r["pitches"] >= 30)
        b2b = sum(1 for r in rows if r["back_to_back"])
        # Fatigue score is descriptive and capped; it does not assume a specific closer.
        fatigue = min(1.0, total_pitches / 180.0 + .12 * relievers_30 + .08 * b2b)
        return {
            "available": game_count > 0, "games_last3d": game_count, "dates": dates_used,
            "total_relief_pitches": total_pitches, "relievers_20plus": relievers_20,
            "relievers_30plus": relievers_30, "back_to_back_relievers": b2b,
            "fatigue_score": fatigue, "relievers": sorted(rows, key=lambda x: x["pitches"], reverse=True),
            "opponent_run_factor": 1 + min(.035, .035 * fatigue),
            "source": "MLB Stats API boxscores, exact reliever pitch counts",
        }

    def bullpen_manager_pattern(self, team_id, kickoff, games_back=10):
        ko = _utc(kickoff)
        if ko is None:
            return {"available": False, "reason": "kickoff unavailable"}
        start = (ko - timedelta(days=22)).date().isoformat(); end = (ko - timedelta(days=1)).date().isoformat()
        try:
            sched = self.base._get("/schedule", {"sportId": 1, "teamId": int(team_id), "startDate": start, "endDate": end})
        except Exception as e:
            return {"available": False, "reason": str(e)}
        games = []
        for d in sched.get("dates", []):
            for g in d.get("games", []):
                if (g.get("status") or {}).get("abstractGameState") == "Final":
                    games.append((d.get("date"), g.get("gamePk")))
        games = games[-int(games_back):]
        appearances = {}
        for gd, gp in games:
            if not gp:
                continue
            try: box = self.base._get(f"/game/{int(gp)}/boxscore")
            except Exception: continue
            side = next((s for s in ("home", "away") if int(((((box.get("teams") or {}).get(s) or {}).get("team") or {}).get("id") or -1)) == int(team_id)), None)
            if not side: continue
            team = (box.get("teams") or {}).get(side) or {}
            pids = team.get("pitchers") or []; players = team.get("players") or {}
            for pid in pids[1:]:
                p = players.get(f"ID{pid}", {})
                st = ((p.get("stats") or {}).get("pitching") or {})
                pc = _num(st.get("numberOfPitches")) or _num(st.get("pitchesThrown")) or 0
                appearances.setdefault(int(pid), []).append({"date": gd, "pitches": pc, "name": (p.get("person") or {}).get("fullName")})
        if not appearances:
            return {"available": False, "reason": "recent relief usage unavailable"}
        b2b_opportunities = 0; b2b_used = 0; heavy_reuse = 0
        rows = []
        for pid, apps in appearances.items():
            apps = sorted(apps, key=lambda x: str(x["date"]))
            for i in range(1, len(apps)):
                d0, d1 = _date(apps[i-1]["date"]), _date(apps[i]["date"])
                if d0 and d1 and (d1 - d0).days == 1:
                    b2b_used += 1
                    if apps[i-1]["pitches"] >= 20:
                        heavy_reuse += 1
            rows.append({"id": pid, "name": apps[-1].get("name"), "appearances": len(apps), "avg_pitches": sum(a["pitches"] for a in apps)/len(apps)})
        # Opportunity denominator uses team game gaps, not every reliever; keeps this descriptive.
        game_dates = [_date(x[0]) for x in games if _date(x[0])]
        b2b_opportunities = sum(1 for i in range(1, len(game_dates)) if (game_dates[i] - game_dates[i-1]).days == 1)
        return {
            "available": True, "games": len(games), "reliever_b2b_uses": b2b_used,
            "team_b2b_game_opportunities": b2b_opportunities, "heavy_reuse_after_20plus": heavy_reuse,
            "most_used": sorted(rows, key=lambda x: x["appearances"], reverse=True)[:5],
            "source": "MLB Stats API recent bullpen usage pattern",
        }

    def _player_split(self, player_id, season, hand):
        if hand not in {"L", "R"}:
            return None
        code = "vl" if hand == "L" else "vr"
        try:
            splits = self.base._stat(person_id=player_id, stats="statSplits", group="hitting", season=season, sit_code=code)
            st = (splits[0].get("stat") if splits else {}) or {}
            pa = _num(st.get("plateAppearances"))
            ops = _num(st.get("ops"))
            return {"player_id": player_id, "ops": ops, "pa": pa, "hand": hand} if ops is not None else None
        except Exception:
            return None

    def lineup_platoon(self, lineup, season, opponent_hand):
        ids = [x.get("player_id") for x in (lineup or [])[:9] if x.get("player_id")]
        if len(ids) < 7 or opponent_hand not in {"L", "R"}:
            return {"available": False, "reason": "confirmed lineup/player ids or starter hand unavailable"}
        rows = []
        with ThreadPoolExecutor(max_workers=min(9, len(ids))) as ex:
            futs = {ex.submit(self._player_split, pid, season, opponent_hand): (i, pid) for i, pid in enumerate(ids)}
            for f in as_completed(futs):
                i, pid = futs[f]
                try: x = f.result()
                except Exception: x = None
                if x:
                    x["order"] = i + 1; rows.append(x)
        if len(rows) < 6:
            return {"available": False, "reason": f"only {len(rows)}/9 lineup split rows available", "rows": rows}
        weights = [max(.55, 1.12 - .065*(r["order"]-1)) * min(1.0, (r.get("pa") or 0)/80.0) for r in rows]
        ops = _weighted_mean([r["ops"] for r in rows], weights)
        return {"available": ops is not None, "lineup_split_ops": ops, "players": len(rows), "rows": sorted(rows, key=lambda x: x["order"]), "source": "MLB Stats API exact batter vs pitcher-hand splits"}

    def pitch_type_matchup(self, lineup, opponent_arsenal, season):
        if not lineup or not (opponent_arsenal or {}).get("available"):
            return {"available": False, "reason": "lineup or opposing pitch arsenal unavailable"}
        batter_ids = {int(x.get("player_id")) for x in lineup[:9] if x.get("player_id")}
        batter_names = {_norm(x.get("name")): x for x in lineup[:9] if x.get("name")}
        if len(batter_ids) < 7:
            return {"available": False, "reason": "lineup ids incomplete"}
        try:
            df = self.savant.arsenal(season, "batter")
        except Exception as e:
            return {"available": False, "reason": str(e)}
        if df.empty:
            return {"available": False, "reason": "batter arsenal leaderboard empty"}
        id_col = _pick_col(df, "player_id", "playerid", "id")
        name_col = _pick_col(df, "player_name", "player", "name")
        if id_col:
            ids = pd.to_numeric(df[id_col], errors="coerce")
            sub = df[ids.isin(batter_ids)].copy()
        elif name_col:
            namekeys={_namekey(x.get("name")) for x in lineup[:9] if x.get("name")}
            sub = df[df[name_col].map(lambda v: _norm(v) in batter_names or _namekey(v) in namekeys)].copy()
        else:
            return {"available": False, "reason": "batter pitch-type player identifier unavailable"}
        if sub.empty:
            return {"available": False, "reason": "lineup pitch-type rows unavailable"}
        pitch_col = _pick_col(sub, "pitch", "pitch type")
        xw_col = _pick_col(sub, "xwoba")
        pa_col = _pick_col(sub, "pa")
        if not pitch_col or not xw_col:
            return {"available": False, "reason": "pitch-type xwOBA columns unavailable"}
        pitch_team = {}
        for _, r in sub.iterrows():
            pt = str(r.get(pitch_col) or "")
            xw = _num(r.get(xw_col)); pa = _num(r.get(pa_col)) if pa_col else 1
            if not pt or xw is None:
                continue
            pitch_team.setdefault(_pitch_key(pt), []).append((xw, max(1, pa or 1)))
        pitch_xw = {k: _weighted_mean([x for x, _ in v], [w for _, w in v]) for k, v in pitch_team.items()}
        vals=[]; weights=[]; matched=[]
        for p in opponent_arsenal.get("rows", []):
            use = p.get("usage")
            if use is None or use < .04: continue
            key = _pitch_key(p.get("pitch_type"))
            # Common aliases between leaderboard labels/codes are handled by normalized match.
            choices = [k for k in pitch_xw if key == k or key in k or k in key]
            if not choices: continue
            xw = pitch_xw[choices[0]]
            if xw is None: continue
            vals.append(xw); weights.append(use); matched.append({"pitch_type": p.get("pitch_type"), "usage": use, "lineup_xwoba": xw})
        weighted = _weighted_mean(vals, weights)
        return {"available": weighted is not None and len(matched)>=2, "weighted_xwoba": weighted, "matched_pitch_types": matched, "source": "Baseball Savant batter pitch-type leaderboard × starter pitch mix"}

    def bvp(self, lineup, pitcher_id, kickoff, years=3):
        ids = {int(x.get("player_id")) for x in (lineup or [])[:9] if x.get("player_id")}
        if len(ids) < 7 or not pitcher_id:
            return {"available": False, "reason": "confirmed lineup/pitcher unavailable"}
        ko = _utc(kickoff); end = (ko - timedelta(days=1)).date() if ko is not None else pd.Timestamp.utcnow().date()
        start = pd.Timestamp(year=end.year-years+1, month=3, day=1).date()
        try:
            df = self.savant.raw_player(player_id=pitcher_id, player_type="pitcher", start_date=start, end_date=end)
        except Exception as e:
            return {"available": False, "reason": str(e)}
        if df.empty:
            return {"available": False, "reason": "BvP Statcast rows unavailable"}
        batter_col = _pick_col(df, "batter")
        events_col = _pick_col(df, "events")
        woba_col = _pick_col(df, "woba_value")
        if not batter_col or not events_col:
            return {"available": False, "reason": "BvP identifier/event columns unavailable"}
        b = pd.to_numeric(df[batter_col], errors="coerce")
        sub = df[b.isin(ids) & df[events_col].notna()].copy()
        at_col = _pick_col(sub, "at_bat_number"); game_col = _pick_col(sub, "game_pk")
        if at_col and game_col:
            sub = sub.drop_duplicates([game_col, at_col], keep="last")
        pa = len(sub)
        if pa == 0:
            return {"available": False, "reason": "no lineup BvP plate appearances"}
        if woba_col:
            wv = pd.to_numeric(sub[woba_col], errors="coerce")
            woba = float(wv.mean()) if wv.notna().any() else None
        else:
            woba = None
        return {"available": True, "pa": pa, "woba_value_mean": woba, "sample_reliable": pa >= 30, "source": "Baseball Savant historical batter-vs-pitcher pitch data"}

    def availability(self, team_id, kickoff):
        ko = _utc(kickoff)
        if ko is None:
            return {"available": False, "reason": "kickoff unavailable"}
        start = (ko - timedelta(days=10)).date().isoformat(); end = ko.date().isoformat()
        try:
            tx = self.base._get("/transactions", {"teamId": int(team_id), "startDate": start, "endDate": end})
            rows = []
            for t in tx.get("transactions", []):
                desc = str(t.get("description") or "")
                low = desc.lower()
                if any(k in low for k in ["injured list", "activated", "reinstated", "optioned", "recalled", "bereavement", "paternity", "scratched"]):
                    rows.append({"date": t.get("date"), "description": desc})
            return {"available": True, "transactions": rows[-12:], "alerts": len(rows), "source": "MLB Stats API official transactions"}
        except Exception as e:
            return {"available": False, "reason": str(e)}

    def news_alerts(self, team_name, kickoff, probable_name=None):
        ko = _utc(kickoff)
        if ko is None:
            return {"available": False, "reason": "kickoff unavailable"}
        hours = (ko - pd.Timestamp.now(tz="UTC")).total_seconds()/3600
        if hours > 18 or hours < -1:
            return {"available": False, "reason": "news scan only runs within 18h of first pitch"}
        query = f'"{team_name}" MLB (injury OR injured OR activated OR reinstated OR scratched OR rest)'
        if probable_name:
            query += f' OR "{probable_name}"'
        try:
            r = self.s.get(GOOGLE_NEWS_RSS, params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}, timeout=min(10, self.timeout))
            r.raise_for_status()
            root = ET.fromstring(r.content)
            items=[]
            for item in root.findall(".//item")[:8]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                if title:
                    items.append({"title": title, "link": link, "published": pub})
            return {"available": True, "items": items, "source": "Google News RSS alert scan (context only; no direct probability adjustment)"}
        except Exception as e:
            return {"available": False, "reason": str(e)}

    def lineup_change(self, event_id, lineup):
        if not event_id or not lineup or not lineup.get("confirmed"):
            return {"available": False, "changed": False, "reason": "confirmed lineup/event id unavailable"}
        h = lineup_hash(lineup.get("home")); a = lineup_hash(lineup.get("away"))
        if not h or not a:
            return {"available": False, "changed": False, "reason": "lineup hash unavailable"}
        state = JSONState(self.data_dir / "mlb_lineup_state.json")
        obj = state.load(); old = obj.get(str(event_id)) or {}
        changed = bool(old and (old.get("home") != h or old.get("away") != a))
        obj[str(event_id)] = {"home": h, "away": a, "seen_at": pd.Timestamp.now(tz="UTC").isoformat()}
        state.save(obj)
        return {"available": True, "changed": changed, "previous_seen": old.get("seen_at"), "source": "local immutable lineup observation state"}

    def market_movement(self, event_id, market_frame):
        if not event_id or market_frame is None or market_frame.empty:
            return {"available": False, "reason": "event/market unavailable", "by_key": {}}
        path = self.data_dir / "mlb_market_snapshots.jsonl"
        now = pd.Timestamp.now(tz="UTC")
        prior=[]
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines()[-8000:]:
                    x=json.loads(line)
                    if str(x.get("event_id"))==str(event_id): prior.append(x)
            except Exception:
                prior=[]
        by_key={}
        for _, r in market_frame.iterrows():
            k=_event_key(r); p=_num(r.get("consensus_prob")); odds=_num(r.get("best_odds"))
            if p is None: continue
            hist=[x for x in prior if x.get("key")==k]
            hist=sorted(hist,key=lambda x:x.get("ts",""))
            prev=hist[-1] if hist else None
            opening=hist[0] if hist else None
            by_key[k]={
                "available": bool(prev), "current_prob":p,"current_odds":odds,
                "last_prob":_num(prev.get("prob")) if prev else None,
                "move_pp":(p-_num(prev.get("prob")))*100 if prev and _num(prev.get("prob")) is not None else None,
                "opening_prob":_num(opening.get("prob")) if opening else None,
                "from_open_pp":(p-_num(opening.get("prob")))*100 if opening and _num(opening.get("prob")) is not None else None,
                "last_seen":prev.get("ts") if prev else None,
            }
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open("a",encoding="utf-8") as f:
                for _, r in market_frame.iterrows():
                    p=_num(r.get("consensus_prob"))
                    if p is None: continue
                    f.write(json.dumps({"ts":now.isoformat(),"event_id":str(event_id),"key":_event_key(r),"prob":p,"best_odds":_num(r.get("best_odds"))},ensure_ascii=False)+"\n")
        except Exception:
            pass
        vals=[abs(x.get("move_pp")) for x in by_key.values() if x.get("move_pp") is not None]
        return {"available": any(x.get("available") for x in by_key.values()),"by_key":by_key,"max_abs_move_pp":max(vals) if vals else None,"source":"locally observed The Odds API consensus snapshots"}

    def _live_feed(self, game_pk):
        if not game_pk:return {}
        gp=int(game_pk)
        if gp in self.live_cache:return self.live_cache[gp]
        r=self.s.get(f"https://statsapi.mlb.com/api/v1.1/game/{gp}/feed/live",timeout=min(12,self.timeout))
        r.raise_for_status(); data=r.json(); self.live_cache[gp]=data; return data

    def _umpire_tendency(self, umpire_id, season, kickoff, max_games=5):
        if not umpire_id:return {"available":False,"reason":"umpire id unavailable"}
        try:
            sched=self.base._get(f"/jobs/umpires/games/{int(umpire_id)}",{"season":int(season),"fields":"dates,date,games,gamePk,gameDate"})
        except Exception as e:
            return {"available":False,"reason":str(e)}
        ko=_utc(kickoff)
        games=[]
        for d in sched.get("dates",[]):
            for g in d.get("games",[]):
                gt=_utc(g.get("gameDate"))
                if g.get("gamePk") and (ko is None or gt is None or gt<ko):games.append((gt,g.get("gamePk")))
        games=sorted(games,key=lambda x:(x[0] or pd.Timestamp.min.tz_localize("UTC")))[-12:]
        verified=[]
        for _,gp in reversed(games):
            if len(verified)>=max_games:break
            try:data=self._live_feed(gp)
            except Exception:continue
            officials=(((data.get("liveData") or {}).get("boxscore") or {}).get("officials") or [])
            hp=next((x for x in officials if "home plate" in str(x.get("officialType") or "").lower()),None)
            if int((((hp or {}).get("official") or {}).get("id") or -1))!=int(umpire_id):continue
            ls=((data.get("liveData") or {}).get("linescore") or {}).get("teams") or {}
            hr=_num((ls.get("home") or {}).get("runs")); ar=_num((ls.get("away") or {}).get("runs"))
            called=0;takes=0
            for play in ((data.get("liveData") or {}).get("plays") or {}).get("allPlays",[]):
                for ev in play.get("playEvents",[]):
                    if not ev.get("isPitch"):continue
                    code=str((ev.get("details") or {}).get("code") or "")
                    # Called strike / ball / automatic ball are non-swing takes. HBP excluded.
                    if code in {"C","B","V","P"}:
                        takes+=1; called+=int(code=="C")
            verified.append({"gamePk":gp,"total_runs":(hr+ar if hr is not None and ar is not None else None),"called_strike_on_takes":called/takes if takes else None})
        if len(verified)<3:return {"available":False,"games":len(verified),"reason":"<3 verified home-plate games"}
        avg_runs=_weighted_mean([x["total_runs"] for x in verified],[1]*len(verified))
        csr=_weighted_mean([x["called_strike_on_takes"] for x in verified],[1]*len(verified))
        return {"available":True,"games":len(verified),"avg_total_runs":avg_runs,"called_strike_on_takes":csr,
                "note":"verified recent home-plate games; descriptive context only, no unvalidated total coefficient", "source":"MLB Stats API umpire schedule + live pitch logs"}

    def roof_umpire(self, game_pk, venue_id, kickoff=None):
        structural=self._venue(venue_id)
        out={"available":bool(structural),"stadium":structural.get("name"),"roof_type":structural.get("roof_type"),"roof_state":None,"umpire":None,"source":"MLB Stats API live feed/venue"}
        if not game_pk:
            return out
        try:
            # feed/live lives under v1.1 rather than the v1 base used by the parent.
            data=self._live_feed(game_pk)
            weather=((data.get("gameData") or {}).get("weather") or {})
            cond=str(weather.get("condition") or "")
            if "roof" in cond.lower(): out["roof_state"]=cond
            elif any(x in str(out.get("roof_type") or "").lower() for x in ["dome","fixed"]): out["roof_state"]="fixed/closed"
            officials=(((data.get("liveData") or {}).get("boxscore") or {}).get("officials") or [])
            hp=next((x for x in officials if "home plate" in str(x.get("officialType") or "").lower()),None)
            if hp:
                off=hp.get("official") or {}
                tendency=self._umpire_tendency(off.get("id"),(_utc(kickoff).year if kickoff and _utc(kickoff) is not None else pd.Timestamp.utcnow().year),kickoff) if kickoff else {"available":False,"reason":"kickoff unavailable"}
                out["umpire"]={"id":off.get("id"),"name":off.get("fullName"),"tendency":tendency,"note":"assignment + verified recent plate sample when available"}
            out["available"]=True
        except Exception as e:
            out["live_reason"]=str(e)
        return out

    def travel_rest(self, team_profile, current_venue_id, kickoff):
        recent=(team_profile or {}).get("recent_games") or []
        if not recent:
            return {"available":False,"reason":"previous game unavailable"}
        prev=recent[-1]
        curr=self._venue(current_venue_id); last=self._venue(prev.get("venue_id"))
        ko=_utc(kickoff); prev_t=_utc(prev.get("date"))
        rest_hours=(ko-prev_t).total_seconds()/3600 if ko is not None and prev_t is not None else None
        miles=_haversine_miles(last.get("lat"),last.get("lon"),curr.get("lat"),curr.get("lon"))
        tz_shift=None
        try:
            from zoneinfo import ZoneInfo
            if last.get("timezone") and curr.get("timezone") and ko is not None:
                a=ko.tz_convert(ZoneInfo(last["timezone"])).utcoffset().total_seconds()/3600
                b=ko.tz_convert(ZoneInfo(curr["timezone"])).utcoffset().total_seconds()/3600
                tz_shift=b-a
        except Exception:
            tz_shift=None
        factor=1.0
        if rest_hours is not None and rest_hours < 24 and miles is not None and miles > 1200: factor=.987
        elif rest_hours is not None and rest_hours < 28 and miles is not None and miles > 700: factor=.992
        if rest_hours is not None and rest_hours < 28 and tz_shift is not None and abs(tz_shift)>=2:
            factor*=.994
        return {"available":rest_hours is not None,"rest_hours":rest_hours,"travel_miles":miles,"timezone_shift_hours":tz_shift,
                "offense_factor":_clamp(factor,.975,1.01),"previous_venue":last.get("name"),"current_venue":curr.get("name"),
                "previous_timezone":last.get("timezone"),"current_timezone":curr.get("timezone"),
                "source":"MLB schedule + venue coordinates/time zones"}

    def collect(self, *, home, away, kickoff, schedule_row, home_profile, away_profile,
                home_pitcher, away_pitcher, lineup, market_frame=None, event_id=None, season=None):
        season=int(season or pd.Timestamp(kickoff).year)
        hp_id=(schedule_row or {}).get("home_probable_id")
        ap_id=(schedule_row or {}).get("away_probable_id")
        hp_name=(schedule_row or {}).get("home_probable") or (home_pitcher or {}).get("name")
        ap_name=(schedule_row or {}).get("away_probable") or (away_pitcher or {}).get("name")
        hp_sc=self.statcast_pitcher(hp_id,hp_name,season,kickoff,((home_pitcher or {}).get("recent") or {}).get("game_pks",[])) if hp_id else {"available":False,"reason":"home starter unavailable"}
        ap_sc=self.statcast_pitcher(ap_id,ap_name,season,kickoff,((away_pitcher or {}).get("recent") or {}).get("game_pks",[])) if ap_id else {"available":False,"reason":"away starter unavailable"}
        hp_work=self.starter_workload(home_pitcher,kickoff); ap_work=self.starter_workload(away_pitcher,kickoff)
        hb=self.bullpen_exact((home_profile or {}).get("team_id"),kickoff) if (home_profile or {}).get("team_id") else {"available":False}
        ab=self.bullpen_exact((away_profile or {}).get("team_id"),kickoff) if (away_profile or {}).get("team_id") else {"available":False}
        hmgr=self.bullpen_manager_pattern((home_profile or {}).get("team_id"),kickoff) if (home_profile or {}).get("team_id") else {"available":False}
        amgr=self.bullpen_manager_pattern((away_profile or {}).get("team_id"),kickoff) if (away_profile or {}).get("team_id") else {"available":False}
        hhand=((away_pitcher or {}).get("recent") or {}).get("hand")
        ahand=((home_pitcher or {}).get("recent") or {}).get("hand")
        hpl=self.lineup_platoon((lineup or {}).get("home",[]),season,hhand)
        apl=self.lineup_platoon((lineup or {}).get("away",[]),season,ahand)
        hpm=self.pitch_type_matchup((lineup or {}).get("home",[]),ap_sc.get("arsenal") or {},season)
        apm=self.pitch_type_matchup((lineup or {}).get("away",[]),hp_sc.get("arsenal") or {},season)
        hbvp=self.bvp((lineup or {}).get("home",[]),ap_id,kickoff) if (lineup or {}).get("confirmed") else {"available":False,"reason":"lineup not confirmed"}
        abvp=self.bvp((lineup or {}).get("away",[]),hp_id,kickoff) if (lineup or {}).get("confirmed") else {"available":False,"reason":"lineup not confirmed"}
        hav=self.availability((home_profile or {}).get("team_id"),kickoff) if (home_profile or {}).get("team_id") else {"available":False}
        aav=self.availability((away_profile or {}).get("team_id"),kickoff) if (away_profile or {}).get("team_id") else {"available":False}
        hnews=self.news_alerts(home,kickoff,hp_name); anews=self.news_alerts(away,kickoff,ap_name)
        lu_change=self.lineup_change(event_id,lineup)
        movement=self.market_movement(event_id,market_frame)
        env=self.roof_umpire((schedule_row or {}).get("gamePk"),(schedule_row or {}).get("venue_id"),kickoff)
        htravel=self.travel_rest(home_profile,(schedule_row or {}).get("venue_id"),kickoff)
        atravel=self.travel_rest(away_profile,(schedule_row or {}).get("venue_id"),kickoff)

        statuses={
            "plate_discipline":bool((hp_sc.get("recent_discipline") or {}).get("available") and (ap_sc.get("recent_discipline") or {}).get("available")),
            "statcast_quality":bool((hp_sc.get("season") or {}).get("available") and (ap_sc.get("season") or {}).get("available")),
            "batted_ball_regression":bool((hp_sc.get("recent_batted_ball") or {}).get("available") and (ap_sc.get("recent_batted_ball") or {}).get("available")),
            "pitch_mix":bool((hp_sc.get("arsenal") or {}).get("available") and (ap_sc.get("arsenal") or {}).get("available")),
            "starter_workload":bool(hp_work.get("available") and ap_work.get("available")),
            "bullpen_exact":bool(hb.get("available") and ab.get("available")),
            "lineup_platoon_exact":bool(hpl.get("available") and apl.get("available")),
            "pitch_matchup":bool(hpm.get("available") and apm.get("available")),
            "availability_news":bool(hav.get("available") and aav.get("available")),
            "lineup_change":bool(lu_change.get("available")),
            "market_movement":bool(movement.get("available")),
            "roof":bool(env.get("available")),
            "umpire":bool((env.get("umpire") or {}).get("name")),
            "travel_rest":bool(htravel.get("available") and atravel.get("available")),
            "bvp":bool(hbvp.get("available") and abvp.get("available")),
            "bullpen_manager":bool(hmgr.get("available") and amgr.get("available")),
            "news_scan":bool(hnews.get("available") and anews.get("available")),
        }

        # Conservative direction factors. Every one is bounded to avoid swamping the base model.
        components={}
        def starter_deep(sc, work):
            factor=1.0
            sea=sc.get("season") or {}; disc=sc.get("recent_discipline") or {}; recent=sc.get("recent_batted_ball") or {}; seasraw=sc.get("season_batted_ball") or {}
            sx=sea.get("xwoba")
            rx=None
            # raw batted-ball lacks full-PA xwOBA; only use hard-hit/barrel deltas here.
            if sea.get("hardhit_pct") is not None and recent.get("hardhit_pct") is not None:
                factor*=1+_clamp((recent["hardhit_pct"]-sea["hardhit_pct"])*.10,-.018,.018)
            if sea.get("barrel_pct") is not None and recent.get("barrel_pct") is not None:
                factor*=1+_clamp((recent["barrel_pct"]-sea["barrel_pct"])*.12,-.012,.012)
            # BABIP/HR-FB regression is only used when contact quality did not move in the same direction.
            sraw=sc.get("season_batted_ball") or {}
            hh_delta=(recent.get("hardhit_pct")-sraw.get("hardhit_pct")) if recent.get("hardhit_pct") is not None and sraw.get("hardhit_pct") is not None else None
            if recent.get("babip") is not None and sraw.get("babip") is not None and (hh_delta is None or abs(hh_delta)<.025):
                bd=recent["babip"]-sraw["babip"]
                factor*=1-_clamp(bd*.12,-.010,.010)
            if recent.get("hr_fb") is not None and sraw.get("hr_fb") is not None and (hh_delta is None or abs(hh_delta)<.025):
                hd=recent["hr_fb"]-sraw["hr_fb"]
                factor*=1-_clamp(hd*.08,-.008,.008)
            if sea.get("whiff_pct") is not None and disc.get("whiff_pct") is not None:
                factor*=1-_clamp((disc["whiff_pct"]-sea["whiff_pct"])*.08,-.015,.015)
            if sea.get("chase_pct") is not None and disc.get("chase_pct") is not None:
                factor*=1-_clamp((disc["chase_pct"]-sea["chase_pct"])*.06,-.010,.010)
            # If recent pitch mix shifted toward the pitcher's own better Savant arsenal pitches,
            # use a very small directional adjustment. This is a transparent Stuff proxy, not Stuff+.
            uq=sc.get("usage_quality_delta")
            if uq is not None:
                factor*=1-_clamp(float(uq)*.025,-.012,.012)
            if work.get("run_factor_against") is not None:
                factor*=work["run_factor_against"]
            return _clamp(factor,.94,1.06)
        components["home_starter_deep_factor"]=starter_deep(hp_sc,hp_work)
        components["away_starter_deep_factor"]=starter_deep(ap_sc,ap_work)
        components["home_vs_bullpen_exact_factor"]=ab.get("opponent_run_factor") if ab.get("available") else None
        components["away_vs_bullpen_exact_factor"]=hb.get("opponent_run_factor") if hb.get("available") else None
        # Exact confirmed-lineup splits are compared to team season OPS (real baseline already collected by parent).
        if hpl.get("lineup_split_ops") is not None and (home_profile or {}).get("ops"):
            ratio=hpl["lineup_split_ops"]/(home_profile.get("ops") or 1)
            components["home_lineup_platoon_factor"]=_clamp(math.exp(.22*math.log(max(.5,ratio))),.96,1.04)
        if apl.get("lineup_split_ops") is not None and (away_profile or {}).get("ops"):
            ratio=apl["lineup_split_ops"]/(away_profile.get("ops") or 1)
            components["away_lineup_platoon_factor"]=_clamp(math.exp(.22*math.log(max(.5,ratio))),.96,1.04)
        # Pitch-type matchup: compare weighted lineup xwOBA to neutral .320 only as a very small context factor.
        # .320 is not a missing-data fill; it is a transparent centering constant and never creates availability.
        if hpm.get("weighted_xwoba") is not None:
            components["home_pitch_matchup_factor"]=_clamp(1+(hpm["weighted_xwoba"]-.320)*.18,.97,1.03)
        if apm.get("weighted_xwoba") is not None:
            components["away_pitch_matchup_factor"]=_clamp(1+(apm["weighted_xwoba"]-.320)*.18,.97,1.03)
        components["home_travel_rest_factor"]=htravel.get("offense_factor") if htravel.get("available") else None
        components["away_travel_rest_factor"]=atravel.get("offense_factor") if atravel.get("available") else None
        # BvP stays low weight and requires 30+ combined PA before moving probability.
        if hbvp.get("sample_reliable") and hbvp.get("woba_value_mean") is not None:
            components["home_bvp_factor"]=_clamp(1+(hbvp["woba_value_mean"]-.320)*.06,.985,1.015)
        if abvp.get("sample_reliable") and abvp.get("woba_value_mean") is not None:
            components["away_bvp_factor"]=_clamp(1+(abvp["woba_value_mean"]-.320)*.06,.985,1.015)

        high_priority=["plate_discipline","statcast_quality","starter_workload","bullpen_exact","lineup_platoon_exact","pitch_mix","pitch_matchup","market_movement"]
        missing_high=[k for k in high_priority if not statuses.get(k)]
        extra_unc=min(3.5,.32*len(missing_high))
        if lu_change.get("changed"): extra_unc+=.5
        if movement.get("max_abs_move_pp") is not None and movement.get("max_abs_move_pp")>=2.0: extra_unc+=.35

        return {
            "statuses":statuses,"components":components,"advanced_total":len(statuses),
            "advanced_used":sum(bool(v) for v in statuses.values()),
            "advanced_completeness":sum(bool(v) for v in statuses.values())/len(statuses),
            "extra_uncertainty_pp":min(4.2,extra_unc),
            "home_statcast":hp_sc,"away_statcast":ap_sc,
            "home_starter_workload":hp_work,"away_starter_workload":ap_work,
            "home_bullpen_exact":hb,"away_bullpen_exact":ab,
            "home_bullpen_manager":hmgr,"away_bullpen_manager":amgr,
            "home_lineup_platoon":hpl,"away_lineup_platoon":apl,
            "home_pitch_matchup":hpm,"away_pitch_matchup":apm,
            "home_bvp":hbvp,"away_bvp":abvp,
            "home_availability":hav,"away_availability":aav,
            "home_news":hnews,"away_news":anews,
            "lineup_change":lu_change,"market_movement":movement,"environment":env,
            "home_travel_rest":htravel,"away_travel_rest":atravel,
            "missing_high_priority":missing_high,
            "source":"Baseball Savant + MLB Stats API + The Odds API observed snapshots + Open-Meteo/News alerts",
        }
