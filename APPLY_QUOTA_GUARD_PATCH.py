#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Sports EV Engine - API-Football quota guard deploy patch
# Target baseline observed: main commit e86f8821186f8d01872aa1504ebd300b063c1673
#
# This patch:
# 1) analyzes the full board without match-specific deep API calls,
# 2) runs deep context only on top-N candidate matches,
# 3) reuses competition fixture ids for recent xG,
# 4) lazily fetches player pages,
# 5) tracks API-Football quota/cache/request state,
# 6) respects Retry-After on 429 and stops the remaining deep batch on quota holds,
# 7) caches the resolved competition pool in Streamlit session_state.

from __future__ import annotations
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / "_quota_guard_backup"

def read(path: str) -> str:
    p = ROOT / path
    if not p.exists():
        raise RuntimeError(f"missing required file: {path}")
    return p.read_text(encoding="utf-8")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected marker exactly once, found {n}")
    return text.replace(old, new, 1)

def replace_section(text: str, start_marker: str, end_marker: str, new_section: str, label: str) -> str:
    s = text.find(start_marker)
    if s < 0:
        raise RuntimeError(f"{label}: start marker not found")
    e = text.find(end_marker, s)
    if e < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:s] + new_section + text[e:]

def patch_provider(text: str) -> str:
    if "from email.utils import parsedate_to_datetime" not in text:
        text = replace_once(
            text,
            "from threading import Lock\n",
            "from threading import Lock\nfrom email.utils import parsedate_to_datetime\n",
            "provider import",
        )

    if 'QUOTA_GUARD_BUILD = "3.4.25-staged-quota"' not in text:
        text = replace_once(
            text,
            'PROVIDER_BUILD = "3.0.0"\n',
            'PROVIDER_BUILD = "3.0.0"\nQUOTA_GUARD_BUILD = "3.4.25-staged-quota"\n',
            "provider build marker",
        )

    old_state = 'self.state=self._states.setdefault(key,{"cache":{},"next":0,"blocked":0,"last_supported":True})'
    new_state = '''self.state=self._states.setdefault(key,{
                "cache":{},"next":0.0,"blocked":0.0,"block_reason":"",
                "last_supported":True,"remaining_day":None,
                "remaining_minute":None,"requests_made":0,
            })'''
    if old_state in text:
        text = replace_once(text, old_state, new_state, "provider state")
    elif '"remaining_day"' not in text or '"requests_made"' not in text:
        raise RuntimeError("provider state: unsupported current source")

    marker = "    def _get(self,path,params):\n"
    if "    def quota_status(self):\n" not in text:
        helpers = r'''    @staticmethod
    def _number(headers,*names):
        for name in names:
            try:
                value=headers.get(name)
                if value is not None:return int(value)
            except (ValueError,TypeError):
                pass
        return None

    @staticmethod
    def _retry_after(headers):
        raw=headers.get("Retry-After")
        try:return max(60.0,float(raw))
        except (ValueError,TypeError):
            try:
                delta=(parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds()
                return max(60.0,delta)
            except (ValueError,TypeError,OverflowError):
                return 60.0

    def _block(self,seconds,reason):
        with self._lock:
            self.state["blocked"]=max(float(self.state.get("blocked") or 0.0),time.monotonic()+float(seconds))
            self.state["block_reason"]=str(reason)

    def quota_status(self):
        with self._lock:
            now=time.monotonic()
            return {
                "remaining_day":self.state.get("remaining_day"),
                "remaining_minute":self.state.get("remaining_minute"),
                "blocked_seconds":max(0,int(float(self.state.get("blocked") or 0.0)-now)),
                "block_reason":self.state.get("block_reason") or "",
                "requests_made":int(self.state.get("requests_made") or 0),
                "cache_entries":len(self.state.get("cache") or {}),
            }

    def can_spend(self,estimated=1,reserve=12):
        q=self.quota_status()
        if q["blocked_seconds"]>0:return False
        remaining=q.get("remaining_day")
        if remaining is None:return True
        return int(remaining)-max(0,int(reserve))>=max(1,int(estimated))

'''
        text = replace_once(text, marker, helpers + marker, "provider helper insertion")

    start = text.find("    def _get(self,path,params):\n")
    end = text.find("    def search_leagues(self, name):\n", start)
    if start < 0 or end < 0:
        raise RuntimeError("provider _get section markers missing")

    new_get = r'''    def _get(self,path,params):
        cache_key=(path,json.dumps(params,sort_keys=True))
        with self._lock:
            now=time.monotonic()
            cached=self.state["cache"].get(cache_key)
            if cached and now<cached[0]:
                return cached[1]
            blocked=float(self.state.get("blocked") or 0.0)
            if now<blocked:
                remaining=int(blocked-now)+1
                reason=self.state.get("block_reason") or "요청 제한"
                raise FootballAccessError(
                    f"API-Football 요청 보류 ({remaining}초): {reason}. "
                    "기존 배당/1차 모델은 유지하고 정밀 데이터만 보류합니다."
                )
            if self.state.get("remaining_day")==0:
                raise FootballAccessError(
                    "API-Football 일일 할당량 소진: 다음 할당량 갱신 전 정밀 요청을 중단합니다."
                )
            wait=max(0.0,float(self.state.get("next") or 0.0)-now)
            self.state["next"]=now+wait+6.2
        if wait:
            time.sleep(wait)

        try:
            r=requests.get(f"{BASE}/{path}",headers=self.headers,params=params,timeout=45)
        except requests.RequestException as exc:
            raise FootballAccessError(f"API-Football 네트워크 오류: {exc}") from exc

        with self._lock:
            self.state["requests_made"]=int(self.state.get("requests_made") or 0)+1

        remaining_day=self._number(
            r.headers,
            "x-ratelimit-requests-remaining","X-RateLimit-Requests-Remaining"
        )
        remaining_minute=self._number(
            r.headers,
            "X-RateLimit-Remaining","x-ratelimit-remaining"
        )
        with self._lock:
            if remaining_day is not None:self.state["remaining_day"]=remaining_day
            if remaining_minute is not None:self.state["remaining_minute"]=remaining_minute

        if r.status_code==429:
            seconds=self._retry_after(r.headers)
            if remaining_day==0:
                self._block(86400,"일일 할당량 소진")
            else:
                self._block(seconds,"429 요청 제한")
            raise FootballAccessError(
                f"API-Football 429: 요청 제한. Retry-After 기준 {int(seconds)}초 동안 정밀 호출을 중단합니다."
            )
        if r.status_code in (401,403):
            self._block(3600,f"인증/접근 오류 {r.status_code}")
            raise FootballAccessError(
                f"API-Football 인증/접근 오류 ({r.status_code}): 키와 플랜 권한을 확인하세요."
            )

        r.raise_for_status()
        body=r.json()
        errors=body.get("errors")
        if errors:
            if isinstance(errors,dict) and "plan" in errors:
                raise FootballPlanError(str(errors["plan"]))
            msg=str(errors)
            if any(s in msg.lower() for s in ("rate","limit","request","token","access")):
                self._block(86400 if remaining_day==0 else 60,"제공사 사용량/요청 제한")
                raise FootballAccessError(f"API-Football 수집 제한: {errors}")
            raise RuntimeError(msg)

        result=body.get("response",[])
        if path=="fixtures/statistics":
            ttl=86400
        elif path in ("teams","leagues"):
            ttl=86400
        elif path=="fixtures/lineups":
            ttl=120 if result else 60
        elif path=="injuries":
            ttl=600
        elif path=="players":
            ttl=21600
        elif path=="fixtures":
            ttl=900
        else:
            ttl=900

        with self._lock:
            self.state["cache"][cache_key]=(time.monotonic()+ttl,result)
            if len(self.state["cache"])>2500:
                now=time.monotonic()
                self.state["cache"]={k:v for k,v in self.state["cache"].items() if v[0]>now}
        return result

'''
    return text[:start] + new_get + text[end:]

def patch_deep(text: str) -> str:
    if "def _pool_xg_profile(" not in text:
        marker = "\ndef _api_xg_profile(api, team_id, team_name, kickoff_iso, n=3):"
        helper = r'''
def _pool_xg_profile(api,pool,team_name,kickoff_iso,n=3):
    vals_for=[];vals_against=[];shots=[];big=[]
    for fx in _recent_team_fixtures(pool,team_name,kickoff_iso,max(n,3))[:n]:
        fid=(fx.get("fixture") or {}).get("id")
        teams=fx.get("teams") or {}
        h=(teams.get("home") or {}).get("name","")
        a=(teams.get("away") or {}).get("name","")
        opp=a if norm_name(h)==norm_name(team_name) else h if norm_name(a)==norm_name(team_name) else ""
        if not fid or not opp:continue
        try:payload=api.fixture_statistics(fid)
        except Exception:continue
        own=_parse_fixture_stats(payload,team_name)
        other=_parse_fixture_stats(payload,opp)
        ox=own.get("expected_goals")
        if ox is None:ox=own.get("expected_goal")
        ax=other.get("expected_goals")
        if ax is None:ax=other.get("expected_goal")
        if ox is None or ax is None:continue
        try:
            vals_for.append(float(ox));vals_against.append(float(ax))
        except (TypeError,ValueError):
            continue
        if own.get("total_shots") is not None:shots.append(float(own["total_shots"]))
        for key in ("big_chances","big_chances_created"):
            if own.get(key) is not None:
                big.append(float(own[key]));break
    return {
        "xg_for":sum(vals_for)/len(vals_for) if len(vals_for)>=n else None,
        "xg_against":sum(vals_against)/len(vals_against) if len(vals_against)>=n else None,
        "xg_games":len(vals_for),
        "shots":sum(shots)/len(shots) if shots else None,
        "big_chances":sum(big)/len(big) if big else None,
    }

'''
        text = replace_once(text, marker, "\n" + helper + marker, "deep pool xg helper")

    old_sig = "def collect_deep_context(api,pool,home,away,kickoff_iso,season=None,horizon_hours=24):"
    new_sig = "def collect_deep_context(api,pool,home,away,kickoff_iso,season=None,horizon_hours=24,prefer_pool_xg=False,allow_projection=True,player_pages=2):"
    if old_sig in text:
        text = replace_once(text, old_sig, new_sig, "deep signature")
    elif new_sig not in text:
        raise RuntimeError("deep signature unsupported")

    xg_section = r'''    # Recent xG/chance creation. Reuse competition fixture ids first in club mode.
    hp=ap=None
    xg_source="API-Football recent team fixtures/statistics"
    if prefer_pool_xg:
        hp=_pool_xg_profile(api,pool,home,kickoff_iso)
        ap=_pool_xg_profile(api,pool,away,kickoff_iso)
        xg_source="API-Football competition-pool fixture statistics"

    if not hp or hp.get("xg_for") is None or hp.get("xg_against") is None:
        hp=_api_xg_profile(api,htid,home,kickoff_iso)
    if not ap or ap.get("xg_for") is None or ap.get("xg_against") is None:
        ap=_api_xg_profile(api,atid,away,kickoff_iso)

    if hp.get("xg_for") is not None and hp.get("xg_against") is not None:
        hxf,hxa=hp["xg_for"],hp["xg_against"];hbig=hp.get("big_chances")
    else:
        hfor=_xg_average(api,pool,home,kickoff_iso)
        hxf=hfor["xg"];hxa=_opponent_xga(api,pool,home,kickoff_iso);hbig=hfor.get("big_chances")
    if ap.get("xg_for") is not None and ap.get("xg_against") is not None:
        axf,axa=ap["xg_for"],ap["xg_against"];abig=ap.get("big_chances")
    else:
        afor=_xg_average(api,pool,away,kickoff_iso)
        axf=afor["xg"];axa=_opponent_xga(api,pool,away,kickoff_iso);abig=afor.get("big_chances")

    ctx.update(
        home_xg_for=hxf,home_xg_against=hxa,
        away_xg_for=axf,away_xg_against=axa,
        home_big_chances=hbig,away_big_chances=abig,
        xg_source=xg_source,big_chance_source=xg_source,
        xg_fallback_used=False,
        xg_samples_home=hp.get("xg_games"),
        xg_samples_away=ap.get("xg_games"),
        xg_checked_at=datetime.now(timezone.utc).isoformat(),
    )

'''
    text = replace_section(
        text,
        "    # Recent xG and chance creation.",
        "    # Confirmed XIs.",
        xg_section,
        "deep xg section",
    )

    s = text.find("    # Season player importance is optional")
    e = text.find("    return ctx", s)
    if s < 0 or e < 0:
        raise RuntimeError("deep importance section markers missing")

    tail = r'''    # Fetch injuries first. Player pages are fetched only when XI/injury/projection needs them.
    injuries=[]
    injury_request_ok=False
    try:
        injuries=api.injuries(fid) if fid else []
        injury_request_ok=bool(fid) and isinstance(injuries,list)
    except Exception:
        injuries=[]
        injury_request_ok=False
    ctx["injury_available"]=injury_request_ok
    ctx["injury_source"]="API-Football injuries"

    season=int(season or target_utc.year)
    importance={"home":{},"away":{}}
    need_importance=bool(ids["home"] or ids["away"] or injuries or allow_projection)
    if need_importance:
        pages=max(1,int(player_pages))
        for side,tid in (("home",htid),("away",atid)):
            if not tid:continue
            try:
                importance[side]=_player_importance(api.team_players(tid,season,max_pages=pages))
            except Exception:
                importance[side]={}

    for side in ("home","away"):
        la,ld=_lineup_change(importance[side],ids[side])
        ctx[f"{side}_lineup_attack_pct"]=la
        ctx[f"{side}_lineup_defense_pct"]=ld

    injured_ids={"home":set(),"away":set()}
    for side,tname in (("home",home),("away",away)):
        pids=[]
        for r in injuries or []:
            if norm_name((r.get("team") or {}).get("name",""))!=norm_name(tname):continue
            p=(r.get("player") or {})
            pids.append(p.get("id"))
        injured_ids[side]={int(x) for x in pids if x is not None}
        atk,defn,names=_impact_by_position(importance[side],[x for x in pids if x])
        ctx[f"{side}_injury_attack_pct"]=atk
        ctx[f"{side}_injury_defense_pct"]=defn
        ctx[f"{side}_missing_players"]=names

    projected={"home":[],"away":[]}
    if allow_projection:
        for side in ("home","away"):
            ranked=[(pid,v) for pid,v in importance[side].items() if int(pid) not in injured_ids[side]]
            ranked.sort(key=lambda z:z[1].get("importance",0),reverse=True)
            projected[side]=ranked[:11]
            ids_proj=[pid for pid,_ in projected[side]]
            pa,pdef=_lineup_change(importance[side],ids_proj)
            ctx[f"{side}_probable_lineup_attack_pct"]=pa
            ctx[f"{side}_probable_lineup_defense_pct"]=pdef
            ctx[f"{side}_probable_players"]=[v.get("name","") for _,v in projected[side] if v.get("name")]

    ctx["probable_lineup_available"]=bool(
        allow_projection
        and len(projected["home"])==11
        and len(projected["away"])==11
        and not ctx.get("lineup_confirmed")
    )
    if allow_projection:
        ctx["probable_lineup_source"]="MODEL-PROJECTED XI: API-Football player importance + availability"
    if ctx["probable_lineup_available"] and not ctx.get("lineup_confirmed"):
        ctx["lineup_status"]="PROBABLE"
'''
    return text[:s] + tail + text[e:]

def patch_app(text: str) -> str:
    checkbox = '''    deep_context_on=st.checkbox("🧠 v3 정밀 컨텍스트 (24시간 이내 xG·확정 라인업·결장·선수 영향·휴식일)",value=True,
        help="API-Football 추가 호출을 사용합니다. 실제 반환된 데이터만 반영하며 없는 항목은 MISSING으로 남깁니다.")
'''
    if "club_deep_max_events" not in text:
        text = replace_once(
            text,
            checkbox,
            checkbox + '''    deep_max_events=st.slider("정밀 컨텍스트 최대 경기 수",2,8,5,key="club_deep_max_events",
        help="전체 경기는 1차 독립모델로 분석하고 후보 가능성이 높은 상위 경기만 정밀 API 호출을 합니다.")
    st.caption("쿼터 보호: 전체 1차 스크리닝 → 상위 N경기만 xG·라인업·결장 정밀수집 → 나머지는 REVIEW")
''',
            "app deep controls",
        )

    old_pool = "                pool=build_competition_pool(foot,competition_name,target_year)\n"
    if "_club_competition_pool_cache" not in text:
        text = replace_once(
            text,
            old_pool,
            '''                _pool_cache=st.session_state.setdefault("_club_competition_pool_cache",{})
                _pool_key=f"{competition_name}|{target_year}"
                pool=_pool_cache.get(_pool_key)
                if pool is None:
                    pool=build_competition_pool(foot,competition_name,target_year)
                    _pool_cache[_pool_key]=pool
                    if len(_pool_cache)>8:
                        for _old_key in list(_pool_cache.keys())[:-8]:
                            _pool_cache.pop(_old_key,None)
''',
            "app competition pool cache",
        )

    start_marker = '''                all_rows=[]
                event_meta=[]
                total_events=market["event_id"].nunique() if not market.empty else 0
                failures=[]
'''
    end_marker = '                st.session_state["failures"]=failures'
    if "stage-1 quota-safe screening" not in text:
        staged = r'''                all_rows=[]
                event_meta=[]
                total_events=market["event_id"].nunique() if not market.empty else 0
                failures=[]
                base_results={}
                ordered_events=[]

                # Stage 1: full-board independent screening with no match-specific deep calls.
                for idx,(eid,g) in enumerate(market.groupby("event_id"),start=1):
                    home=g.iloc[0]["home_team"];away=g.iloc[0]["away_team"]
                    st.write(f"[1차 {idx}/{total_events}] {match_label_kst(home,away,g.iloc[0]['commence_time'])}")
                    try:
                        base_pool=dict(pool)
                        base_pool["event_context"]={
                            "deep_context_attempted":False,
                            "deep_context_reason":"stage-1 quota-safe screening",
                            "lineup_confirmed":False,
                            "lineup_status":"NOT_CHECKED",
                        }
                        base_frame,base_meta=analyze_event(g,base_pool,recent_n=recent_n)
                        if base_frame.empty:
                            failures.append({
                                "경기":f"{home} - {away}",
                                "이유":base_meta.get("reason",base_meta.get("status","데이터 없음"))
                            })
                            continue
                        base_results[eid]=(g,base_frame,base_meta)
                        ordered_events.append(eid)
                    except Exception as e:
                        failures.append({"경기":f"{home} - {away}","이유":str(e)})

                deep_event_ids=set()
                if deep_context_on and base_results:
                    now_utc=pd.Timestamp.now(tz="UTC")
                    scored=[]
                    def _mx(frame,col,default=-999.0):
                        if col not in frame.columns:return default
                        s=pd.to_numeric(frame[col],errors="coerce").dropna()
                        return float(s.max()) if not s.empty else default

                    for order,eid in enumerate(ordered_events):
                        g,base_frame,_=base_results[eid]
                        try:
                            target=pd.Timestamp(g.iloc[0]["commence_time"])
                            target=target.tz_convert("UTC") if target.tzinfo else target.tz_localize("UTC")
                            hours=(target-now_utc).total_seconds()/3600
                        except Exception:
                            hours=999.0
                        if hours<0 or hours>24:
                            continue
                        scored.append((
                            _mx(base_frame,"conservative_ev_roi"),
                            _mx(base_frame,"point_ev_roi"),
                            _mx(base_frame,"edge_pp"),
                            _mx(base_frame,"model_win_prob"),
                            -order,eid
                        ))
                    scored.sort(key=lambda x:x[:-1],reverse=True)
                    deep_event_ids={row[-1] for row in scored[:int(deep_max_events)]}
                    st.caption(
                        f"API-Football 쿼터 보호: 1차 {len(base_results)}경기 → "
                        f"정밀수집 {len(deep_event_ids)}경기"
                    )

                quota_stopped=False
                for idx,eid in enumerate(ordered_events,start=1):
                    g,base_frame,base_meta=base_results[eid]
                    home=g.iloc[0]["home_team"];away=g.iloc[0]["away_team"]
                    try:
                        if not deep_context_on:
                            analyzed,meta=base_frame,base_meta
                        else:
                            event_pool=dict(pool)
                            if eid in deep_event_ids:
                                if quota_stopped or not foot.can_spend(estimated=10,reserve=12):
                                    q=foot.quota_status()
                                    event_ctx={
                                        "deep_context_attempted":True,
                                        "deep_context_reason":(
                                            f"quota guard hold: daily remaining={q.get('remaining_day')} "
                                            f"blocked={q.get('blocked_seconds')}s "
                                            f"{q.get('block_reason') or ''}"
                                        ),
                                        "lineup_confirmed":False,
                                        "lineup_status":"QUOTA_HOLD",
                                    }
                                else:
                                    try:
                                        event_ctx=collect_deep_context(
                                            foot,pool,home,away,g.iloc[0]["commence_time"],
                                            season=(pool.get("seasons") or [target_year])[0],
                                            horizon_hours=24,
                                            prefer_pool_xg=True,
                                            allow_projection=False,
                                            player_pages=1,
                                        )
                                    except FootballAccessError as deep_error:
                                        quota_stopped=True
                                        event_ctx={
                                            "deep_context_attempted":True,
                                            "deep_context_reason":f"quota/access hold: {deep_error}",
                                            "lineup_confirmed":False,
                                            "lineup_status":"QUOTA_HOLD",
                                        }
                                    except Exception as deep_error:
                                        event_ctx={
                                            "deep_context_attempted":True,
                                            "deep_context_reason":(
                                                f"collector failed: {type(deep_error).__name__}: {deep_error}"
                                            ),
                                            "lineup_confirmed":False,
                                            "lineup_status":"ERROR",
                                        }
                            else:
                                event_ctx={
                                    "deep_context_attempted":True,
                                    "deep_context_reason":"staged quota guard: 1차 스크리닝 후 정밀수집 대상 밖",
                                    "lineup_confirmed":False,
                                    "lineup_status":"STAGED_OUT",
                                }

                            event_pool["event_context"]=event_ctx
                            analyzed,meta=analyze_event(g,event_pool,recent_n=recent_n)

                        if not analyzed.empty:
                            analyzed["sport_key"]=sport_key
                            analyzed["kickoff_kst"]=format_kst(g.iloc[0]["commence_time"])
                            all_rows.append(analyzed)
                        else:
                            failures.append({
                                "경기":f"{home} - {away}",
                                "이유":meta.get("reason",meta.get("status","데이터 없음"))
                            })
                        event_meta.append(meta)
                    except Exception as e:
                        failures.append({"경기":f"{home} - {away}","이유":str(e)})
                        event_meta.append({
                            "status":"exception","home":home,"away":away,"reason":str(e)
                        })
                st.session_state["failures"]=failures'''
        s = text.find(start_marker)
        e = text.find(end_marker, s)
        if s < 0 or e < 0:
            raise RuntimeError("app main analysis loop markers missing")
        text = text[:s] + staged + text[e + len(end_marker):]

    status_old = '                status.update(label=f"완료 — Odds API 남은 요청량 {remain}",state="complete")'
    if "API-Football 실제 호출" not in text:
        text = replace_once(
            text,
            status_old,
            '''                _fq=foot.quota_status()
                _day="?" if _fq.get("remaining_day") is None else str(_fq.get("remaining_day"))
                _minute="?" if _fq.get("remaining_minute") is None else str(_fq.get("remaining_minute"))
                st.caption(
                    f"API-Football 실제 호출 {_fq.get('requests_made',0)}회 · "
                    f"일일 잔여 {_day} · 분당 잔여 {_minute} · "
                    f"캐시 {_fq.get('cache_entries',0)}개"
                )
                if _fq.get("blocked_seconds",0)>0:
                    st.warning(
                        f"API-Football 정밀 호출 보류 {_fq.get('blocked_seconds')}초 · "
                        f"{_fq.get('block_reason') or '요청 제한'}"
                    )
                status.update(
                    label=f"완료 — Odds API 남은 요청량 {remain} · API-Football 일일잔여 {_day}",
                    state="complete"
                )''',
            "app quota status UI",
        )

    generic = '''if any(getattr(module,"PROVIDER_BUILD",None)!="3.0.0" for module in (live_provider,national_provider,advanced_provider,odds_provider,football_provider,free_provider,auto_national_provider,deep_soccer_provider)):
    st.error("앱과 수집 파일 버전이 다릅니다. ZIP의 sports_ev_engine 폴더까지 전부 반영한 뒤 Streamlit 앱을 Reboot하세요.")
    st.stop()
'''
    if "QUOTA_GUARD_BUILD" not in text:
        text = replace_once(
            text,
            generic,
            generic + '''if getattr(football_provider,"QUOTA_GUARD_BUILD",None)!="3.4.25-staged-quota":
    st.error("API-Football 쿼터 보호 모듈이 구버전입니다. app.py와 sports_ev_engine/providers/api_football.py를 함께 반영한 뒤 Reboot하세요.")
    st.stop()
''',
            "app quota module guard",
        )

    if '_BUILD_ID = "3.4.24-national-validation"' in text:
        text = text.replace(
            '_BUILD_ID = "3.4.24-national-validation"',
            '_BUILD_ID = "3.4.24-national-validation-quota1"',
            1,
        )

    old_caption = 'st.caption("분석 백엔드 v3.4.24 · opponent-strength robust form + adaptive measured-xG single-pass + API-Football/ESPN/FotMob/SofaScore fallback")'
    if old_caption in text:
        text = text.replace(
            old_caption,
            'st.caption("분석 백엔드 v3.4.24 · quota-aware staged deep context + opponent-strength robust form + adaptive measured-xG single-pass")',
            1,
        )
    return text

def main():
    required = {
        "sports_ev_engine/providers/api_football.py": patch_provider,
        "sports_ev_engine/deep_soccer_context.py": patch_deep,
        "app.py": patch_app,
    }
    optional = {
        "api_football.py": patch_provider,
        "deep_soccer_context.py": patch_deep,
        "sports_ev_engine/app.py": patch_app,
    }

    originals = {}
    modified = {}

    for path, fn in required.items():
        originals[path] = read(path)
        modified[path] = fn(originals[path])

    for path, fn in optional.items():
        p = ROOT / path
        if not p.exists():
            continue
        originals[path] = p.read_text(encoding="utf-8")
        try:
            modified[path] = fn(originals[path])
        except Exception as exc:
            print(f"[WARN] optional duplicate not patched: {path}: {exc}")

    # Validate complete modified files before writing anything.
    for path, content in modified.items():
        ast.parse(content, filename=path)

    BACKUP.mkdir(exist_ok=True)
    for path, content in modified.items():
        src = ROOT / path
        backup = BACKUP / path
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(originals[path], encoding="utf-8")
        src.write_text(content, encoding="utf-8")
        print(f"[OK] patched {path}")

    runtime_targets = [
        ROOT / "app.py",
        ROOT / "sports_ev_engine/providers/api_football.py",
        ROOT / "sports_ev_engine/deep_soccer_context.py",
    ]
    cp = subprocess.run(
        [sys.executable, "-m", "py_compile", *[str(p) for p in runtime_targets]],
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        raise RuntimeError("py_compile failed:\n" + cp.stderr)

    print()
    print("PATCH COMPLETE")
    print("- full board -> stage-1 independent screening")
    print("- deep context -> top N only (default 5, UI range 2~8)")
    print("- quota/cache/request diagnostics added")
    print("- 429 Retry-After honored")
    print("- competition-pool xG reuse enabled")
    print("- unnecessary projected-XI player-page calls skipped")
    print()
    print("Commit/upload changed files and Reboot Streamlit.")

if __name__ == "__main__":
    main()
