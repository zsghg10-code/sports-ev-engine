
import os
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.free_national import (download_history, prepare_history, read_csv, combine_history, free_pool, event_context, blank_csv, HISTORY_COLUMNS, CONTEXT_COLUMNS)

@st.cache_data(ttl=3600, show_spinner=False)
def cached_free_history():
    return download_history()

from sports_ev_engine import auto_national as auto_national_provider
from sports_ev_engine.auto_national import collect as collect_automatic, fetch_board, automatic_pool, fetch_lineup, fetch_summary, collect_recent_xg, DataHold

@st.cache_data(ttl=1800, show_spinner=False)
def cached_public_board(league,year):
    return fetch_board(league,year)

@st.cache_data(ttl=300, show_spinner=False)
def cached_public_lineup(event):
    return fetch_lineup(event)

@st.cache_data(ttl=21600, show_spinner=False)
def cached_public_summary(league,event_id):
    return fetch_summary(league,event_id)

from sports_ev_engine.providers.api_football import APIFootball
from sports_ev_engine.providers import api_football as football_provider
from sports_ev_engine.providers.mlb_statsapi import schedule as mlb_schedule, schedule_kst as mlb_schedule_kst, match_schedule as mlb_match_schedule
from sports_ev_engine.providers.mlb_context import MLBContextProvider
from sports_ev_engine.market import consensus, clean_odds
from sports_ev_engine import auto_soccer as auto_soccer_provider
from sports_ev_engine.models import elo as elo_provider
from sports_ev_engine.auto_soccer import analyze_event
from sports_ev_engine.competition_form import build_competition_pool
from sports_ev_engine import reasoning_engine as reasoning_provider
from sports_ev_engine.national_soccer import is_senior_international, build_national_event_pool
from sports_ev_engine.international_discovery import (discover_api_football_fixtures, flatten_api_football_odds, append_only_missing_events, competition_rows, kst_target_dates)
from sports_ev_engine.core.parlay import optimize_parlays
from sports_ev_engine.review_policy import candidate_mask, review_mask
from sports_ev_engine.national_policy import reference_pairs
from sports_ev_engine.analysis_view import match_summary
from sports_ev_engine.explanations import humanize_policy_reason
from sports_ev_engine.validation import validate
from sports_ev_engine.providers.official_baseball import OfficialBaseballStats
from sports_ev_engine.official_baseball_model import analyze_official_event
from sports_ev_engine.providers.live_baseball import LiveBaseballContext
from sports_ev_engine.providers.baseball_advanced import AdvancedBaseballSignals
from sports_ev_engine.providers import live_baseball as live_provider
from sports_ev_engine import national_soccer as national_provider
from sports_ev_engine import free_national as free_provider
from sports_ev_engine.providers import baseball_advanced as advanced_provider, the_odds_api as odds_provider
from sports_ev_engine.deep_soccer_context import collect_deep_context, merge_xg_fallback
from sports_ev_engine import national_context_pipeline as national_context_provider
from sports_ev_engine.national_context_pipeline import collect_national_context
from sports_ev_engine import deep_soccer_context as deep_soccer_provider
from sports_ev_engine.prediction_store import record_frame, auto_settle, evaluation, pending_sport_keys, configure_persistence, persistence_status, refresh_events, load_market_observations, load_settled
from sports_ev_engine.providers.mlb_postgame import analyze_settled_mlb, postgame_reviews
from sports_ev_engine.final_view import split_events, compact_table, event_summary
from sports_ev_engine.baseball_diagnostics import build_baseball_diagnostics, diagnostic_counts
from sports_ev_engine.kst_schedule import format_kst
from sports_ev_engine.daily_combo import latest_snapshots_for_kst_date, prepare_daily_candidates, best_combos, combo_display_rows
from sports_ev_engine.smart_refresh import run_smart_cycle
from sports_ev_engine.change_log import change_logs
from sports_ev_engine.adaptive_model import calibration_curve
from sports_ev_engine.data_freshness import freshness_rows
from sports_ev_engine.postgame_stats import failure_statistics
from sports_ev_engine.replay_backtest import replay_day, version_backtest
from sports_ev_engine.lineup_stage import resolve_lineup_stage
from sports_ev_engine.source_health import source_health_rows
from sports_ev_engine.paper_trade import paper_summary
from sports_ev_engine.feature_attribution import attribution
from sports_ev_engine.model_drift import drift_rows
from sports_ev_engine.bankroll import simulate as simulate_bankroll

st.set_page_config(page_title="Sports EV Engine v3.4.16",layout="wide")
st.title("Sports EV Engine v3.4.16")
st.caption("BUILD v3.4.16-robust-form-xg · 2026-09-29")
if any(getattr(module,"PROVIDER_BUILD",None)!="3.0.0" for module in (live_provider,national_provider,advanced_provider,odds_provider,football_provider,free_provider,auto_national_provider,deep_soccer_provider)):
    st.error("앱과 수집 파일 버전이 다릅니다. ZIP의 sports_ev_engine 폴더까지 전부 반영한 뒤 Streamlit 앱을 Reboot하세요.")
    st.stop()
_PATCH_BUILD = "3.4.16-robust-form-xg"
_patch_modules=(auto_national_provider,deep_soccer_provider,free_provider,auto_soccer_provider,reasoning_provider,national_context_provider,elo_provider)
_patch_mismatch=[getattr(m,"__name__",str(m)) for m in _patch_modules if getattr(m,"PATCH_BUILD",None)!=_PATCH_BUILD]
if _patch_mismatch:
    st.error("v3.4.16 핵심 축구 모델 모듈이 섞여 있습니다: " + ", ".join(_patch_mismatch) + ". DEPLOY_ONLY ZIP의 app.py와 sports_ev_engine 폴더를 함께 덮어쓴 뒤 Reboot하세요.")
    st.stop()
FootballAccessError=football_provider.FootballAccessError
st.caption("분석 백엔드 v3.4.16 · opponent-strength robust form + adaptive measured-xG single-pass + API-Football/ESPN/FotMob/SofaScore fallback")
st.caption("독립 모델 → 정밀 컨텍스트 → 반증 검사 → 시장 캘리브레이션 → 27개 스트레스 시나리오 → EV/ROBUST 판정 → 기록·사후검증")

def secret(name):
    try:
        if name in st.secrets:return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name)

ODDS_KEY=secret("THE_ODDS_API_KEY")
FOOTBALL_KEY=secret("API_FOOTBALL_KEY")
SUPABASE_URL=secret("SUPABASE_URL")
SUPABASE_KEY=secret("SUPABASE_SERVICE_ROLE_KEY") or secret("SUPABASE_KEY")
configure_persistence(SUPABASE_URL,SUPABASE_KEY)
BASEBALL_KEY=None

_BUILD_ID = "3.4.16-robust-form-xg"
if st.session_state.get("_build_id") != _BUILD_ID:
    for _k in [
        "baseball_ranked","baseball_failures","baseball_meta","baseball_live_rows",
        "ranked","failures","event_meta","raw_odds","market","baseball_raw_odds","baseball_market","mlb_ranked","mlb_failures","mlb_status","mlb_schedule","mlb_market","mlb_raw_odds","mlb_filter_label","national_ranked","national_failures","national_evidence","national_sources","national_status","national_lineups","national_discovery_status","validation_records","validation_report","club_filter_label","national_filter_label","baseball_filter_label"
    ]:
        st.session_state.pop(_k, None)
    st.session_state["_build_id"] = _BUILD_ID



KST=ZoneInfo("Asia/Seoul")

def render_kst_calendar(prefix, *, range_options=None, range_index=0):
    """Calendar-first filter. The provider stays UTC; comparison is always KST."""
    c1,c2=st.columns([1,2])
    date_only=c1.checkbox("📅 특정 날짜만 분석 (KST)",value=True,key=f"{prefix}_date_only")
    selected=c2.date_input(
        "경기 날짜(KST)",
        value=datetime.now(KST).date(),
        key=f"{prefix}_match_date",
        help="UTC가 아니라 한국시간(KST) 00:00~23:59 기준으로 경기를 묶습니다.",
    )
    scope=None
    if range_options:
        scope=st.selectbox(
            "경기 범위",range_options,index=range_index,key=f"{prefix}_scope",
            disabled=date_only,
            help="특정 날짜 필터를 끄면 기존 기간 조회를 사용할 수 있습니다.",
        )
    if date_only:
        st.caption(f"📅 {selected:%Y-%m-%d} KST 경기만 분석 · 경기시간도 모두 KST로 표시")
    return date_only, selected, scope

def apply_kst_filter(frame, *, date_only, selected_date, scope=None, future_only=False):
    if frame is None or frame.empty:
        return frame
    ts=pd.to_datetime(frame["commence_time"],utc=True,errors="coerce").dt.tz_convert(KST)
    now=datetime.now(KST)
    mask=ts.notna()
    if future_only:
        mask &= ts>now
    if date_only:
        mask &= ts.dt.date==selected_date
    elif scope and scope!="전체":
        if scope=="오늘(KST)":
            mask &= ts.dt.date==now.date()
        elif scope=="앞으로 3일":
            mask &= (ts>=now)&(ts<=now+timedelta(days=3))
        elif scope=="앞으로 7일":
            mask &= (ts>=now)&(ts<=now+timedelta(days=7))
    return frame.loc[mask].copy()

def match_label_kst(home,away,kickoff):
    return f"{home} - {away} · {format_kst(kickoff)}"


def render_final_decision_layer(frame, key_prefix, title="🧠 v3 FINAL Decision Layer", include_spread=False):
    """Manual-analysis style per-match presentation for v3 outputs."""
    events=split_events(frame)
    if not events:
        return
    # Put the strongest ROBUST/+EV match first so the useful result is visible immediately.
    scored=[]
    for key,label,g in events:
        cand=g[g.get("v3_candidate",pd.Series(False,index=g.index)).fillna(False).astype(bool)].copy()
        robust=cand[cand.get("v3_decision_status",pd.Series("",index=cand.index)).eq("ROBUST")] if not cand.empty else cand
        src=robust if not robust.empty else cand
        p10=pd.to_numeric(src.get("robust_ev_p10"),errors="coerce").max() if not src.empty and "robust_ev_p10" in src else float("nan")
        ev=pd.to_numeric(src.get("ev_roi"),errors="coerce").max() if not src.empty and "ev_roi" in src else float("nan")
        scored.append((0 if not robust.empty else 1 if not cand.empty else 2, -(p10 if pd.notna(p10) else -9), -(ev if pd.notna(ev) else -9), key,label,g))
    scored.sort(key=lambda x:(x[0],x[1],x[2],x[4]))
    events=[(x[3],x[4],x[5]) for x in scored]
    st.markdown(f"### {title}")
    st.caption("내가 경기 하나를 수동 분석할 때처럼 승/무/패와 대표 O/U를 한 표에 모으고, +EV와 강건성·반증 위험·라인업 상태를 분리해 보여줍니다.")
    labels={key:label for key,label,_ in events}
    selected=st.selectbox("경기 선택",[key for key,_,_ in events],format_func=lambda k:labels[k],key=f"{key_prefix}_final_event")
    key,label,g=next(x for x in events if x[0]==selected)
    st.markdown(f"#### {label}")
    final_table=compact_table(g,include_spread=include_spread)
    st.dataframe(final_table,hide_index=True,use_container_width=True)
    summary=event_summary(g)
    summary_lines=[
        f"**모델 최우선 후보:** {summary['model_best']}",
        f"**토탈 최우선 후보:** {summary['total_best']}",
    ]
    if include_spread:
        summary_lines.append(f"**런라인 최우선 후보:** {summary.get('spread_best','없음')}")
    summary_lines += [
        f"**다폴:** {summary['parlay']}",
        f"**선정/판정 이유:** {summary.get('selection_reason','-')}",
        f"**주요 실패경로:** {summary['failure']}",
        f"**데이터/모델 리스크:** {summary.get('data_risk','-')}",
        f"**데이터 상태:** {summary['data_status']}",
        f"**Model confidence:** {summary['confidence']}/100",
    ]
    st.markdown("  \n".join(summary_lines))
    ratio=summary.get('robust_positive_ratio')
    n=summary.get('robust_scenario_count',0)
    p10=summary.get('robust_ev_p10')
    if pd.notna(ratio) and n:
        positive=int(round(float(ratio)*int(n)))
        p10_text=f" · P10 EV {float(p10)*100:+.1f}%" if pd.notna(p10) else ""
        st.caption(f"강건성: {positive}/{n}개 스트레스 시나리오에서 +EV{p10_text} · confidence는 확률이 아니라 데이터 완성도/불확실성/시장 충돌/강건성을 합친 휴리스틱 점수입니다.")
    else:
        st.caption("confidence는 적중확률이 아니라 데이터 완성도·불확실성·시장 충돌을 요약한 휴리스틱 점수입니다.")
    with st.expander("이 경기의 v3 근거·결측 신호·내부 판정코드 보기"):
        if summary.get('signal_summary'):
            st.write("확인 신호:",summary['signal_summary'])
        if summary.get('missing_signals'):
            st.write("MISSING:",summary['missing_signals'])
        detail_cols=[c for c in [
            "kickoff_kst","display_pick","best_book","best_odds","raw_independent_prob","market_prob","model_win_prob",
            "robust_positive_ratio","robust_ev_min","robust_ev_p10","robust_ev_max",
            "counter_case_risk","counter_case_summary","sanity","uncertainty_pp","signal_coverage",
            "lineup_confirmed","stage","data_quality","home_lambda","away_lambda","home_expected_runs","away_expected_runs"
        ] if c in g.columns]
        detail=g[detail_cols].copy()
        for c in ["raw_independent_prob","market_prob","model_win_prob","robust_positive_ratio","signal_coverage"]:
            if c in detail:detail[c]=(pd.to_numeric(detail[c],errors="coerce")*100).round(1)
        for c in ["robust_ev_min","robust_ev_p10","robust_ev_max"]:
            if c in detail:detail[c]=(pd.to_numeric(detail[c],errors="coerce")*100).round(1)
        st.dataframe(detail,hide_index=True,use_container_width=True)
    with st.expander("모든 경기 FINAL 요약"):
        rows=[]
        for _,elabel,eg in events:
            es=event_summary(eg)
            rows.append({"경기":elabel,"모델 최우선":es['model_best'],"토탈 최우선":es['total_best'],"다폴":es['parlay'],"데이터 상태":es['data_status'],"Confidence":es['confidence']})
        st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)

tabs=st.tabs(["⚡ 완전자동 축구","🌍 축구 A매치","⚾ KBO/NPB 자동분석","실시간 배당","시장 가격","MLB","🏆 오늘의 베스트 조합","다폴","📡 모니터링","설정","📊 모델 검증","🧪 모델 연구소","🛡️ 데이터·리스크"])

with tabs[0]:
    st.subheader("클럽 축구 자동분석")
    st.info("국가대표·네이션스리그는 🌍 축구 A매치 탭의 무료 자동 수집을 사용하세요. 클럽 모델과 분리했습니다.")
    st.write("배당·대회 경기 데이터를 자동 수집하고 Elo/상대전력/최근폼으로 독립 확률을 만든 뒤 시장 무마진 확률로 과대괴리를 보정합니다.")
    st.caption("v2.2: 최근 득실만 보던 문제를 수정해 Elo 상대전력 보정 + 시장 prior + 이상치 자동 격리를 적용합니다.")
    if not ODDS_KEY:
        st.warning("THE_ODDS_API_KEY가 필요합니다.")
    if not FOOTBALL_KEY:
        st.warning("API_FOOTBALL_KEY가 필요합니다. 설정 탭에서 연결하면 수동 Elo/xG 입력 없이 자동분석이 작동합니다.")

    c1,c2,c3=st.columns(3)
    if ODDS_KEY:
        try:
            sports=TheOddsAPI(ODDS_KEY).sports()
            soccer=[s for s in sports if s.get("active") and str(s.get("key","")).startswith("soccer_") and not is_senior_international(s)]
            options={f'{s.get("title")} — {s.get("description","")}':s["key"] for s in soccer}
        except Exception:
            options={}
    else:
        options={}
    label=c1.selectbox("클럽 대회",list(options.keys()),index=0,key="club_competition_v291")
    sport_key=options.get(label)
    region=c2.selectbox("배당 지역",["eu","uk","us","au"],index=0)
    recent_n=c3.selectbox("최근 경기 반영", [4,5,6,8,10], index=2)

    club_date_only,club_match_date,date_scope=render_kst_calendar(
        "club",range_options=["오늘(KST)","앞으로 3일","앞으로 7일","전체"],range_index=0
    )

    markets=st.multiselect("분석 마켓",["h2h","spreads","totals"],default=["h2h","spreads","totals"])
    min_books=st.slider("컨센서스 최소 북메이커 수",2,6,3)
    deep_context_on=st.checkbox("🧠 v3 정밀 컨텍스트 (24시간 이내 xG·확정 라인업·결장·선수 영향·휴식일)",value=True,
        help="API-Football 추가 호출을 사용합니다. 실제 반환된 데이터만 반영하며 없는 항목은 MISSING으로 남깁니다.")
    run=st.button("🚀 선택 종목 전체 자동분석",type="primary",disabled=not(ODDS_KEY and FOOTBALL_KEY and sport_key))

    if run:
        try:
            if is_senior_international({"key":sport_key,"title":label}):
                raise ValueError("국가대표 경기는 축구 A매치 탭에서 분석하세요.")
            with st.status("배당과 팀 데이터를 자동 분석 중...",expanded=True) as status:
                odds_api=TheOddsAPI(ODDS_KEY)
                events,headers=odds_api.odds(sport_key,region,",".join(markets))
                raw=clean_odds(odds_api.flatten(events))
                # Calendar comparison is done only after UTC -> KST conversion.
                raw=apply_kst_filter(raw,date_only=club_date_only,selected_date=club_match_date,scope=date_scope)
                st.session_state["club_filter_label"]=(f"{club_match_date:%Y-%m-%d} KST" if club_date_only else str(date_scope))
                if raw.empty:
                    st.warning("선택한 KST 날짜/범위에 배당이 있는 경기가 없습니다.")
                    st.session_state["raw_odds"]=raw
                    st.session_state["market"]=pd.DataFrame()
                    st.session_state["ranked"]=pd.DataFrame()
                    status.update(label="선택 범위 경기 없음",state="complete")
                    st.stop()
                st.session_state["raw_odds"]=raw
                market=consensus(raw,min_books=min_books)
                st.session_state["market"]=market
                st.write(f"배당: {len(events)}경기 / {len(raw)}개 항목")

                foot=APIFootball(FOOTBALL_KEY)

                # Resolve the selected competition once and fetch its current/previous fixture history.
                # The Odds API label is usually "Title — Description"; use the title part.
                competition_name=label.split(" — ")[0].strip()
                target_year=datetime.now(ZoneInfo("Asia/Seoul")).year
                pool=build_competition_pool(foot,competition_name,target_year)
                st.write(
                    f"모델 데이터: API-Football {pool['league_name']} "
                    f"(league_id={pool['league_id']}, seasons={pool['seasons']}, fixtures={len(pool['fixtures'])})"
                )

                all_rows=[]
                event_meta=[]
                total_events=market["event_id"].nunique() if not market.empty else 0
                failures=[]
                for idx,(eid,g) in enumerate(market.groupby("event_id"),start=1):
                    home=g.iloc[0]["home_team"]; away=g.iloc[0]["away_team"]
                    st.write(f"[{idx}/{total_events}] {match_label_kst(home,away,g.iloc[0]['commence_time'])}")
                    try:
                        event_pool=dict(pool)
                        if deep_context_on:
                            try:
                                event_pool["event_context"]=collect_deep_context(
                                    foot,pool,home,away,g.iloc[0]["commence_time"],
                                    season=(pool.get("seasons") or [target_year])[0],horizon_hours=24
                                )
                            except Exception as deep_error:
                                event_pool["event_context"]={"deep_context_attempted":True,"deep_context_reason":f"collector failed: {type(deep_error).__name__}: {deep_error}"}
                        else:
                            event_pool["event_context"]={"deep_context_attempted":False,"deep_context_reason":"disabled in UI"}
                        analyzed,meta=analyze_event(g,event_pool,recent_n=recent_n)
                        if not analyzed.empty:
                            analyzed["sport_key"]=sport_key
                            analyzed["kickoff_kst"]=format_kst(g.iloc[0]["commence_time"])
                            all_rows.append(analyzed)
                        else:
                            failures.append({"경기":f"{home} - {away}","이유":meta.get("reason",meta.get("status","데이터 없음"))})
                        event_meta.append(meta)
                    except Exception as e:
                        failures.append({"경기":f"{home} - {away}","이유":str(e)})
                        event_meta.append({"status":"exception","home":home,"away":away,"reason":str(e)})
                st.session_state["failures"]=failures
                ranked=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
                if not ranked.empty:
                    _v3_order={"ROBUST":0,"SENSITIVE":1,"REVIEW":2,"FRAGILE":3,"PASS":4,"DATA_HOLD":5}
                    ranked["_v3_order"]=ranked["v3_decision_status"].map(_v3_order).fillna(9)
                    ranked=ranked.sort_values(["_v3_order","robust_ev_p10","conservative_ev_roi"],ascending=[True,False,False]).drop(columns=["_v3_order"])
                    ranked["odds_region"]=region
                    saved=record_frame(ranked,sport_key=sport_key,sport_family="soccer_club")
                    settle_info=auto_settle(odds_api,sport_key)
                    st.caption(f"v3 불변 예측 스냅샷 {saved}개 추가 · 자동 정산 {settle_info.get('settled',0)}개" + (f" · 정산 오류: {settle_info.get('error')}" if settle_info.get('error') else ""))
                st.session_state["ranked"]=ranked
                st.session_state["event_meta"]=event_meta
                remain=headers.get("x-requests-remaining")
                status.update(label=f"완료 — Odds API 남은 요청량 {remain}",state="complete")
        except Exception as e:
            st.error(f"자동분석 실패: {e}")

    if "ranked" in st.session_state and not st.session_state["ranked"].empty:
        ranked=st.session_state["ranked"]
        if st.session_state.get("club_filter_label"):st.caption(f"분석 경기일: {st.session_state['club_filter_label']}")
        render_final_decision_layer(ranked,"club",title="🧠 v3 FINAL Decision Layer · 클럽 축구")
        st.markdown("### 상세 진단 · 조건을 통과한 +EV 후보")
        view=ranked[candidate_mask(ranked)].copy()
        if view.empty:st.info("괴리/기대값 기준을 통과한 후보가 없습니다. 검토 대상을 억지로 추천하지 않습니다.")
        cols=["v3_decision_status","robust_positive_ratio","robust_ev_p10","robust_ev_min","counter_case_risk","signal_coverage",
              "grade","sanity","kickoff_kst","display_pick","best_book","best_odds","books",
              "consensus_prob","raw_independent_prob","model_win_prob","push_prob",
              "break_even","raw_market_gap_pp","final_market_gap_pp","edge_pp",
              "ev_roi","conservative_ev_roi","uncertainty_pp","model_weight",
              "home_elo","away_elo","home_form_matches","away_form_matches",
              "home_lambda","away_lambda","missing_signals","counter_case_summary","review_reason"]
        cols=[c for c in cols if c in view.columns]
        view=view[cols]
        for c in ["consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even","model_weight"]:
            view[c]=(view[c]*100).round(1)
        for c in ["ev_roi","conservative_ev_roi","robust_ev_p10","robust_ev_min"]:
            if c in view:view[c]=(view[c]*100).round(1)
        for c in ["robust_positive_ratio","signal_coverage"]:
            if c in view:view[c]=(view[c]*100).round(1)
        view["edge_pp"]=view["edge_pp"].round(1)
        view["raw_market_gap_pp"]=view["raw_market_gap_pp"].round(1)
        view["final_market_gap_pp"]=view["final_market_gap_pp"].round(1)
        view["home_elo"]=view["home_elo"].round(0)
        view["away_elo"]=view["away_elo"].round(0)
        view["home_lambda"]=view["home_lambda"].round(2)
        view["away_lambda"]=view["away_lambda"].round(2)
        st.dataframe(view.head(100),use_container_width=True,hide_index=True)
        review=ranked[review_mask(ranked)]
        st.caption(f"전체 {len(ranked)}개 옵션 · 후보 {len(view)}개 · 검토 {len(review)}개 (경기 수가 아닌 승무패/라인별 옵션 수)")
        with st.expander(f"검토 대상 {len(review)}개 — 추천/자동 다폴 제외"):
            st.dataframe(review[["grade","sanity","display_pick","raw_market_gap_pp","final_market_gap_pp","review_reason","home_form_matches","away_form_matches"]],hide_index=True)
        st.download_button("축구 전체 진단 CSV",ranked.to_csv(index=False).encode("utf-8-sig"),file_name="soccer_diagnostics.csv")


        st.markdown("""
**sanity 해석**
- `OK`: 모델-시장 괴리 정상 범위
- `CHECK`: 원모델과 시장 차이가 다소 큼
- `HIGH_DISAGREEMENT`: 강한 괴리, 후보/자동 다폴 제외
- `OUTLIER_SHRUNK`: 과도한 괴리. 시장 prior로 강하게 축소하고 자동 다폴 제외
""")
        st.caption("raw_independent_prob=Elo+상대보정 최근폼 원모델, model_win_prob=시장 prior로 캘리브레이션한 최종확률. OUTLIER_SHRUNK는 자동 조합 제외.")
    elif "ranked" in st.session_state:
        st.info("현재 필터와 데이터에서 +EV 후보가 없습니다.")

    if st.session_state.get("failures"):
        with st.expander(f"분석하지 못한 경기 {len(st.session_state['failures'])}개 보기"):
            st.dataframe(pd.DataFrame(st.session_state["failures"]),use_container_width=True,hide_index=True)


with tabs[1]:
    st.subheader("🌍 국가대표 축구 A매치 자동분석")
    st.caption("친선전·월드컵 예선·네이션스리그 등에서 배당이 있는 성인 국가대표 경기를 분석합니다. 선택한 소스의 최근 3년 기록을 사용하며, 무료 소스의 대회 범위와 최신성은 아래에서 확인하세요.")
    if not ODDS_KEY:
        st.warning("배당 자동 조회에는 THE_ODDS_API_KEY가 필요합니다. 무료 기록 모드는 API_FOOTBALL_KEY 없이 동작합니다.")
    record_mode=st.radio("A매치 기록 소스",["무료 자동 수집","API-Football"],key="national_record_mode_v290")
    st.caption("무료 모드는 공개 경기 결과를 자동 수집합니다. CSV 입력은 필요 없습니다. 핵심 기록이 부족하면 분석을 보류합니다. 시작 2시간 이내에는 공개 선발 명단도 조회합니다.")
    history_file=None; context_file=None
    with st.expander("무료 자동 수집 범위"):
        st.write("선택 대회와 관련된 국가대표 대회·예선·친선전의 올해/전년도 공개 기록을 수집합니다. 90분 종료가 확인된 경기만 사용하며 연장·승부차기 결과는 제외합니다. 최신성·표본 기준 미달은 분석 보류로 표시합니다.")
        st.write("라인업은 공개 소스에서 선발 11명이 확인될 때만 반영합니다. xG·결장·PPDA 자동 수집은 지원하지 않아 미수집으로 표시합니다. 현재 모델은 백테스트로 검증된 예측기가 아닙니다.")
    try:
        national_catalog=[s for s in TheOddsAPI(ODDS_KEY).sports(all_sports=True) if is_senior_international(s,include_inactive=True)] if ODDS_KEY else []
        national_sports=[s for s in national_catalog if s.get("active",True)]
        national_options={"전체 A매치 · 다중소스 자동발견":["__AUTO_ALL__"]}
        if national_sports:
            national_options["The Odds API 활성 A매치 전체"]=[s["key"] for s in national_sports]
            national_options.update({f'{s.get("title",s["key"])} — {s["key"]}':[s["key"]] for s in national_sports})
        with st.expander("A매치 대회 제공 상태",expanded=True):
            st.caption("v3.4.6부터 목록을 하드코딩하지 않습니다. The Odds API 활성 국제대회는 자동 탐색하고, '전체 A매치 · 다중소스 자동발견'은 API-Football의 해당 날짜 성인 국가대표 일정까지 추가 탐색합니다. The Odds API에 없는 경기라도 API-Football 사전 배당이 있으면 EV 분석을 계속합니다.")
            if national_catalog:
                st.caption("아래 표의 활성/비활성은 **The Odds API 카탈로그만** 뜻합니다. 비활성이어도 `전체 A매치 · 다중소스 자동발견`에서는 API-Football 일정으로 다시 찾습니다.")
                st.dataframe(pd.DataFrame([{
                    "대회":s.get("title",s["key"]),
                    "The Odds API 상태":"활성" if s.get("active",True) else "비활성",
                    "다중소스 처리":"The Odds API 우선" if s.get("active",True) else "API-Football 날짜별 자동탐색",
                    "코드":s["key"]
                } for s in national_catalog]),hide_index=True,use_container_width=True)
            else:
                st.caption("The Odds API 국제대회 목록 없음/키 미설정 — 다중소스 자동발견은 API-Football 키가 있으면 계속 사용할 수 있습니다.")
            st.info("CONCACAF Nations League, Africa Cup of Nations **예선 포함**, African Nations Championship, Gulf/Arab Cup, 월드컵·대륙 예선, 성인 친선전 등은 날짜별 자동발견 대상입니다. 일정은 발견돼도 어느 배당 소스에도 가격이 없으면 '일정 활성 · 배당 없음'으로 표시하며 EV는 만들지 않습니다.")
    except Exception as e:
        national_options={"전체 A매치 · 다중소스 자동발견":["__AUTO_ALL__"]}
        national_sports=[]
        st.warning(f"The Odds API 국가대표 대회 목록을 읽지 못했습니다. 다중소스 자동발견으로 계속 시도합니다: {e}")

    n1,n2,n3=st.columns(3)
    national_label=n1.selectbox("A매치 대회",list(national_options),key="national_sport")
    national_region=n2.selectbox("배당 지역",["eu","uk","us","au"],key="national_region")
    national_recent=n3.selectbox("최근 A매치 반영",[4,5,6,8,10],index=2,key="national_recent")
    national_date_only,national_match_date,national_scope=render_kst_calendar(
        "national",range_options=["오늘(KST)","앞으로 3일","앞으로 7일","전체"],range_index=1
    )
    national_markets=st.multiselect("A매치 분석 마켓",["h2h","spreads","totals"],default=["h2h","totals"],key="national_markets")
    national_books=st.slider("A매치 최소 북메이커",1,6,2,key="national_books")
    national_deep=st.checkbox("🧠 API-Football v3 정밀 컨텍스트 추가",value=True,key="national_deep",
        help="기록 소스와 독립적으로 API-Football 키가 있으면 경기 24시간 이내 xG/확정 라인업/결장/선수 중요도를 추가 수집합니다. 예상(Probable) 라인업은 확정으로 취급하지 않습니다.")

    # Lightweight date preflight. API-Football accepts timezone on /fixtures; asking
    # for the selected KST day directly prevents AFCON/other internationals from being
    # hidden by UTC-calendar boundaries. The provider class caches this request for 15m,
    # so the subsequent full analysis normally reuses it without another network call.
    pre_discovered=[]; pre_discovery_diag=[]
    if national_label=="전체 A매치 · 다중소스 자동발견" and FOOTBALL_KEY:
        try:
            _pre_dates=kst_target_dates(national_date_only,national_match_date,national_scope,datetime.now(ZoneInfo("Asia/Seoul")).date())
            pre_discovered,pre_discovery_diag=discover_api_football_fixtures(APIFootball(FOOTBALL_KEY),_pre_dates)
            st.markdown("#### 📅 선택 날짜 API-Football 성인 A매치 자동발견")
            if pre_discovered:
                st.dataframe(pd.DataFrame(competition_rows(pre_discovered)),hide_index=True,use_container_width=True)
                st.caption(f"성인 A매치 일정 {len(pre_discovered)}경기를 먼저 발견했습니다. 여기의 '일정 활성'은 배당 확보와 별개이며, 아래 자동분석에서 The Odds API/API-Football 사전 배당을 결합합니다.")
            else:
                st.warning("선택 날짜에 API-Football 성인 A매치 일정을 아직 찾지 못했습니다. '실제 수집 결과 · 소스 진단'에서 fixtures 응답/필터 상태를 확인하세요.")
                if pre_discovery_diag:
                    st.dataframe(pd.DataFrame(pre_discovery_diag),hide_index=True,use_container_width=True)
        except Exception as _pre_err:
            st.warning(f"선택 날짜 A매치 일정 사전확인 실패: {_pre_err}")

    national_run=st.button("🌍 선택 대회 A매치 자동분석",type="primary",disabled=not((ODDS_KEY or FOOTBALL_KEY) and (record_mode!="API-Football" or FOOTBALL_KEY) and national_label and national_markets))
    if national_run:
        try:
            with st.status("국가대표 배당과 최근 A매치 기록을 분석 중...",expanded=True) as status:
                st.session_state["national_ranked"]=pd.DataFrame()
                st.session_state["national_failures"]=[]
                st.session_state["national_evidence"]=[]
                records=[]; context_frame=None; public_events=[]
                st.session_state["national_sources"]=[]
                st.session_state["national_status"]=[]
                st.session_state["national_lineups"]=[]
                st.session_state["national_discovery_status"]=[]
                odds_api=TheOddsAPI(ODDS_KEY) if ODDS_KEY else None
                events=[]; headers={}; event_sports={}; competition_errors=[]
                selected_keys=national_options[national_label]
                auto_all="__AUTO_ALL__" in selected_keys
                theodds_keys=[s["key"] for s in national_sports] if auto_all else [k for k in selected_keys if not str(k).startswith("__")]
                # Primary odds source: every currently-active senior international key discovered from The Odds API.
                if odds_api:
                    for sport_key in theodds_keys:
                        try:
                            batch,headers=odds_api.odds(sport_key,national_region,",".join(national_markets))
                            events.extend(batch)
                            event_sports.update({e["id"]:sport_key for e in batch})
                        except Exception as e:
                            competition_errors.append({"경기":sport_key,"이유":str(e)})
                raw_primary=clean_odds(odds_api.flatten(events)) if (odds_api and events) else pd.DataFrame()
                if not raw_primary.empty:
                    raw_primary["odds_source"]="The Odds API"
                    raw_primary=apply_kst_filter(raw_primary,date_only=national_date_only,selected_date=national_match_date,scope=national_scope,future_only=True)

                # Secondary discovery/price source: API-Football date fixtures + pre-match odds.
                # This catches competitions that are not listed by The Odds API (e.g. CONCACAF NL,
                # Gulf/Arab cups, some AFCON/qualification windows and senior friendlies).
                raw_fallback=pd.DataFrame(); discovered_fixtures=[]; discovery_diag=[]
                if auto_all and FOOTBALL_KEY:
                    try:
                        kst_dates=kst_target_dates(national_date_only,national_match_date,national_scope,datetime.now(ZoneInfo("Asia/Seoul")).date())
                        discovery_api=APIFootball(FOOTBALL_KEY)
                        if pre_discovered and set(kst_dates)==set(_pre_dates):
                            discovered_fixtures=list(pre_discovered); discovery_diag=list(pre_discovery_diag)
                        else:
                            discovered_fixtures,discovery_diag=discover_api_football_fixtures(discovery_api,kst_dates)
                        fixture_map={int((fx.get("fixture") or {}).get("id")):fx for fx in discovered_fixtures if (fx.get("fixture") or {}).get("id") is not None}
                        odds_payload=[]
                        league_jobs={}
                        for fx in discovered_fixtures:
                            lg=fx.get("league") or {}; f=fx.get("fixture") or {}
                            try:
                                fts=pd.Timestamp(f.get("date"))
                                if fts.tzinfo is None: fts=fts.tz_localize("UTC")
                                api_date=fts.tz_convert("UTC").date().isoformat()
                                league_jobs[(int(lg.get("id")),int(lg.get("season")),api_date)]=None
                            except Exception:
                                continue
                        for lid,season,kdate in league_jobs:
                            try:
                                odds_payload.extend(discovery_api.odds_for_league_date(lid,season,kdate,max_pages=3))
                            except Exception as oe:
                                competition_errors.append({"경기":f"API-Football league {lid} {kdate}","이유":str(oe)})
                        raw_fallback=clean_odds(flatten_api_football_odds(odds_payload,fixture_map,markets=national_markets))
                        if not raw_fallback.empty:
                            raw_fallback=apply_kst_filter(raw_fallback,date_only=national_date_only,selected_date=national_match_date,scope=national_scope,future_only=True)
                            for eid in raw_fallback["event_id"].dropna().unique():
                                fid=int(str(eid).split(":",1)[1])
                                lg=(fixture_map.get(fid) or {}).get("league") or {}
                                event_sports[eid]=f"api_football:{lg.get('id','international')}"
                        st.session_state["national_discovery_status"]=competition_rows(discovered_fixtures,append_only_missing_events(raw_primary,raw_fallback))
                        st.session_state["national_sources"].extend(discovery_diag)
                    except Exception as de:
                        competition_errors.append({"경기":"다중소스 A매치 자동발견","이유":str(de)})
                        st.session_state["national_discovery_status"]=[]
                elif auto_all:
                    st.session_state["national_discovery_status"]=[]
                    competition_errors.append({"경기":"다중소스 A매치 자동발견","이유":"API_FOOTBALL_KEY 없음 — The Odds API 활성 국제대회만 조회"})

                raw=append_only_missing_events(raw_primary,raw_fallback)
                st.session_state["national_filter_label"]=(f"{national_match_date:%Y-%m-%d} KST" if national_date_only else str(national_scope))
                market=consensus(raw,min_books=national_books) if not raw.empty else pd.DataFrame()
                if record_mode=="무료 자동 수집" and not market.empty:
                    public_records,public_events,diagnostics=collect_automatic((["all_international"] if auto_all else (theodds_keys if theodds_keys else ["all_international"])),fetch=cached_public_board,progress=lambda i,n,d:st.write(f"공개 기록 {i}/{n}: {d['소스']} — {d['상태']}"))
                    st.session_state["national_sources"].extend(diagnostics)
                    if any(d['상태']=='수집 실패' for d in diagnostics):
                        st.warning("일부 공개 소스 수집에 실패했습니다. 확보한 기록으로 기준을 검사하며, 전체 최근 경기 수집을 보장하지 않습니다. 아래 소스 진단을 확인하세요.")
                    try:
                        baseline,_=cached_free_history()
                        records=combine_history(baseline,public_records)
                    except Exception as e:
                        records=public_records
                        st.session_state["national_sources"].append({"소스":"공개 친선전 보조 자료","상태":"수집 실패","이유":str(e)})
                if record_mode=="무료 자동 수집":st.session_state["validation_records"]=records
                all_rows=[]; failures=list(competition_errors); cache={}; access_blocked=False
                if record_mode=="API-Football":st.caption("API-Football 요청은 한도 보호를 위해 간격을 두고 전송하며, 성공한 기록은 재사용합니다. 최초 전체 조회는 몇 분 걸릴 수 있습니다.")
                if not market.empty:
                    foot=APIFootball(FOOTBALL_KEY) if record_mode=="API-Football" else None
                    # History source and deep pre-match context are independent.
                    # Free-history mode may still use API-Football for target fixture xG/lineup/injuries.
                    context_api=foot if foot is not None else (APIFootball(FOOTBALL_KEY) if national_deep and FOOTBALL_KEY else None)
                    for idx,(eid,g) in enumerate(market.groupby("event_id"),1):
                        home,away=g.iloc[0][["home_team","away_team"]]
                        st.write(f"[{idx}/{market['event_id'].nunique()}] {match_label_kst(home,away,g.iloc[0]['commence_time'])}")
                        try:
                            kickoff=g.iloc[0]["commence_time"]
                            match_label=match_label_kst(home,away,kickoff)
                            pool=build_national_event_pool(foot,home,away,kickoff,cache,national_recent) if foot else automatic_pool(records,public_events,home,away,kickoff,national_recent)
                            pool["manual_context"]={}
                            if not foot and any(d.get('상태')=='수집 실패' for d in st.session_state['national_sources']):
                                pool['extra_uncertainty']=pool.get('extra_uncertainty',0)+2.0
                            if not foot:
                                from sports_ev_engine.models.soccer_auto import norm_name
                                matches=[e for e in public_events if norm_name(e['home'])==norm_name(home) and norm_name(e['away'])==norm_name(away) and pd.Timestamp(e['kickoff'])==pd.Timestamp(kickoff)]
                                if len(matches)==1:
                                    pool['venue_unknown']=matches[0].get('neutral') is None
                                    try:pool['manual_context']=cached_public_lineup(matches[0])
                                    except Exception as exc:st.session_state['national_sources'].append({'소스':f'{home} - {away} 라인업','상태':'수집 실패','이유':str(exc)})
                            for side,info in pool.get("evidence",{}).items():
                                st.session_state["national_evidence"].append({"경기":f"{home} - {away}","팀":home if side=="home" else away,**info})
                            if national_deep:
                                # API-Football and public measured-xG fallback are independent.
                                # A rate-limit/fixture failure must not prevent ESPN/FotMob checks.
                                pool["event_context"]=collect_national_context(
                                    context_api,pool,home,away,kickoff,
                                    season=pd.Timestamp(kickoff).year,horizon_hours=24,
                                    public_events=public_events,summary_fetch=cached_public_summary,
                                    enable_deep=True,
                                )
                            else:
                                pool["event_context"]={"deep_context_attempted":False,"deep_context_reason":"deep context disabled","lineup_confirmed":False,"lineup_status":"NOT_CHECKED"}
                            deep=pool.get("event_context") or {}
                            lineup_stage=resolve_lineup_stage(deep,pool.get("manual_context") or {})
                            if lineup_stage.get("confirmed") or lineup_stage.get("probable"):
                                st.session_state["national_lineups"].append({
                                    "경기":match_label,
                                    "상태":lineup_stage.get("label"),
                                    "홈 포메이션":lineup_stage.get("home_formation") or "—",
                                    "홈 선발/예상":" · ".join(lineup_stage.get("home_players") or []),
                                    "원정 포메이션":lineup_stage.get("away_formation") or "—",
                                    "원정 선발/예상":" · ".join(lineup_stage.get("away_players") or []),
                                    "소스":lineup_stage.get("source") or "—",
                                    "fallback":"YES" if lineup_stage.get("fallback_used") else "NO",
                                })
                            analyzed,meta=analyze_event(g,pool,recent_n=national_recent)
                            if analyzed.empty:
                                failures.append({"경기":match_label,"이유":meta.get("reason","기록 부족")})
                                st.session_state["national_status"].append({"경기":match_label,"경기시간(KST)":format_kst(kickoff),"상태":"자료 부족 · 분석 보류","이유":meta.get("reason","기록 부족")})
                            else:
                                analyzed["sport_key"]=event_sports.get(eid,"")
                                analyzed["odds_source"]="API-Football" if str(eid).startswith("af:") else "The Odds API"
                                analyzed["kickoff_kst"]=format_kst(kickoff)
                                all_rows.append(analyzed)
                                deep=pool.get("event_context") or {}
                                lineup_stage=resolve_lineup_stage(deep,pool.get("manual_context") or {})
                                lineup_ok=bool(lineup_stage.get("confirmed"))
                                lineup_label=lineup_stage.get("label") or "미확인"
                                analyzed["lineup_confirmed"]=lineup_ok
                                analyzed["probable_lineup"]=bool(lineup_stage.get("probable"))
                                analyzed["lineup_source"]=lineup_stage.get("source")
                                analyzed["lineup_status"]=lineup_stage.get("stage")
                                analyzed["lineup_fallback_used"]=bool(lineup_stage.get("fallback_used"))
                                analyzed["stage"]="FINAL" if lineup_ok else ("PROBABLE" if lineup_stage.get("probable") else "PRE-LINEUP")
                                deep_bits=[]
                                if deep.get("deep_context_attempted"):
                                    deep_bits.append("정밀 컨텍스트 조회")
                                    xg_ok=all(deep.get(k) is not None for k in ("home_xg_for","home_xg_against","away_xg_for","away_xg_against"))
                                    xg_h=int(deep.get("xg_samples_home") or 0); xg_a=int(deep.get("xg_samples_away") or 0)
                                    if xg_ok:
                                        deep_bits.append("xG 반영 (다중 공개소스 fallback)" if deep.get("xg_fallback_used") else "xG 반영")
                                    elif xg_h or xg_a:
                                        deep_bits.append(f"xG 부분수집 홈 {xg_h}/3 · 원정 {xg_a}/3 · 자동후보 제외")
                                    elif deep.get("xg_collection_status") == "ERROR":
                                        deep_bits.append("xG 수집기 오류 · 자동후보 제외")
                                    else:
                                        deep_bits.append("xG 미수집 · 자동후보 제외")
                                    deep_bits.append("라인업 확정" if lineup_ok else "확정 라인업 미게시")
                                else:
                                    deep_bits.append("정밀 컨텍스트 미조회")
                                st.session_state["national_status"].append({
                                    "경기":match_label,"경기시간(KST)":format_kst(kickoff),"상태":"분석 가능",
                                    "이유":"기록 기준 충족 · " + " · ".join(deep_bits),
                                    "라인업":lineup_label,
                                    "라인업 소스":lineup_stage.get("source") or "—",
                                    "xG":"반영" if all(deep.get(k) is not None for k in ("home_xg_for","home_xg_against","away_xg_for","away_xg_against")) else ("부분수집" if (deep.get('xg_samples_home') or deep.get('xg_samples_away')) else "미수집"),
                                    "xG 상태":deep.get("xg_collection_status") or "NOT_CHECKED",
                                    "xG 소스":deep.get("xg_source") or deep.get("xg_fallback_source") or "—",
                                    "xG 시도":deep.get("xg_sources_tried") or ("API-Football" if deep.get("deep_context_attempted") else "—"),
                                    "xG 표본":f"홈 {deep.get('xg_samples_home') or 0}/3 · 원정 {deep.get('xg_samples_away') or 0}/3",
                                    "xG 후보경기":f"홈 {deep.get('xg_candidates_home') or 0} (검사 {deep.get('xg_checked_home') or 0}) · 원정 {deep.get('xg_candidates_away') or 0} (검사 {deep.get('xg_checked_away') or 0})",
                                    "xG 진단":deep.get("xg_errors") or deep.get("xg_fallback_error") or deep.get("xg_pipeline_invariant_error") or "—",
                                    "xG 적용":deep.get("xg_application_mode") or ("single_pass_blend" if all(deep.get(k) is not None for k in ("home_xg_for","home_xg_against","away_xg_for","away_xg_against")) else "—")
                                })
                        except DataHold as e:
                            failures.append({"경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),"이유":str(e)})
                            st.session_state["national_status"].append({"경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),"경기시간(KST)":format_kst(g.iloc[0]["commence_time"]),"상태":"자료 부족 · 분석 보류","이유":str(e)})
                        except FootballAccessError as e:
                            access_blocked=True
                            failures.append({"경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),"이유":str(e)})
                            failures.append({"경기":"남은 경기 조회 중단","이유":"동일 계정의 플랜/요청 제한이므로 반복 요청하지 않습니다."})
                            st.error(str(e))
                            break
                        except Exception as e:
                            failures.append({"경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),"이유":str(e)})
                            st.session_state["national_status"].append({"경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),"경기시간(KST)":format_kst(g.iloc[0]["commence_time"]),"상태":"수집/분석 실패","이유":str(e)})
                ranked=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
                if not ranked.empty:
                    ranked=ranked.sort_values(["robust_positive_ratio","robust_ev_p10","scenario_ev_min","point_ev_roi"],ascending=False)
                    ranked["odds_region"]=national_region
                    saved=record_frame(ranked,sport_family="soccer_national")
                    settled_total=0; settlement_errors=[]
                    for sk in sorted(x for x in ranked.get("sport_key",pd.Series(dtype=str)).dropna().unique() if x and not str(x).startswith("api_football:")):
                        if not odds_api: break
                        info=auto_settle(odds_api,sk);settled_total+=info.get("settled",0)
                        if info.get("error"):settlement_errors.append(f"{sk}: {info['error']}")
                    st.caption(f"v3 불변 예측 스냅샷 {saved}개 추가 · 자동 정산 {settled_total}개" + (" · 일부 종목 정산 미지원/오류" if settlement_errors else ""))
                st.session_state["national_ranked"]=ranked
                st.session_state["national_failures"]=failures
                status.update(label=f"{'기록 수집 제한으로 중단' if access_blocked else '완료'} — Odds API 남은 요청량 {headers.get('x-requests-remaining','확인 불가')}",state="error" if access_blocked else "complete")
        except Exception as e:
            st.error(f"A매치 분석 실패: {e}")
    if st.session_state.get("national_discovery_status"):
        with st.expander("🌐 선택 날짜 자동발견 A매치 대회", expanded=True):
            st.caption("완전 활성=일정+사전 배당 확보, 부분 활성=일부 경기만 배당 확보, 일정 활성·배당 없음=경기는 발견했지만 현재 연결 소스에서 가격을 받지 못해 EV 계산은 보류합니다.")
            st.dataframe(pd.DataFrame(st.session_state["national_discovery_status"]),hide_index=True,use_container_width=True)
    if st.session_state.get("national_status"):
        st.markdown("#### 경기별 수집·분석 가능 여부 — 추천 여부와 별개")
        st.dataframe(pd.DataFrame(st.session_state["national_status"]),hide_index=True)
    if st.session_state.get("national_lineups"):
        with st.expander("👥 라인업 상태 · 예상→확정 보기", expanded=False):
            st.caption("CONFIRMED는 공식/검증된 11명만 의미합니다. PROBABLE은 시즌 중요도+availability로 만든 저가중치 모델 예상 XI이며 확정으로 승격하지 않습니다. 공식 startXI가 들어오면 즉시 대체됩니다.")
            st.dataframe(pd.DataFrame(st.session_state["national_lineups"]),hide_index=True,use_container_width=True)
    if st.session_state.get("national_sources"):
        with st.expander("실제 수집 결과 · 소스 진단"):
            st.dataframe(pd.DataFrame(st.session_state["national_sources"]),hide_index=True)
    with st.expander("과거 경기 검증 · 불확실성 기준",expanded=False):
        st.write("기존 약 8.5%p 차감은 실측 오차가 아니라 보수 가정입니다. 아래 검증은 배당 없는 원모델 승무패만 평가하며, 시장 혼합 확률·언더오버·수익성을 검증하지 않습니다.")
        if st.button("수집한 기록으로 날짜순 검증",disabled=not st.session_state.get("validation_records")):
            with st.spinner("각 경기 이전 기록만으로 검증 중..."):
                report,_=validate(st.session_state["validation_records"],datetime.now(ZoneInfo("Asia/Seoul")).date(),national_recent,max_events=900)
                st.session_state["validation_report"]=report
        report=st.session_state.get("validation_report")
        if report is None:
            try:report=json.loads((Path(__file__).parent/'data/validation_report.json').read_text())
            except (OSError,ValueError):report={}
        if report:
            st.write({"검증 상태":report.get('status'),"학습 경기":report.get('train_n',0),"후기 검증 경기":report.get('test_n',0),"분리 날짜":report.get('split_date'),"기간 끝":report.get('end_date')})
            st.write({"보정 전":report.get('holdout_before'),"보정 후":report.get('holdout_after')})
            st.caption("Brier와 Log loss는 낮을수록 좋습니다. 표본 최소 100경기씩·두 지표 개선을 요구합니다. 진단 보고서는 운영 확률/차감률을 자동으로 바꾸지 않습니다.")
            st.dataframe(pd.DataFrame(report.get('bins',[])),hide_index=True)
            st.download_button("검증 보고서 다운로드",json.dumps(report,ensure_ascii=False,indent=2),file_name="validation_report.json")
    nr=st.session_state.get("national_ranked")
    if isinstance(nr,pd.DataFrame) and not nr.empty:
        if st.session_state.get("national_filter_label"):st.caption(f"분석 경기일: {st.session_state['national_filter_label']}")
        render_final_decision_layer(nr,"national",title="🧠 v3 FINAL Decision Layer · A매치")
        st.markdown("### 상세 진단 · 전체 경기 자동분석")
        st.caption("확률이 가장 높은 선택과 배당 대비 기대값이 가장 높은 선택을 따로 표시합니다. EV가 음수인 경기도 표시합니다. 언더오버 요약은 양방향 배당이 있는 라인 중 북메이커 수와 시장 균형으로 대표 라인을 고릅니다. 모든 라인과 핸디캡은 아래 전체 옵션에서 확인하세요.")
        st.dataframe(match_summary(nr),hide_index=True)
        st.info("분석 확률과 EV는 모델 추정입니다. v3 정밀 모드에서는 제공사가 실제 반환한 xG·확정 라인업·결장·선수 중요도·휴식일을 추가 반영하며, 확보하지 못한 신호는 MISSING으로 남깁니다.")
        st.markdown("### A매치 후보 · 조합 상태")
        passed_count=int(nr.scenario_candidate.sum())
        ready_count=int(nr.scenario_parlay_eligible.sum())
        pairs=reference_pairs(nr)
        metrics=st.columns(4)
        for cell,label,value in zip(metrics,['가정 변화 통과 선택지','그중 라인업 확인','참고용 2폴 표시','성능 검증 완료'],[passed_count,ready_count,len(pairs),0]):
            cell.metric(label,value)
        st.caption("앞의 두 숫자는 경기 수가 아닌 선택지 수입니다. 개별 분석 → 가정 변화 통과 → 라인업 확인 → 서로 다른 경기의 참고용 2폴 순서입니다. 모델 성능 검증은 별도입니다.")
        with st.expander("미검증은 어떻게 검증하나요?"):
            st.write("경기 전 예측 확률·배당·수집 시각·모델 버전을 저장하고 경기 후 실제 90분 결과와 연결합니다. 승무패와 언더오버를 따로 평가하며, 학습에 쓰지 않은 이후 기간에서 확률 정확도(Brier/Log loss·확률 구간별 실제 빈도), 시장 기준 대비 성능, 적특을 반영한 수익률과 불확실성을 확인해야 합니다.")
            st.write("v3는 분석 당시 모델확률·배당·BE·Edge·EV·모델버전·ROBUST 판정을 append-only 스냅샷으로 저장합니다. The Odds API scores가 지원되는 종목은 최근 종료 경기 결과를 자동 연결해 Brier, Log loss, ROI를 계산합니다. 장기 검증은 충분한 사후 표본이 쌓인 뒤 판단해야 합니다.")
        st.markdown("#### 가정 변화에도 기대값이 양수인 개별 후보")
        st.caption("양 팀 예상 득점을 각각 −10%·기준·+10%, 원모델 비중을 15%·25%·35%로 바꾼 27개 조합입니다. 이 범위는 실측 오차나 신뢰구간이 아닙니다. 기존 약 8.5%p 일괄 차감은 후보 선정에서 사용하지 않습니다.")
        st.info("여기서 '가정 변화 통과'는 개별 픽의 강건성 시험 통과를 뜻합니다. 🏆 오늘의 베스트 조합에서는 여기에 데이터 신선도·반증위험·드리프트·라인업 단계 등 별도 안전 게이트를 한 번 더 적용하므로, 통과 픽이 '검토 후보'로는 보이더라도 실제 조합에서는 제외될 수 있습니다.")
        counts=nr.selection_status.value_counts()
        st.write({"분석 옵션":len(nr),"가정 변화 통과":int(counts.get('SCENARIO_PASS',0)),"가정에 민감":int(counts.get('SENSITIVE',0)),"괴리 검토":int(counts.get('REVIEW',0)),"기대값 미달":int(counts.get('PASS',0)),"자료 보류":int(counts.get('DATA_HOLD',0))})
        labels={'SCENARIO_PASS':'가정 변화 통과 · 미검증','SENSITIVE':'가정에 민감 · 조합 제외','REVIEW':'괴리 검토 · 조합 제외','PASS':'기대값 미달','DATA_HOLD':'자료 보류'}
        def policy_table(frame):
            cols=['selection_status','display_pick','best_book','best_odds','model_win_prob','break_even','edge_pp','point_ev_roi','scenario_win_min','scenario_win_max','scenario_ev_min','scenario_ev_max','lineup_confirmed','selection_reason']
            shown=frame[[c for c in cols if c in frame]].copy()
            for col in ['model_win_prob','break_even','point_ev_roi','scenario_win_min','scenario_win_max','scenario_ev_min','scenario_ev_max']:
                shown[col]=(shown[col]*100).round(2)
            shown['selection_status']=shown['selection_status'].map(labels)
            if 'selection_reason' in shown:
                shown['selection_reason']=[humanize_policy_reason(v, frame.iloc[i] if i < len(frame) else None) for i,v in enumerate(shown['selection_reason'].tolist())]
            return shown.rename(columns={'selection_status':'판정','display_pick':'경기/선택','model_win_prob':'기준 확률(%)','break_even':'BE(%)','edge_pp':'Edge(%p)','point_ev_roi':'기준 EV(%)','scenario_win_min':'가정 최저 확률(%)','scenario_win_max':'가정 최고 확률(%)','scenario_ev_min':'가정 최저 EV(%)','scenario_ev_max':'가정 최고 EV(%)','lineup_confirmed':'라인업 확인','selection_reason':'사람이 읽는 판정 이유'})
        passed=nr[nr.scenario_candidate].sort_values('scenario_ev_min',ascending=False)
        if passed.empty:st.info("설정한 모든 가정에서 기대값이 양수인 후보는 없습니다. 아래 민감 후보와 제외 이유를 확인하세요.")
        else:st.dataframe(policy_table(passed),hide_index=True)
        st.markdown("#### 기준 기대값은 양수지만 가정에 민감한 후보")
        sensitive=nr[nr.selection_status.eq('SENSITIVE')].sort_values('point_ev_roi',ascending=False)
        if sensitive.empty:st.caption("해당 후보 없음")
        else:st.dataframe(policy_table(sensitive),hide_index=True)
        with st.expander("모든 옵션 · 개별 판정 이유"):
            st.dataframe(policy_table(nr),hide_index=True)
        with st.expander("이전 버전 일괄 차감 결과 비교 · 현재 선정에는 미사용"):
            st.dataframe(nr[['display_pick','legacy_grade','uncertainty_pp','legacy_conservative_ev_roi']],hide_index=True)
        st.caption("통과는 설정한 가정 안에서만 의미가 있습니다. xG·결장은 미수집이며 최종 시장 혼합 확률과 수익성은 검증되지 않았습니다. A/B/C 등급과 베팅금액 추천은 제공하지 않습니다.")
        st.download_button("전체 분석 CSV 다운로드",nr.to_csv(index=False).encode("utf-8-sig"),file_name="national_analysis.csv")
        st.markdown("#### 라인업 확인 + 가정 변화 통과: 참고용 2폴")
        combos=pairs
        if combos:st.dataframe(pd.DataFrame(combos),hide_index=True)
        else:st.info("라인업 확인과 가정 변화 시험을 모두 통과한 서로 다른 2경기가 없어 조합을 만들지 않습니다.")
    elif "national_ranked" in st.session_state:
        st.info("기록 수집 실패로 분석 결과를 만들지 못했습니다. 아래 제외 사유를 확인하세요." if st.session_state.get("national_failures") else "선택 범위의 배당 또는 분석 가능한 후보가 없습니다.")
    if st.session_state.get("national_failures"):
        with st.expander(f"분석 제외 경기 {len(st.session_state['national_failures'])}개"):
            st.dataframe(pd.DataFrame(st.session_state["national_failures"]),use_container_width=True,hide_index=True)


    if st.session_state.get("national_evidence"):
        st.markdown("#### 사용한 최근 기록과 출처")
        evidence=pd.DataFrame(st.session_state["national_evidence"])
        if (evidence["age_days"]>45).any():
            st.warning("45일 넘게 지난 기록을 사용하는 팀이 있습니다. 최근 대회 결과가 빠졌을 수 있습니다. 수집 진단과 마지막 기록일을 확인하세요.")
        st.dataframe(evidence.rename(columns={"last_date":"마지막 기록일","age_days":"경과 일수","matches":"사용 경기 수","xg_matches":"xG 표본 수","sources":"출처"}),hide_index=True)

with tabs[2]:
    st.subheader("⚾ KBO / NPB 완전자동 분석")
    st.write("The Odds API 배당 + KBO/NPB 공식 팀기록 + 예고/확정 선발 + 실제 라인업을 자동 수집해 최종 확률을 다시 계산합니다.")
    st.caption("v3.0.2: KST 캘린더로 특정 날짜 경기만 분석하고 모든 경기 카드에 KST 시작시간을 표시합니다. ROBUST만 자동 다폴에 허용합니다.")

    if not ODDS_KEY:
        st.warning("THE_ODDS_API_KEY가 필요합니다.")
    b1,b2,b3,b4=st.columns(4)
    league=b1.selectbox("리그",["KBO","NPB"],index=0)
    baseball_key={"KBO":"baseball_kbo","NPB":"baseball_npb"}[league]
    bregion=b2.selectbox("배당 지역",["eu","uk","us","au"],index=0,key="baseball_region")
    brecent=b3.selectbox("최근 경기 반영",[6,8,10,12],index=2,help="확인 가능한 최근 팀/타선 기록 수입니다. 선발은 최근 최대 5경기를 조회합니다.")
    bbooks=b4.slider("최소 북메이커",1,5,2,key="baseball_books")
    bmarkets=st.multiselect("야구 분석 마켓",["h2h","spreads","totals"],default=["h2h","spreads","totals"],key="baseball_markets")
    baseball_date_only,baseball_match_date,_=render_kst_calendar("baseball")
    brun=st.button("⚾ 선택 리그 전체 자동분석",type="primary",disabled=not ODDS_KEY)

    if brun:
        try:
            with st.status(f"{league} 배당/공식기록/선발/라인업을 분석 중...",expanded=True) as status:
                odds_api=TheOddsAPI(ODDS_KEY)
                events,headers=odds_api.odds(baseball_key,bregion,",".join(bmarkets))
                rawb=clean_odds(odds_api.flatten(events))
                rawb=apply_kst_filter(rawb,date_only=baseball_date_only,selected_date=baseball_match_date,future_only=True) if baseball_date_only else apply_kst_filter(rawb,date_only=False,selected_date=baseball_match_date,future_only=True)
                st.session_state["baseball_filter_label"]=(f"{baseball_match_date:%Y-%m-%d} KST" if baseball_date_only else "전체 제공 예정 경기")
                st.session_state["baseball_raw_odds"]=rawb
                if rawb.empty:
                    st.warning("선택한 KST 날짜에 현재 배당이 있는 예정 경기가 없습니다." if baseball_date_only else "현재 배당이 있는 예정 경기가 없습니다.")
                    st.session_state["baseball_ranked"]=pd.DataFrame()
                    status.update(label="배당 경기 없음",state="complete")
                    st.stop()

                marketb=consensus(rawb,min_books=bbooks)
                st.session_state["baseball_market"]=marketb
                official=OfficialBaseballStats()
                live=LiveBaseballContext()
                advanced=AdvancedBaseballSignals(live)
                year=pd.Timestamp.now(tz="Asia/Seoul").year
                stats=official.load(league,year)
                st.write(
                    f"✅ 기본 모델 데이터 로드: {league} 공식 기록 팀 {len(stats)}개 "
                    f"(마지막 소스: {official.last_source})"
                )

                all_rows=[]; failures=[]; metas=[]; live_rows=[]
                groups=list(marketb.groupby("event_id"))
                for i,(eid,g) in enumerate(groups,start=1):
                    home=g.iloc[0]["home_team"]; away=g.iloc[0]["away_team"]
                    st.write(f"[{i}/{len(groups)}] {match_label_kst(home,away,g.iloc[0]['commence_time'])}")
                    try:
                        ctx=live.context(league,home,away,g.iloc[0]["commence_time"])
                        started=pd.Timestamp(g.iloc[0]["commence_time"])<=pd.Timestamp.now(tz="UTC")
                        if started:
                            live_rows.append({"경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),"경기시간(KST)":format_kst(g.iloc[0]["commence_time"]),"단계":"시작시간 경과·사전분석 제외",
                                "라인업":ctx.get("lineup_label") or ("확인" if ctx.get("lineup_confirmed") else "원본 미수집"),
                                "타순 1~9 확정":ctx.get("lineup_kind")=="starting",
                                "원정 1~9":", ".join(x.get("name","") for x in ctx.get("away_lineup",[])),
                                "홈 1~9":", ".join(x.get("name","") for x in ctx.get("home_lineup",[])),
                                "비고":"시작 예정시간이 지났습니다. 최신 오더는 교체 선수를 포함할 수 있으며 사전 모델로 라이브 배당을 분석하지 않습니다."})
                            continue
                        try:
                            ctx["advanced"]=advanced.collect(
                                league,home,away,g.iloc[0]["commence_time"],ctx,recent_n=brecent
                            )
                        except Exception as advanced_error:
                            ctx["advanced"]={
                                "advanced_used":0,"advanced_total":7,"advanced_completeness":0.0,
                                "extra_uncertainty_pp":2.1,
                                "statuses":{},"notes":[f"advanced collection failed: {type(advanced_error).__name__}: {advanced_error}"],
                            }
                        adv=ctx["advanced"]
                        if league=="NPB" and ctx.get("stage")=="FINAL":
                            both_starters=all((adv.get(side+"_starter_recent") or {}).get("kbb_pct") is not None for side in ("home","away"))
                            both_ops=all((adv.get(side+"_lineup_form") or {}).get("recent10_ops") is not None for side in ("home","away"))
                            if not (both_starters and both_ops):
                                ctx["stage"]="DATA PARTIAL"
                                adv.setdefault("notes",[]).append("선발 최근 K-BB% 또는 양 팀 최근 OPS 결측: FINAL 보류")
                        live_rows.append({
                            "경기":match_label_kst(home,away,g.iloc[0]["commence_time"]),
                            "경기시간(KST)":format_kst(g.iloc[0]["commence_time"]),
                            "단계":ctx.get("stage"),
                            "원정 선발":ctx.get("away_starter") or "미확인",
                            "홈 선발":ctx.get("home_starter") or "미확인",
                            "선발투수 확정":bool(ctx.get("starter_confirmed")),
                            "라인업":(ctx.get("lineup_label") or ("확정" if ctx.get("lineup_confirmed") else "원본 미수집")),
                            "라인업 소스":ctx.get("lineup_source") or ctx.get("source") or "-",
                            "Advanced":f'{adv.get("advanced_used",0)}/{adv.get("advanced_total",7)}',
                            "최근 타선":bool((adv.get("statuses") or {}).get("recent_form")),
                            "선발 최근":bool((adv.get("statuses") or {}).get("starter_recent")),
                            "불펜":bool((adv.get("statuses") or {}).get("bullpen")),
                            "좌우":bool((adv.get("statuses") or {}).get("split")),
                            "구속":bool((adv.get("statuses") or {}).get("velocity")),
                            "날씨":bool((adv.get("statuses") or {}).get("weather")),
                            "홈 선발 최근 경기":(adv.get("home_starter_recent") or {}).get("games"),
                            "홈 선발 최근 K%":(adv.get("home_starter_recent") or {}).get("k_pct"),
                            "홈 선발 최근 BB%":(adv.get("home_starter_recent") or {}).get("bb_pct"),
                            "홈 선발 최근 K-BB%":(adv.get("home_starter_recent") or {}).get("kbb_pct"),
                            "원정 선발 최근 경기":(adv.get("away_starter_recent") or {}).get("games"),
                            "원정 선발 최근 K-BB%":(adv.get("away_starter_recent") or {}).get("kbb_pct"),
                            "홈 팀 최근 OPS":(adv.get("home_lineup_form") or {}).get("recent10_ops"),
                            "원정 팀 최근 OPS":(adv.get("away_lineup_form") or {}).get("recent10_ops"),
                            "홈 OPS 근거 경기":(adv.get("home_lineup_form") or {}).get("ops_games"),
                            "원정 OPS 근거 경기":(adv.get("away_lineup_form") or {}).get("ops_games"),
                            "홈 선발 기록 사유":(adv.get("home_starter_recent") or {}).get("reason"),
                            "원정 선발 기록 사유":(adv.get("away_starter_recent") or {}).get("reason"),
                            "홈 최근 OBP 대리값":(adv.get("home_recent") or {}).get("obp_proxy"),
                            "원정 최근 OBP 대리값":(adv.get("away_recent") or {}).get("obp_proxy"),
                            "홈 불펜 최근 구원 이닝":(adv.get("home_bullpen") or {}).get("relief_ip_last3"),
                            "원정 불펜 최근 구원 이닝":(adv.get("away_bullpen") or {}).get("relief_ip_last3"),
                            "원정 1~9":", ".join(x.get("name","") for x in ctx.get("away_lineup",[])[:9]) or "-",
                            "홈 1~9":", ".join(x.get("name","") for x in ctx.get("home_lineup",[])[:9]) or "-",
                            "소스":ctx.get("source"),
                            "비고":"; ".join(filter(None,[ctx.get("note",""),*(adv.get("notes") or [])]))
                        })
                        analyzed,meta=analyze_official_event(g,stats,league,ctx)
                        metas.append(meta)
                        if not analyzed.empty:
                            analyzed["kickoff_kst"]=format_kst(g.iloc[0]["commence_time"])
                            all_rows.append(analyzed)
                        else:
                            failures.append({"경기":f"{home} - {away}","이유":meta.get("reason",meta.get("status","데이터 없음"))})
                    except Exception as e:
                        failures.append({"경기":f"{home} - {away}","이유":str(e)})

                rankedb=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
                if not rankedb.empty:
                    rankedb["sport_key"]=baseball_key
                    rankedb=rankedb.sort_values(["robust_positive_ratio","robust_ev_p10","conservative_ev_roi"],ascending=False)
                    rankedb["odds_region"]=bregion
                    saved=record_frame(rankedb,sport_key=baseball_key,sport_family=f"baseball_{league.lower()}")
                    settle_info=auto_settle(odds_api,baseball_key)
                    st.caption(f"v3 불변 예측 스냅샷 {saved}개 추가 · 자동 정산 {settle_info.get('settled',0)}개" + (f" · 정산 오류: {settle_info.get('error')}" if settle_info.get('error') else ""))
                st.session_state["baseball_ranked"]=rankedb
                st.session_state["baseball_failures"]=failures
                st.session_state["baseball_meta"]=metas
                st.session_state["baseball_live_rows"]=live_rows
                status.update(label=f"완료 — Odds API 남은 요청량 {headers.get('x-requests-remaining')}",state="complete")
        except Exception as e:
            st.session_state["baseball_ranked"]=pd.DataFrame()
            st.session_state["baseball_failures"]=[]
            st.session_state["baseball_live_rows"]=[]
            st.error(f"야구 자동분석 실패: {type(e).__name__}: {e}")
            st.caption("수집 실패 지점과 소스 오류를 확인하세요. 이전 실행 결과는 자동 초기화됩니다.")

    if st.session_state.get("baseball_live_rows"):
        st.markdown("### 선발 / 라인업 자동수집 상태")
        st.dataframe(pd.DataFrame(st.session_state["baseball_live_rows"]),use_container_width=True,hide_index=True)
        st.caption("PRE-LINEUP → STARTER/LINEUP CONFIRMED → DATA PARTIAL 또는 FINAL. NPB는 양 선발 최근 K-BB%와 양 팀 최근 OPS까지 확인되어야 FINAL입니다.")

    if "baseball_ranked" in st.session_state and not st.session_state["baseball_ranked"].empty:
        rb=st.session_state["baseball_ranked"]
        if st.session_state.get("baseball_filter_label"):st.caption(f"분석 경기일: {st.session_state['baseball_filter_label']}")
        render_final_decision_layer(rb,"baseball",title="🧠 v3 FINAL Decision Layer · KBO/NPB")
        st.markdown("### 상세 진단 · KBO/NPB 기준 +EV 전체")
        st.caption("기준 EV가 양수인 선택지는 REVIEW/라인업 미확정이어도 숨기지 않습니다. 실제 조합 가능 여부는 별도 안전게이트로 구분합니다.")
        vb=build_baseball_diagnostics(rb)
        diag_counts=diagnostic_counts(rb)
        if diag_counts["base_positive"]:
            st.caption(
                f"기준 +EV {diag_counts['base_positive']}개 · 조합 가능 {diag_counts['parlay']}개 · "
                f"단일 +EV 후보 {diag_counts['single']}개 · 검토 후보 {diag_counts['review']}개"
            )
        cols=[
            "diagnostic_candidate_status","diagnostic_exclusion_reason","v3_decision_status",
            "robust_positive_ratio","robust_ev_p10","robust_ev_min","counter_case_risk","signal_coverage",
            "grade","stage","data_quality","sanity","kickoff_kst","display_pick","best_book","best_odds","books",
            "consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even",
            "edge_pp","ev_roi","conservative_ev_roi","uncertainty_pp","starter_confirmed","lineup_confirmed",
            "advanced_completeness","advanced_used","recent_form_used","starter_recent_used",
            "bullpen_used","split_used","velocity_used","weather_used",
            "away_starter","away_starter_era","away_starter_whip",
            "home_starter","home_starter_era","home_starter_whip",
            "away_recent_rf","away_recent_ra","home_recent_rf","home_recent_ra",
            "away_expected_runs","home_expected_runs",
        ]
        cols=[c for c in cols if c in vb.columns]
        vb=vb[cols]
        for c in ["consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even"]:
            if c in vb: vb[c]=(vb[c]*100).round(1)
        for c in ["ev_roi","conservative_ev_roi"]:
            if c in vb: vb[c]=(vb[c]*100).round(1)
        for c in ["robust_positive_ratio","signal_coverage"]:
            if c in vb:vb[c]=(vb[c]*100).round(1)
        for c in ["robust_ev_p10","robust_ev_min"]:
            if c in vb:vb[c]=(vb[c]*100).round(1)
        for c in ["edge_pp","uncertainty_pp","away_recent_rf","away_recent_ra","home_recent_rf","home_recent_ra","away_expected_runs","home_expected_runs","away_starter_era","home_starter_era","away_starter_whip","home_starter_whip"]:
            if c in vb: vb[c]=pd.to_numeric(vb[c],errors="coerce").round(2)
        st.dataframe(vb.head(100),use_container_width=True,hide_index=True)
        if vb.empty:
            partial=sum(x.get("단계")=="DATA PARTIAL" for x in st.session_state.get("baseball_live_rows",[]))
            if partial:
                st.warning(f"기준 EV가 양수인 선택지가 없습니다. {partial}경기는 핵심 기록 결측으로 DATA PARTIAL이며 자동 다폴에서 제외됩니다.")
            else:
                st.info(f"{len(rb)}개 배당 선택지를 계산했지만 현재 기준 EV가 양수인 선택지가 없습니다.")
        elif diag_counts["parlay"] == 0:
            st.warning("기준 +EV 선택지는 있지만 현재 실제 조합 안전게이트를 통과한 픽은 없습니다. 위 표의 '후보상태/조합 제외 이유'에서 원인을 확인하세요.")
        st.caption("NPB FINAL은 공식 선발·라인업과 양 선발 최근 K-BB%, 양 팀 최근 OPS가 확인된 상태입니다. 구속·좌우 스플릿 결측은 불확실성에 반영합니다.")

        st.markdown("### 야구 2~6폴")
        sizesb=st.multiselect("야구 폴더 수",[2,3,4,5,6],default=[2,3],key="baseball_parlay_sizes")
        final_only=st.checkbox("자동 다폴은 FINAL 경기만 사용",value=True,key="baseball_final_only")
        rb_parlay=rb.copy()
        rb_parlay=rb_parlay[rb_parlay["stage"]!="DATA PARTIAL"]
        if final_only and "stage" in rb_parlay:
            rb_parlay=rb_parlay[(rb_parlay["stage"]=="FINAL") & (rb_parlay["data_quality"]=="HIGH")]
        elif "data_quality" in rb_parlay:
            rb_parlay=rb_parlay[rb_parlay["data_quality"].isin(["HIGH","MEDIUM"])]
        resb=optimize_parlays(rb_parlay,sizes=sizesb,top_n=10) if not rb_parlay.empty else {n:[] for n in sizesb}
        for n in sizesb:
            st.markdown(f"#### {n}폴 TOP")
            f=pd.DataFrame(resb.get(n,[]))
            if not f.empty:
                f["배당"]=f["배당"].round(2)
                f["근사 적중확률"]=(f["근사 적중확률"]*100).round(1)
                f["근사 EV"]=(f["근사 EV"]*100).round(1)
            st.dataframe(f,use_container_width=True,hide_index=True)
    elif "baseball_ranked" in st.session_state:
        partial=sum(x.get("단계")=="DATA PARTIAL" for x in st.session_state.get("baseball_live_rows",[]))
        if any(x.get("단계")=="시작시간 경과·사전분석 제외" for x in st.session_state.get("baseball_live_rows",[])):
            st.info("시작 예정시간이 지난 경기는 위 표에서 오더 수집 상태만 확인합니다. 경기중 배당용 분석은 지원하지 않습니다.")
        elif partial:
            st.warning(f"{partial}경기는 선발 최근 기록 또는 팀 OPS가 없어 DATA PARTIAL입니다. +EV 후보 판단을 보류하고 자동 다폴에서 제외했습니다.")
        elif st.session_state.get("baseball_failures"):
            st.warning("분석 실패 경기 때문에 후보를 계산하지 못했습니다. 아래 경기별 실패 이유를 확인하세요.")
        elif st.session_state.get("baseball_market") is not None and st.session_state["baseball_market"].empty:
            st.warning("배당은 수집됐지만 최소 북메이커 수를 충족한 시장이 없습니다.")
        else:
            st.info("분석 가능한 경기나 선택지가 없습니다. 선발/최근 기록 수집 상태를 확인하세요.")

    if st.session_state.get("baseball_failures"):
        with st.expander(f"분석하지 못한 야구 경기 {len(st.session_state['baseball_failures'])}개"):
            st.dataframe(pd.DataFrame(st.session_state["baseball_failures"]),use_container_width=True,hide_index=True)


with tabs[3]:
    st.subheader("실시간 배당 원본")
    if "raw_odds" not in st.session_state:
        st.info("완전자동 축구에서 먼저 분석하세요.")
    else:
        df=st.session_state["raw_odds"]
        q=st.text_input("팀/선택 검색")
        if q:
            m=df.astype(str).apply(lambda c:c.str.contains(q,case=False,na=False)).any(axis=1)
            df=df[m]
        st.dataframe(df,use_container_width=True,hide_index=True)

with tabs[4]:
    st.subheader("정상 시장 컨센서스")
    if "market" not in st.session_state:
        st.info("완전자동 축구에서 먼저 분석하세요.")
    else:
        m=st.session_state["market"].copy()
        show=m[["home_team","away_team","market_id","selection","best_book","best_odds","books",
                "consensus_prob","best_be","market_edge_pp","market_ev"]].copy()
        show["consensus_prob"]=(show["consensus_prob"]*100).round(2)
        show["best_be"]=(show["best_be"]*100).round(2)
        show["market_ev"]=(show["market_ev"]*100).round(2)
        show["market_edge_pp"]=show["market_edge_pp"].round(2)
        st.dataframe(show,use_container_width=True,hide_index=True)
        st.caption("lay/exchange, books 부족, 비정상 최고배당을 자동 필터링한 표입니다.")

with tabs[5]:
    st.subheader("⚾ MLB 완전자동 분석")
    st.write("The Odds API 배당 + MLB Stats API 일정/팀기록/예고선발/라인업을 결합해 승패·런라인·언더오버 확률과 EV를 계산합니다.")
    st.caption("v3.3.0: MLB 정밀계층은 Statcast xwOBA/Barrel/HardHit, Whiff/Chase/Zone/Contact, 구종/구사율, 선발 workload, 불펜 정확한 최근 3일 투구수, 확정 라인업 좌우 스플릿, 구종 상성, 부상·복귀/뉴스, 라인업 변화, 시장 이동, roof/심판, 이동·휴식, BvP, 불펜 운용 패턴까지 실제 수집된 경우에만 반영합니다.")
    st.info("MLB 모델도 채팅 분석 체크리스트를 최대한 자동화한 별도 정량 엔진입니다. 확보하지 못한 신호는 MISSING으로 남기며 평균값을 임의로 채우지 않습니다.")

    if not ODDS_KEY:
        st.warning("THE_ODDS_API_KEY가 필요합니다.")

    m1,m2,m3=st.columns(3)
    mlb_region=m1.selectbox("배당 지역",["eu","uk","us","au"],index=0,key="mlb_region")
    mlb_recent=m2.selectbox("최근 경기 반영",[6,8,10,12],index=2,key="mlb_recent",help="팀 최근폼은 선택 경기 수, 선발은 최근 최대 5경기를 사용합니다.")
    mlb_books=m3.slider("최소 북메이커",1,6,2,key="mlb_books")
    mlb_date_only,mlb_match_date,_=render_kst_calendar("mlb")
    mlb_markets=st.multiselect("MLB 분석 마켓",["h2h","spreads","totals"],default=["h2h","spreads","totals"],key="mlb_markets")
    mlb_deep=st.checkbox(
        "🧠 MLB v3 정밀 컨텍스트 사용",value=True,key="mlb_deep",
        help="Statcast/pitch-level, exact bullpen workload, lineup platoon, pitch matchup, workload, availability/news, market movement, travel/roof/umpire/BvP까지 조회합니다. 응답이 없으면 MISSING 처리합니다."
    )
    mlb_run=st.button("⚾ 선택 날짜 MLB 전체 자동분석",type="primary",disabled=not(ODDS_KEY and mlb_markets))

    if mlb_run:
        st.session_state["mlb_ranked"]=pd.DataFrame()
        st.session_state["mlb_failures"]=[]
        st.session_state["mlb_status"]=[]
        try:
            with st.status("MLB 일정·배당·선발·최근 기록을 분석 중...",expanded=True) as status:
                # 1) The MLB schedule date is NOT a KST date. Query adjacent MLB dates
                # and filter by actual UTC first-pitch time converted to Asia/Seoul.
                schedule_frame=mlb_schedule_kst(mlb_match_date)
                st.session_state["mlb_schedule"]=schedule_frame
                st.write(f"✅ {mlb_match_date:%Y-%m-%d} KST 일정 {len(schedule_frame)}경기 확인")

                # 2) Market prices, independently filtered to the exact same KST day.
                odds_api=TheOddsAPI(ODDS_KEY)
                events,headers=odds_api.odds("baseball_mlb",mlb_region,",".join(mlb_markets))
                raw=clean_odds(odds_api.flatten(events))
                raw=apply_kst_filter(raw,date_only=True,selected_date=mlb_match_date,future_only=True) if not raw.empty else raw
                st.session_state["mlb_raw_odds"]=raw
                st.session_state["mlb_filter_label"]=f"{mlb_match_date:%Y-%m-%d} KST"
                market=consensus(raw,min_books=mlb_books) if not raw.empty else pd.DataFrame()
                st.session_state["mlb_market"]=market

                if raw.empty:
                    st.warning("선택한 KST 날짜에 현재 The Odds API가 제공하는 예정 MLB 배당이 없습니다. 일정은 아래에서 계속 확인할 수 있습니다.")
                elif market.empty:
                    st.warning("MLB 배당은 있으나 설정한 최소 북메이커 수를 충족하는 양방향 시장이 없습니다.")

                provider=MLBContextProvider()
                all_rows=[]; failures=[]; status_rows=[]; matched_gamepks=set()
                groups=list(market.groupby("event_id")) if not market.empty else []
                for idx,(eid,g) in enumerate(groups,1):
                    home=g.iloc[0]["home_team"]; away=g.iloc[0]["away_team"]; kickoff=g.iloc[0]["commence_time"]
                    label=match_label_kst(home,away,kickoff)
                    st.write(f"[{idx}/{len(groups)}] {label}")
                    sched=mlb_match_schedule(schedule_frame,home,away,kickoff)
                    if sched and sched.get("gamePk"):matched_gamepks.add(sched.get("gamePk"))
                    try:
                        stats,ctx=provider.collect(home,away,kickoff,schedule_row=sched,recent_n=mlb_recent,deep=mlb_deep,market_frame=g,event_id=eid)
                        adv=ctx.get("advanced") or {}; statuses=adv.get("statuses") or {}
                        if not stats or any((stats.get(t) or {}).get("runs_per_game") is None or (stats.get(t) or {}).get("runs_allowed_per_game") is None for t in (home,away)):
                            reason=ctx.get("note") or "MLB season run data unavailable"
                            failures.append({"경기":label,"이유":reason})
                            status_rows.append({
                                "경기":label,"경기시간(KST)":format_kst(kickoff),"상태":"자료 부족 · 분석 보류","이유":reason,
                                "원정 선발":ctx.get("away_starter") or "미확인","홈 선발":ctx.get("home_starter") or "미확인",
                            })
                            continue
                        analyzed,meta=analyze_official_event(g,stats,"MLB",ctx)
                        if analyzed.empty:
                            reason=meta.get("reason",meta.get("status","분석 보류"))
                            failures.append({"경기":label,"이유":reason})
                        else:
                            analyzed["sport_key"]="baseball_mlb"
                            analyzed["kickoff_kst"]=format_kst(kickoff)
                            all_rows.append(analyzed)

                        missing=[k for k,v in statuses.items() if not v]
                        hp=(adv.get("home_starter_recent") or {}); ap=(adv.get("away_starter_recent") or {})
                        hv=(adv.get("home_velocity") or {}); av=(adv.get("away_velocity") or {})
                        hb=(adv.get("home_bullpen") or {}); ab=(adv.get("away_bullpen") or {})
                        d31=(adv.get("deep_v31") or {})
                        hsc=(d31.get("home_statcast") or {}); asc=(d31.get("away_statcast") or {})
                        hdisc=(hsc.get("recent_discipline") or {}); adisc=(asc.get("recent_discipline") or {})
                        hsea=(hsc.get("season") or {}); asea=(asc.get("season") or {})
                        hwe=(d31.get("home_starter_workload") or {}); awe=(d31.get("away_starter_workload") or {})
                        hbe=(d31.get("home_bullpen_exact") or {}); abe=(d31.get("away_bullpen_exact") or {})
                        hpl=(d31.get("home_lineup_platoon") or {}); apl=(d31.get("away_lineup_platoon") or {})
                        hpm=(d31.get("home_pitch_matchup") or {}); apm=(d31.get("away_pitch_matchup") or {})
                        env=(d31.get("environment") or {}); mov=(d31.get("market_movement") or {})
                        status_rows.append({
                            "경기":label,"경기시간(KST)":format_kst(kickoff),"상태":ctx.get("stage","PRE-LINEUP"),
                            "원정 선발":ctx.get("away_starter") or "미확인","홈 선발":ctx.get("home_starter") or "미확인",
                            "라인업":"확정" if ctx.get("lineup_confirmed") else "미확인",
                            "Whiff/Chase/Zone/Contact":bool(statuses.get("plate_discipline")),
                            "Statcast xwOBA/Barrel/HH":bool(statuses.get("statcast_quality")),
                            "구종/구사율":bool(statuses.get("pitch_mix")),"선발 workload":bool(statuses.get("starter_workload")),
                            "불펜 정확3일":bool(statuses.get("bullpen_exact")),"라인업 좌우 OPS":bool(statuses.get("lineup_platoon_exact")),
                            "구종 상성":bool(statuses.get("pitch_matchup")),"시장 이동":bool(statuses.get("market_movement")),
                            "Roof":bool(statuses.get("roof")),"심판":bool(statuses.get("umpire")),"이동/휴식":bool(statuses.get("travel_rest")),
                            "BvP":bool(statuses.get("bvp")),"불펜 운용":bool(statuses.get("bullpen_manager")),"부상/복귀":bool(statuses.get("availability_news")),"뉴스 스캔":bool(statuses.get("news_scan")),
                            "원정 Whiff%":adisc.get("whiff_pct"),"홈 Whiff%":hdisc.get("whiff_pct"),
                            "원정 Chase%":adisc.get("chase_pct"),"홈 Chase%":hdisc.get("chase_pct"),
                            "원정 시즌 xwOBA 허용":asea.get("xwoba"),"홈 시즌 xwOBA 허용":hsea.get("xwoba"),
                            "원정 Stuff proxy":((asc.get("arsenal") or {}).get("stuff_proxy")),"홈 Stuff proxy":((hsc.get("arsenal") or {}).get("stuff_proxy")),
                            "원정 구사율 quality Δ":asc.get("usage_quality_delta"),"홈 구사율 quality Δ":hsc.get("usage_quality_delta"),
                            "원정 선발 휴식일":awe.get("rest_days"),"홈 선발 휴식일":hwe.get("rest_days"),
                            "원정 선발 직전 투구수":awe.get("last_pitches"),"홈 선발 직전 투구수":hwe.get("last_pitches"),
                            "원정 불펜 최근3일 투구":abe.get("total_relief_pitches"),"홈 불펜 최근3일 투구":hbe.get("total_relief_pitches"),
                            "원정 라인업 vs손 OPS":apl.get("lineup_split_ops"),"홈 라인업 vs손 OPS":hpl.get("lineup_split_ops"),
                            "원정 구종상성 xwOBA":apm.get("weighted_xwoba"),"홈 구종상성 xwOBA":hpm.get("weighted_xwoba"),
                            "Roof 상태":env.get("roof_state") or env.get("roof_type"),"주심":(env.get("umpire") or {}).get("name"),
                            "최대 시장이동(%p)":mov.get("max_abs_move_pp"),
                            "MISSING":", ".join(missing) if missing else "없음",
                        })
                    except Exception as e:
                        failures.append({"경기":label,"이유":f"{type(e).__name__}: {e}"})
                        status_rows.append({"경기":label,"경기시간(KST)":format_kst(kickoff),"상태":"수집 실패","이유":str(e)})

                # Include schedule-only games so '4 games only' cannot hide the rest of a KST slate.
                if schedule_frame is not None and not schedule_frame.empty:
                    for _,r in schedule_frame.iterrows():
                        if r.get("gamePk") in matched_gamepks:continue
                        status_rows.append({
                            "경기":match_label_kst(r.get("home"),r.get("away"),r.get("gameDate")),
                            "경기시간(KST)":format_kst(r.get("gameDate")),"상태":"일정 확인 · 분석 배당 없음",
                            "원정 선발":r.get("away_probable") or "미확인","홈 선발":r.get("home_probable") or "미확인",
                            "라인업":"미확인","MISSING":"현재 분석 가능한 시장가격",
                        })

                ranked=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
                if not ranked.empty:
                    ranked=ranked.sort_values(["robust_positive_ratio","robust_ev_p10","conservative_ev_roi"],ascending=False)
                    ranked["odds_region"]=mlb_region
                    saved=record_frame(ranked,sport_key="baseball_mlb",sport_family="baseball_mlb")
                    settle_info=auto_settle(odds_api,"baseball_mlb")
                    review_info=analyze_settled_mlb()
                    st.caption(f"v3 불변 예측 스냅샷 {saved}개 추가 · 자동 정산 {settle_info.get('settled',0)}개 · MLB 자동복기 {review_info.get('reviewed',0)}개" + (f" · 정산 오류: {settle_info.get('error')}" if settle_info.get('error') else ""))
                st.session_state["mlb_ranked"]=ranked
                st.session_state["mlb_failures"]=failures
                st.session_state["mlb_status"]=status_rows
                status.update(label=f"완료 — KST 일정 {len(schedule_frame)}경기 · 분석시장 {len(groups)}경기",state="complete")
        except Exception as e:
            st.session_state["mlb_ranked"]=pd.DataFrame()
            st.session_state["mlb_failures"]=[{"경기":"MLB 전체","이유":f"{type(e).__name__}: {e}"}]
            st.error(f"MLB 자동분석 실패: {type(e).__name__}: {e}")

    if isinstance(st.session_state.get("mlb_schedule"),pd.DataFrame) and not st.session_state["mlb_schedule"].empty:
        with st.expander(f"📅 {st.session_state.get('mlb_filter_label','선택 날짜')} MLB 전체 일정",expanded=False):
            sd=st.session_state["mlb_schedule"].copy()
            show=pd.DataFrame({
                "경기시간(KST)":sd["gameDate"].map(format_kst),"원정":sd["away"],"홈":sd["home"],
                "원정 예고선발":sd["away_probable"].fillna("미확인"),"홈 예고선발":sd["home_probable"].fillna("미확인"),"상태":sd["status"],
            })
            st.dataframe(show,use_container_width=True,hide_index=True)

    if st.session_state.get("mlb_status"):
        st.markdown("### 경기별 수집·분석 가능 여부 — 추천 여부와 별개")
        st.dataframe(pd.DataFrame(st.session_state["mlb_status"]),use_container_width=True,hide_index=True)
        st.caption("PRE-LINEUP → STARTER CONFIRMED → FINAL. 확정 라인업이 나오기 전에는 단일 후보를 볼 수 있어도 자동 다폴은 제한합니다. MISSING 신호는 모델이 지어내지 않습니다.")

    mr=st.session_state.get("mlb_ranked")
    if isinstance(mr,pd.DataFrame) and not mr.empty:
        if st.session_state.get("mlb_filter_label"):st.caption(f"분석 경기일: {st.session_state['mlb_filter_label']}")
        render_final_decision_layer(mr,"mlb",title="🧠 v3 FINAL Decision Layer · MLB",include_spread=True)
        st.markdown("### 상세 진단 · MLB 전체 옵션")
        cols=[c for c in [
            "v3_decision_status","robust_positive_ratio","robust_ev_p10","robust_ev_min","counter_case_risk","signal_coverage",
            "stage","data_quality","kickoff_kst","display_pick","best_book","best_odds","books","consensus_prob",
            "raw_independent_prob","model_win_prob","push_prob","break_even","edge_pp","ev_roi","conservative_ev_roi","uncertainty_pp",
            "home_starter","away_starter","home_starter_era","away_starter_era","home_starter_whip","away_starter_whip",
            "home_recent_rf","home_recent_ra","away_recent_rf","away_recent_ra","home_expected_runs","away_expected_runs",
            "recent_form_used","starter_recent_used","bullpen_used","split_used","velocity_used","weather_used",
            "plate_discipline_used","statcast_quality_used","batted_ball_regression_used","pitch_mix_used","starter_workload_used",
            "bullpen_exact_used","lineup_platoon_exact_used","pitch_matchup_used","availability_news_used","lineup_change_used",
            "market_movement_used","market_move_pp","market_from_open_pp","roof_used","umpire_used","travel_rest_used","bvp_used","bullpen_manager_used","missing_signals",
        ] if c in mr.columns]
        shown=mr[cols].copy()
        for c in ["consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even","robust_positive_ratio","signal_coverage"]:
            if c in shown:shown[c]=(pd.to_numeric(shown[c],errors="coerce")*100).round(1)
        for c in ["robust_ev_p10","robust_ev_min","ev_roi","conservative_ev_roi"]:
            if c in shown:shown[c]=(pd.to_numeric(shown[c],errors="coerce")*100).round(1)
        st.dataframe(shown,use_container_width=True,hide_index=True)

        st.markdown("### MLB 2~6폴")
        mlb_sizes=st.multiselect("MLB 폴더 수",[2,3,4,5,6],default=[2,3],key="mlb_parlay_sizes")
        mlb_final_only=st.checkbox("MLB 자동 다폴은 FINAL 경기만 사용",value=True,key="mlb_final_only")
        pool=mr.copy()
        if mlb_final_only and "stage" in pool:
            pool=pool[(pool["stage"]=="FINAL") & (pool["data_quality"]=="HIGH")]
        res=optimize_parlays(pool,sizes=mlb_sizes,top_n=10) if not pool.empty else {n:[] for n in mlb_sizes}
        for n in mlb_sizes:
            st.markdown(f"#### {n}폴 TOP")
            f=pd.DataFrame(res.get(n,[]))
            if not f.empty:
                f["배당"]=f["배당"].round(2); f["근사 적중확률"]=(f["근사 적중확률"]*100).round(1); f["근사 EV"]=(f["근사 EV"]*100).round(1)
            st.dataframe(f,use_container_width=True,hide_index=True)
    elif "mlb_ranked" in st.session_state:
        if st.session_state.get("mlb_failures"):
            st.warning("MLB 일정은 확인했지만 분석 가능한 결과를 만들지 못한 경기가 있습니다. 아래 실패 사유를 확인하세요.")
        else:
            st.info("선택 날짜에 분석 가능한 MLB 시장이 없습니다.")

    if st.session_state.get("mlb_failures"):
        with st.expander(f"분석하지 못한 MLB 경기 {len(st.session_state['mlb_failures'])}개"):
            st.dataframe(pd.DataFrame(st.session_state["mlb_failures"]),use_container_width=True,hide_index=True)

with tabs[6]:
    st.subheader("🏆 오늘의 베스트 조합 · 축구 + 야구 통합")
    st.write("선택한 KST 날짜에 각 분석 탭이 저장한 최신 예측을 한데 모아, 종목과 리그를 가리지 않고 생존확률과 +EV를 함께 본 모델 기준 베스트 조합을 만듭니다.")
    st.caption("지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB. 라인업 미확정 경기도 제외하지 않지만 모델-시장 차이를 보수적으로 축소해 잠정 후보로 취급합니다. 라인업 발표 후 해당 종목을 다시 분석하면 최신 결과가 자동 반영됩니다.")

    d1,d2=st.columns([1,1])
    daily_date=d1.date_input("조합 날짜 (KST)",value=datetime.now(KST).date(),key="daily_best_date")
    include_provisional=d2.checkbox("라인업 미확정 잠정 후보 포함",value=True,key="daily_best_include_provisional")

    daily_raw=latest_snapshots_for_kst_date(daily_date)
    daily_candidates=prepare_daily_candidates(daily_raw)
    if not include_provisional and not daily_candidates.empty:
        daily_candidates=daily_candidates[~daily_candidates["daily_provisional"].fillna(False).astype(bool)].copy()

    if daily_raw.empty:
        st.info("이 날짜에 저장된 분석 스냅샷이 아직 없습니다. 각 종목 탭에서 해당 날짜 분석을 한 번 실행하면 여기로 자동 모입니다.")
    else:
        present=sorted(set(daily_candidates.get("sport_label",pd.Series(dtype=str)).dropna().astype(str))) if not daily_candidates.empty else []
        expected=["클럽축구","A매치","KBO","NPB","MLB"]
        missing=[x for x in expected if x not in present]
        combo_ready=(daily_candidates[daily_candidates.get("daily_combo_eligible",pd.Series(False,index=daily_candidates.index)).fillna(False).astype(bool)].copy() if not daily_candidates.empty else pd.DataFrame())
        single_ready=(daily_candidates[daily_candidates.get("daily_single_eligible",pd.Series(False,index=daily_candidates.index)).fillna(False).astype(bool)].copy() if not daily_candidates.empty else pd.DataFrame())
        m1,m2,m3,m4,m5,m6=st.columns(6)
        m1.metric("저장된 경기",int(daily_raw.get("event_id",pd.Series(dtype=str)).astype(str).nunique()) if "event_id" in daily_raw else 0)
        m2.metric("기준 +EV 전체",len(daily_candidates))
        m3.metric("단일 후보",len(single_ready))
        m4.metric("조합 가능",len(combo_ready))
        m5.metric("분석 종목",len(present))
        m6.metric("잠정 후보",int(daily_candidates.get("daily_provisional",pd.Series(dtype=bool)).fillna(False).sum()) if not daily_candidates.empty else 0)
        if present:
            st.caption("현재 집계: "+" · ".join(present))
        if missing:
            st.caption("현재 통합 +EV 후보가 없는 종목: "+" · ".join(missing)+" — 경기가 없거나, 아직 분석 전이거나, 현재 후보 기준을 통과하지 못한 경우입니다.")

        if daily_candidates.empty:
            st.warning("저장된 경기는 있지만 현재 기준 EV가 양수인 선택지가 없습니다. 억지로 후보나 조합을 만들지 않습니다.")
        else:
            if combo_ready.empty:
                if not single_ready.empty:
                    st.warning("단일 +EV 후보는 있지만 현재 2폴 이상 조합 안전게이트를 통과한 픽은 없습니다. 50% 보수확률·반증위험·데이터 신선도 등 '조합 제외 이유'를 확인하세요.")
                else:
                    st.warning("기준 +EV 검토 후보는 있지만 현재 단일/조합 안전게이트를 통과한 픽은 없습니다. 아래 후보표의 '조합 제외 이유'를 확인하세요.")
            combos=best_combos(daily_candidates,sizes=(1,2,3),top_n=5)
            best_single=(combos.get(1) or [None])[0]
            best_two=(combos.get(2) or [None])[0]
            best_three=(combos.get(3) or [None])[0]
            final_combo=best_two or best_single

            st.markdown("### 🎯 현재 기준 최종 1조합")
            if final_combo:
                c1,c2,c3,c4=st.columns(4)
                c1.metric("폴더",f"{final_combo['folder_count']}폴" if final_combo['folder_count']>1 else "1픽")
                c2.metric("조합배당",f"{final_combo['combined_odds']:.2f}")
                c3.metric("근사 적중확률",f"{final_combo['estimated_hit_prob']*100:.1f}%")
                c4.metric("근사 EV",f"{final_combo['estimated_ev']*100:+.1f}%")
                st.dataframe(combo_display_rows(final_combo),use_container_width=True,hide_index=True)
                if final_combo.get("folder_count",1)>1:
                    _naive=final_combo.get("naive_hit_prob")
                    _cov=final_combo.get("correlation_coverage",0.0)
                    _rho=final_combo.get("avg_pair_rho",0.0)
                    if _naive is not None:
                        st.caption(f"상관보정: 단순독립 {_naive*100:.1f}% → 보정후 {final_combo['estimated_hit_prob']*100:.1f}% · 역사적 pair 커버리지 {_cov*100:.0f}% · 평균 ρ {_rho:+.3f}")
                if final_combo.get("provisional_legs",0):
                    st.warning(f"현재 조합에는 라인업/선발이 완전히 확정되지 않은 잠정 픽 {final_combo['provisional_legs']}개가 있습니다. 해당 경기 라인업 발표 후 종목 탭을 다시 분석하면 이 조합도 자동 재평가됩니다.")
                else:
                    st.success("현재 조합의 모든 픽은 저장된 최신 분석 기준 확정 데이터 단계입니다.")
            else:
                st.info("현재 기준으로 생존확률과 +EV 조건을 동시에 충족하는 조합이 없습니다. 단폴 후보만 확인하세요.")

            st.markdown("### 전체 종목 통합 후보 순위")
            view=daily_candidates.copy()
            view["경기시간(KST)"]=view["kickoff_kst"].apply(lambda x:x.strftime("%m/%d %H:%M") if hasattr(x,"strftime") else "-")
            view["모델확률"]=(pd.to_numeric(view["daily_original_prob"],errors="coerce")*100).round(1)
            view["조합용 보수확률"]=(pd.to_numeric(view["daily_adjusted_prob"],errors="coerce")*100).round(1)
            view["BE"]=(pd.to_numeric(view["daily_be"],errors="coerce")*100).round(1)
            view["EV"]=(pd.to_numeric(view["daily_original_ev"],errors="coerce")*100).round(1)
            view["보수EV"]=(pd.to_numeric(view["daily_adjusted_ev"],errors="coerce")*100).round(1)
            view["강건성"]=(pd.to_numeric(view.get("robust_positive_ratio"),errors="coerce")*100).round(0) if "robust_positive_ratio" in view else pd.NA
            show_cols={
                "sport_label":"종목","경기시간(KST)":"경기시간(KST)","game_label":"경기","pick_label":"픽","best_odds":"배당",
                "모델확률":"모델확률(%)","조합용 보수확률":"조합용 보수확률(%)","BE":"BE(%)","EV":"EV(%)","보수EV":"보수EV(%)",
                "강건성":"강건성(%)","lineup_state":"라인업","daily_stage":"데이터상태","v3_decision_status":"판정",
                "daily_candidate_state":"후보상태","daily_gate_reason":"조합 제외 이유","daily_quality_score":"통합점수",
                "daily_single_eligible":"단일 가능","daily_combo_eligible":"조합 가능"
            }
            table=view[[c for c in show_cols if c in view.columns]].rename(columns=show_cols)
            if "통합점수" in table: table["통합점수"]=pd.to_numeric(table["통합점수"],errors="coerce").round(1)
            if "배당" in table: table["배당"]=pd.to_numeric(table["배당"],errors="coerce").round(2)
            st.dataframe(table,use_container_width=True,hide_index=True)

            a,b=st.columns(2)
            with a:
                st.markdown("### 🥇 최강 단일픽")
                if best_single:
                    st.dataframe(combo_display_rows(best_single),use_container_width=True,hide_index=True)
                else:
                    st.info("단일 +EV 후보 없음")
            with b:
                st.markdown("### 🔒 강한 2폴")
                if best_two:
                    st.dataframe(combo_display_rows(best_two),use_container_width=True,hide_index=True)
                    st.caption(f"조합배당 {best_two['combined_odds']:.2f} · 근사 적중확률 {best_two['estimated_hit_prob']*100:.1f}% · 근사 EV {best_two['estimated_ev']*100:+.1f}%")
                else:
                    st.info("2폴을 억지로 만들지 않음 — 서로 다른 경기에서 50% 이상 보수확률과 +EV를 동시에 만족하는 후보가 부족합니다.")

            st.markdown("### ➕ 선택적 3폴")
            if best_three:
                st.dataframe(combo_display_rows(best_three),use_container_width=True,hide_index=True)
                st.caption(f"조합배당 {best_three['combined_odds']:.2f} · 근사 적중확률 {best_three['estimated_hit_prob']*100:.1f}% · 근사 EV {best_three['estimated_ev']*100:+.1f}% · 3폴은 생존확률 하락을 별도 패널티로 반영")
            else:
                st.info("현재는 3폴을 추가할 만큼 강한 세 번째 후보가 없습니다. 2폴보다 높은 배당을 만들기 위해 약한 픽을 강제로 넣지 않습니다.")

            with st.expander("TOP 5 조합 비교"):
                rows=[]
                for n in (1,2,3):
                    for rank,x in enumerate(combos.get(n,[]),1):
                        rows.append({"구분":f"{n}폴" if n>1 else "1픽","순위":rank,"조합":x["combo_label"],"배당":round(x["combined_odds"],2),"근사 적중확률(%)":round(x["estimated_hit_prob"]*100,1),"근사 EV(%)":round(x["estimated_ev"]*100,1),"잠정픽":x["provisional_legs"]})
                st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

    st.caption("이 탭은 '기준 +EV 전체 → 검토 후보 → 단일 후보 → 조합 가능'을 분리합니다. 기준 EV가 양수면 REVIEW/PASS도 숨기지 않으며, 실제 단일/다폴에는 반증위험·신선도·드리프트·보수EV·라인업 단계 등 안전게이트를 적용합니다. 2폴 이상은 각 leg의 보수확률 50% 이상을 요구하고 같은 경기의 여러 선택지는 한 조합에 동시에 넣지 않습니다.")


with tabs[7]:
    st.subheader("2~6폴 자동 조합")
    if "ranked" not in st.session_state or st.session_state["ranked"].empty:
        st.info("완전자동 축구 분석을 먼저 실행하세요.")
    else:
        sizes=st.multiselect("폴더 수",[2,3,4,5,6],default=[2,3,4,5,6])
        res=optimize_parlays(st.session_state["ranked"],sizes=sizes,top_n=10)
        for n in sizes:
            st.markdown(f"### {n}폴 TOP")
            frame=pd.DataFrame(res[n])
            if not frame.empty:
                frame["배당"]=frame["배당"].round(2)
                frame["근사 적중확률"]=(frame["근사 적중확률"]*100).round(1)
                frame["근사 EV"]=(frame["근사 EV"]*100).round(1)
            st.dataframe(frame,use_container_width=True,hide_index=True)


with tabs[8]:
    st.subheader("📡 20K 플랜 최적화 모니터링 — 축구 + KBO + NPB")
    st.write("앱을 닫아도 계속 감시하려면 `monitor.py`를 별도 항상-실행 서버에서 돌립니다. 이 화면은 스케줄/알림 설정과 테스트용입니다.")

    st.markdown("""
### 기본 감시 스케줄
| 킥오프까지 | h2h 조회 주기 | 재분석 기준 |
|---|---:|---:|
| 24~6시간 | 60분 | 무마진 확률 2.0%p |
| 6~2시간 | 30분 | 2.0%p |
| 2시간~30분 | 15분 | 1.5%p |
| 마지막 30분 | 5분 | 1.0%p |

**전체(h2h+spreads+totals) 스냅샷:** 6시간 / 2시간 / 60분 / 30분 / 15분 / 5분 전  
**라인업 감시:** 킥오프 90분 전부터 15분 간격  
**즉시 재분석:** 핸디·토탈 0.25 이동 / 확률 4%p 이상 급변 / 라인업 변경
""")

    st.markdown("### 크레딧 예산")
    budget=st.number_input("월 Odds API 예산",min_value=500,max_value=100000,value=20000,step=500)
    reserve=st.number_input("비상용으로 남길 credits",min_value=0,max_value=10000,value=2000,step=500)
    usable=max(0,int(budget-reserve))
    st.metric("자동 모니터링 사용 가능 예산",f"{usable:,} credits")
    st.caption("알림 전송은 Odds API credit을 사용하지 않습니다. 크레딧은 새 배당을 조회할 때만 소모됩니다.")

    st.markdown("### 연결 상태")
    telegram_token=secret("TELEGRAM_BOT_TOKEN")
    telegram_chat=secret("TELEGRAM_CHAT_ID")
    st.write({
        "Odds API":"연결됨" if ODDS_KEY else "미연결",
        "API-Football":"연결됨" if FOOTBALL_KEY else "미연결",
        "Telegram":"연결됨" if (telegram_token and telegram_chat) else "미연결",
        "KBO/NPB 데이터":"공식 팀기록 + KBO GameCenter / NPB.jp 선발·라인업 — 별도 키 불필요",
    })

    pst=persistence_status()
    st.markdown("### v3.2 영구기록 · CLV · 스마트 자동재분석")
    st.write({
        "영구 DB": "Supabase + 로컬 이중저장" if pst.get("enabled") else "로컬 JSONL만 사용",
        "DB 호스트": pst.get("url") or "미설정",
        "최근 DB 오류": pst.get("last_error") or "없음",
    })
    if not pst.get("enabled"):
        st.info("Supabase를 연결하지 않아도 앱은 정상 작동합니다. 재배포 후에도 예측/정산/CLV 기록을 영구 보존하려면 설정 탭 안내대로 DB를 연결하세요.")
    if ODDS_KEY and st.button("🔄 스마트 자동화 1회 실행",key="smart_refresh_once_ui"):
        try:
            with st.spinner("저장된 예정경기의 배당·라인업/선발 변화를 확인하고 자동 재분석/정산 중..."):
                rr=run_smart_cycle(ODDS_KEY,FOOTBALL_KEY,region="eu")
            st.success(f"시장 관측 {rr.get('observations',0)}개 · 재분석 {rr.get('reanalyzed',0)}경기 · 정산 {rr.get('settled',0)}개")
            if rr.get("errors"):
                st.warning("일부 자동화 보류: "+" / ".join(rr.get("errors",[])[:8]))
            with st.expander("이번 자동화 상세"):
                st.json(rr)
        except Exception as e:
            st.error(f"스마트 자동화 실행 실패: {type(e).__name__}: {e}")
    st.caption("백그라운드에서는 smart_worker.py를 15분 주기로 실행하면 마감 직전 배당이 계속 저장되어 CLV가 계산되고, 시장/라인업/선발 변화 시 최신 분석이 베스트조합에 자동 반영됩니다.")
    _refresh=refresh_events()
    if _refresh:
        with st.expander("최근 자동 재분석 로그",expanded=False):
            _rf=pd.DataFrame(sorted(_refresh,key=lambda r:str(r.get("recorded_at") or ""),reverse=True)[:100])
            _cols=[c for c in ["recorded_at","sport_family","home_team","away_team","reason","saved_snapshots"] if c in _rf.columns]
            st.dataframe(_rf[_cols],use_container_width=True,hide_index=True)

    if telegram_token and telegram_chat:
        if st.button("📨 Telegram 테스트 알림 보내기"):
            try:
                from sports_ev_engine.telegram_notify import TelegramNotifier
                TelegramNotifier(telegram_token,telegram_chat)(
                    "✅ Sports EV Engine v3.4.0\nTelegram 알림 연결 테스트 성공"
                )
                st.success("테스트 알림을 보냈습니다.")
            except Exception as e:
                st.error(f"Telegram 테스트 실패: {e}")
    else:
        st.info("Telegram 알림을 쓰려면 설정 탭의 Secrets에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 추가하세요.")

    st.markdown("""
### 백그라운드 실행
Streamlit Community Cloud 화면 자체는 스마트폰을 닫은 뒤 계속 감시하는 용도로는 적합하지 않습니다.

v3.2부터는 **`smart_worker.py`**가 권장 통합 worker입니다. 저장된 예정 경기 전체(축구/A매치/KBO/NPB/MLB)의 시장을 관측하고, 가능한 종목은 라인업·선발 변화까지 재확인한 뒤 자동 재분석·정산합니다.
Railway / Render / VPS 같은 항상 실행되는 Python worker에서:

```bash
python smart_worker.py
```

한 번만 돌리는 cron/스케줄러라면 `python smart_once.py`를 사용합니다. 기존 `monitor.py` / `monitor_baseball.py`도 남겨두었지만 CLV와 전종목 통합 자동화는 smart worker가 기준입니다.
""")

with tabs[9]:
    st.subheader("API 연결 상태")
    st.write({
        "The Odds API":"연결됨" if ODDS_KEY else "미연결",
        "API-Football":"연결됨" if FOOTBALL_KEY else "미연결",
    })
    st.markdown("""
Streamlit Cloud → **App settings → Secrets** 에 다음 형식으로 저장:

```toml
THE_ODDS_API_KEY = "..."
API_FOOTBALL_KEY = "..."
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."

# 선택: v3.2 영구 DB
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "..."
```

영구 DB를 쓸 경우 ZIP의 **`supabase_schema.sql`**을 Supabase SQL Editor에서 한 번 실행하세요. 서비스 역할 키는 반드시 Streamlit Secrets/서버 환경변수에만 저장하고 GitHub 코드에는 올리지 마세요.

**v2.5 선발/라인업 FINAL 모델**
- KBO 공식 GameCenter에서 예고/확정 선발 자동수집
- KBO 공식 LineUpAnalysis에서 1~9번 타순·포지션·라인업 확정 여부 자동수집
- NPB.jp 예고선발 자동수집
- NPB 공식 경기속보에 오더가 공개되면 1~9번 자동수집
- 공식 개인 투수/타자 기록으로 선발·라인업 영향 보정
- PRE-LINEUP / STARTER CONFIRMED / LINEUP CONFIRMED / FINAL 상태 표시
- FINAL에서만 A등급 허용, 기본 다폴도 FINAL만 사용

**야구 데이터 구성**
- The Odds API: KBO/NPB 현재 배당
- KBO 공식 기록/GameCenter: 팀 기록·선발·확정 라인업
- NPB.jp: 팀/개인 기록·예고선발·공식 경기 오더
- 별도 야구 API 키/구독 불필요
- Negative Binomial 득점분포 + 시장 prior 캘리브레이션
- 배당/선발/라인업 변화 시 `monitor_baseball.py` 재분석 가능

**v2.3에서 추가된 모니터링**
- 20K credits 최적화 시간대별 감시 주기
- h2h 자주 조회 / spreads+totals 지정 시점 스냅샷
- 무마진 확률 2.0→1.5→1.0%p 동적 재분석 기준
- 4%p 급변 즉시 트리거
- 핸디/토탈 0.25 이동 즉시 트리거
- 일반 가격 변화는 2회 연속 관측 후 재분석
- 야구는 경기 150분 전부터 공식 선발/라인업 상태 변화 감시
- Telegram 알림
- 2,000 credits 비상 reserve 기본값
- `monitor.py` 백그라운드 worker / `monitor_once.py` 스케줄러 실행

**v2.2 모델 개선**
- 경쟁 대회 내부 Elo 자동 계산
- 최근 성적을 상대 Elo 수준으로 보정
- 시장 무마진 확률을 calibration prior로 사용
- 원모델-시장 25%p 이상 괴리는 OUTLIER_SHRUNK로 자동 격리
- raw 독립확률과 최종 캘리브레이션 확률을 둘 다 표시
- REVIEW/OUTLIER는 다폴 자동 제외
- 팀별 최근경기 API 호출 제거
- 대회 전체 경기목록 1~2회 호출로 최근폼 계산
- UEFA Nations League는 API-Football league_id 5로 자동 매칭
- API 무료 플랜 요청량 대폭 절약
- API-Football `season` 필수 오류 우회
- 오늘/3일/7일 경기 범위 필터 추가
- 한 경기 API 오류가 전체 분석을 중단하지 않도록 격리
- 수동 Elo/xG 입력 제거
- 팀 최근 경기 자동 수집
- 최근 득점/실점 기반 독립 Poisson 모델 자동 생성
- 승무패/스프레드/O-U 자동 가격화
- lay/exchange 자동 제외
- 최소 북메이커 수 필터
- 비정상 최고배당 필터
- 모델 EV/불확실성 보정/등급 자동 계산
- 2~6폴 자동 생성

**v3.0 ChatGPT-style reasoning layer**
- 독립 모델 이후 xG·확정 라인업·결장·선수 중요도·휴식일 정밀 컨텍스트
- 실제 미수집 신호는 평균으로 대체하지 않고 MISSING 처리
- 반증(counter-case) 위험 자동 생성
- 득점환경 ±10%(야구 ±8%) × 모델/시장 비중 변화의 27개 스트레스 시나리오
- ROBUST / SENSITIVE / FRAGILE / REVIEW / PASS 판정
- 자동 다폴은 ROBUST만 사용
- 경기 전 모델확률·배당·EV·판정을 append-only로 저장
- The Odds API scores 지원 종목은 자동 결과 연결 및 Brier/Log loss/ROI 평가
""")


with tabs[10]:
    st.subheader("📊 v3 모델 사후 검증")
    st.write("경기 전 스냅샷은 결과가 나온 뒤 덮어쓰지 않습니다. 당시 배당·모델확률·EV·ROBUST 판정과 실제 결과를 연결해 확률 정확도와 ROI를 따로 평가합니다.")
    pending=pending_sport_keys()
    if ODDS_KEY:
        if st.button("최근 종료 경기 지금 자동 정산 + MLB 자동복기"):
            api=TheOddsAPI(ODDS_KEY);settled=0;errors=[]
            for sk in pending:
                info=auto_settle(api,sk);settled+=info.get("settled",0)
                if info.get("error"):errors.append(f"{sk}: {info['error']}")
            # Existing MLB settlements from an older build can also be backfilled here.
            review_info=analyze_settled_mlb()
            st.success(f"{settled}개 스냅샷 정산 · MLB 자동복기 {review_info.get('reviewed',0)}개")
            if errors:st.warning(" / ".join(errors))
            if review_info.get("errors"):
                st.caption("MLB 복기 일부 보류: "+" / ".join(review_info.get("errors",[])[:5]))
    elif pending:
        st.info("정산 대기 기록이 있지만 THE_ODDS_API_KEY가 없어 자동 정산을 실행할 수 없습니다.")
    report=evaluation()
    if not report.get("roi_n"):
        st.info("아직 정산된 표본이 없습니다. 경기 전 분석을 실행하면 예측이 저장되고, 경기 종료 뒤 이 탭의 `최근 종료 경기 지금 자동 정산 + MLB 자동복기` 버튼을 누르면 결과가 연결됩니다. `python settle_once.py`는 별도 서버/스케줄러에서 같은 작업을 자동 실행할 때 쓰는 명령입니다. Streamlit 화면에 입력하는 명령은 아닙니다.")
    else:
        overall=report["overall"]
        c1,c2,c3,c4,c5=st.columns(5)
        c1.metric("확률 검증 표본",overall["n"])
        c2.metric("Brier",f"{overall['brier']:.4f}" if overall["n"] else "—")
        c3.metric("Log loss",f"{overall['log_loss']:.4f}" if overall["n"] else "—")
        c4.metric(f"평균 ROI ({overall['roi_n']}건)",f"{overall['roi']*100:.2f}%")
        _clv=overall.get("avg_clv_prob_pp")
        c5.metric(f"평균 CLV ({overall.get('clv_n',0)}건)",(f"{_clv:+.2f}%p" if overall.get('clv_n') and pd.notna(_clv) else "—"))
        groups=pd.DataFrame(report.get("groups",[]))
        if not groups.empty:
            groups["roi"]=(groups["roi"]*100).round(2);groups["hit_rate"]=(groups["hit_rate"]*100).round(2)
            groups[["brier","log_loss"]]=groups[["brier","log_loss"]].round(4)
            if "avg_clv_prob_pp" in groups:groups["avg_clv_prob_pp"]=pd.to_numeric(groups["avg_clv_prob_pp"],errors="coerce").round(2)
            st.dataframe(groups.rename(columns={"sport_family":"종목","market":"마켓","decision":"v3 판정","n":"확률표본","roi_n":"ROI표본","clv_n":"CLV표본","brier":"Brier","log_loss":"Log loss","roi":"ROI(%)","hit_rate":"적중률(%)","avg_clv_prob_pp":"평균 CLV(%p)"}),hide_index=True,use_container_width=True)
        st.caption("Brier/Log loss는 낮을수록 좋습니다. CLV는 분석 당시 가격보다 마감 시장이 해당 픽 방향으로 얼마나 이동했는지(%p)이며 양수일수록 좋은 가격을 선점한 것입니다. smart_worker가 마감 전 시장을 반복 관측해야 계산됩니다. 아시안 쿼터라인의 부분 적특은 ROI에는 포함하지만 Bernoulli 확률검증에서는 제외합니다.")
    st.markdown("### 📈 최근 CLV")
    _settled_rows=load_settled()
    _clv_rows=[r for r in _settled_rows if r.get("closing_observed_at")]
    if not _clv_rows:
        st.info("아직 closing line 관측이 연결된 정산 표본이 없습니다. smart_worker를 경기 전 계속 실행하면 마감 직전 가격이 저장되고 정산 후 CLV가 표시됩니다.")
    else:
        _clv_rows=sorted(_clv_rows,key=lambda r:str(r.get("settled_at") or ""),reverse=True)[:100]
        _clv_df=pd.DataFrame(_clv_rows)
        _clv_show=pd.DataFrame()
        _clv_show["경기"]=_clv_df.get("away_team","").astype(str)+" @ "+_clv_df.get("home_team","").astype(str)
        _clv_show["픽"]=_clv_df.get("selection","").astype(str)
        _clv_show["진입배당"]=pd.to_numeric(_clv_df.get("best_odds"),errors="coerce")
        _clv_show["마감배당"]=pd.to_numeric(_clv_df.get("closing_odds"),errors="coerce")
        _clv_show["진입라인"]=pd.to_numeric(_clv_df.get("point"),errors="coerce")
        _clv_show["마감라인"]=pd.to_numeric(_clv_df.get("closing_point"),errors="coerce")
        _clv_show["CLV 시장확률(%p)"]=pd.to_numeric(_clv_df.get("clv_market_prob_pp"),errors="coerce").round(2)
        _clv_show["CLV 라인(points)"]=pd.to_numeric(_clv_df.get("clv_line_points"),errors="coerce").round(2)
        _clv_show["결과 ROI(%)"]=(pd.to_numeric(_clv_df.get("realized_roi"),errors="coerce")*100).round(1)
        st.dataframe(_clv_show,use_container_width=True,hide_index=True)

    st.markdown("### ⚾ MLB 자동 사후복기")
    reviews=postgame_reviews()
    if not reviews:
        st.info("아직 MLB 자동복기 표본이 없습니다. MLB 예측이 정산되면 실제 이닝별 득점·선발/불펜 박스스코어를 확인해 승패와 언더오버 실패 경로를 자동 분류합니다.")
    else:
        rdf=pd.DataFrame(reviews)
        if "reviewed_at" in rdf.columns:
            rdf=rdf.sort_values("reviewed_at",ascending=False)
        show=pd.DataFrame()
        show["경기시간(KST)"]=pd.to_datetime(rdf.get("commence_time"),utc=True,errors="coerce").dt.tz_convert(ZoneInfo("Asia/Seoul")).dt.strftime("%m/%d %H:%M")
        show["경기"]=rdf.get("away_team","").astype(str)+" @ "+rdf.get("home_team","").astype(str)
        market_map={"h2h":"승패","totals":"언더오버","spreads":"런라인"}
        show["마켓"]=rdf.get("market","").map(market_map).fillna(rdf.get("market",""))
        show["픽"]=rdf.get("selection","").astype(str)+rdf.get("point").map(lambda x:"" if pd.isna(x) else f" {float(x):+g}" if str(x)!="" else "")
        show["사전확률"]=pd.to_numeric(rdf.get("model_win_prob"),errors="coerce").map(lambda x:"—" if pd.isna(x) else f"{x*100:.1f}%")
        show["EV"]=pd.to_numeric(rdf.get("ev_roi"),errors="coerce").map(lambda x:"—" if pd.isna(x) else f"{x*100:+.1f}%")
        show["실제결과"]=rdf.apply(lambda r:"적중" if float(r.get("settle_win") or 0)>float(r.get("settle_loss") or 0) else ("적특" if float(r.get("settle_push") or 0)>0 and float(r.get("settle_loss") or 0)==0 else "미적중"),axis=1)
        show["최종점수"]=rdf.apply(lambda r:f"{int(float(r.get('away_score') or 0))}:{int(float(r.get('home_score') or 0))}",axis=1)
        show["자동분류"]=rdf.get("postgame_class_ko","")
        show["근거"]=rdf.get("postgame_reason","")
        st.dataframe(show.head(200),use_container_width=True,hide_index=True)
        miss=rdf[pd.to_numeric(rdf.get("settle_loss"),errors="coerce").fillna(0)>0].copy()
        if not miss.empty:
            st.markdown("#### 최근 미적중 상세")
            for _,r in miss.head(12).iterrows():
                label=f"{r.get('away_team')} @ {r.get('home_team')} · {r.get('selection')}"
                if pd.notna(r.get("point")):label+=f" {float(r.get('point')):+g}"
                with st.expander(label,expanded=False):
                    st.write(f"**분류:** {r.get('postgame_class_ko')}  ")
                    st.write(f"**사후 근거:** {r.get('postgame_reason')}")
                    if r.get("expected_starter_ip") is not None or r.get("actual_starter_ip") is not None:
                        exp=r.get("expected_starter_ip");act=r.get("actual_starter_ip")
                        st.write(f"**선발 이닝:** 사전 최근평균 {('—' if exp is None else f'{float(exp):.1f}')} → 실제 {('—' if act is None else f'{float(act):.1f}')}")
                    if r.get("expected_starter_bb_pct") is not None or r.get("actual_starter_bb_pct") is not None:
                        exp=r.get("expected_starter_bb_pct");act=r.get("actual_starter_bb_pct")
                        st.write(f"**BB%:** 사전 최근 {('—' if exp is None else f'{float(exp)*100:.1f}%')} → 실제 {('—' if act is None else f'{float(act)*100:.1f}%')}")
        st.caption("GOOD PICK 표시는 결과가 아쉬웠다는 이유만으로 붙이지 않습니다. 7~9회 리드 후 역전, 정규 후반까지 언더 유지 후 late crossing, 연장 승부, 높은 출루/잔루 등 코드로 확인 가능한 조건이 있을 때만 표시합니다. 그 외는 REVIEW 또는 모델 미스 후보로 남깁니다.")

    _pst=persistence_status()
    st.caption("운영 기록: prediction snapshots / market observations(CLV) / settled predictions / MLB postgame reviews. "+("Supabase + 로컬 이중저장 활성화" if _pst.get("enabled") else "현재 로컬 JSONL 저장 — Supabase 연결 시 재배포 후에도 영구 보존")+".")


with tabs[11]:
    st.subheader("🧪 v3.4 모델 연구소")
    st.write("확률 변화, calibration, 상관보정, no-lookahead replay를 검증하는 연구/감사 탭입니다. v3.4의 Source Health·Paper lock·Drift·자금위험은 데이터·리스크 탭에서 확인합니다.")

    st.markdown("### 1) 🔄 픽 변화 로그")
    _changes=change_logs(limit=200)
    if not _changes:
        st.info("같은 픽의 재분석 스냅샷이 2개 이상 쌓이면 변화 로그가 표시됩니다.")
    else:
        _cdf=pd.DataFrame(_changes)
        _show=pd.DataFrame()
        _show["변경시각"] = pd.to_datetime(_cdf["recorded_at"],utc=True,errors="coerce").dt.tz_convert(KST).dt.strftime("%m/%d %H:%M")
        _show["경기"]=_cdf["away_team"].astype(str)+" @ "+_cdf["home_team"].astype(str)
        _show["픽"]=_cdf["selection"].astype(str)
        _show["이전확률"]=(pd.to_numeric(_cdf["old_prob"],errors="coerce")*100).round(1)
        _show["현재확률"]=(pd.to_numeric(_cdf["new_prob"],errors="coerce")*100).round(1)
        _show["변화(%p)"]=pd.to_numeric(_cdf["delta_pp"],errors="coerce").round(1)
        _show["관측된 원인"]=_cdf["reason"]
        st.dataframe(_show,use_container_width=True,hide_index=True)
        st.caption("원인 분해는 저장된 시장·라인업·선발·신호·calibration 변화에 근거한 감사용 설명입니다. 인과 Shapley 분해처럼 정확한 기여도 합산을 가장하지 않습니다.")

    st.markdown("### 2) 🎯 시장별 자동 Calibration")
    _settled=load_settled()
    _groups=sorted({(str(x.get("sport_family") or ""),str(x.get("market") or "")) for x in _settled if x.get("sport_family") and x.get("market")})
    _calrows=[]
    for fam,mkt in _groups:
        c=calibration_curve(_settled,fam,mkt)
        _calrows.append({"종목":fam,"마켓":mkt,"표본":c.get("n",0),"활성":"ON" if c.get("active") else "대기","보정강도":round(float(c.get("reliability") or 0)*100,1),"상태":c.get("reason")})
    if _calrows:
        st.dataframe(pd.DataFrame(_calrows),use_container_width=True,hide_index=True)
    else:
        st.info("정산 표본이 쌓이면 종목×마켓별 calibration이 자동 학습됩니다. 40건 미만에서는 확률을 건드리지 않습니다.")
    st.caption("v3.3 최종확률 = 독립모델/시장/최근폼/정밀컨텍스트 앙상블 → 과거 정산표본 calibration. 한 번의 보정은 기존 확률 대비 최대 ±5%p로 제한됩니다.")

    st.markdown("### 3) ⏱️ 데이터 품질·신선도")
    _fresh=freshness_rows()
    if _fresh:
        _fdf=pd.DataFrame(_fresh)
        _fdf["경기시간(KST)"]=pd.to_datetime(_fdf["commence_time"],utc=True,errors="coerce").dt.tz_convert(KST).dt.strftime("%m/%d %H:%M")
        st.dataframe(_fdf[[c for c in ["경기시간(KST)","경기","sport_family","배당","모델","라인업","Statcast","날씨","부상/뉴스","freshness_risk"] if c in _fdf]],use_container_width=True,hide_index=True)
        st.caption("여기 시간은 공급자가 데이터를 발표한 시각이 아니라 우리 엔진이 마지막으로 가져오거나 확인한 시각입니다.")
    else:
        st.info("예정 경기 분석 스냅샷이 생기면 마지막 수집/확인 시각을 표시합니다.")

    st.markdown("### 4) 🕰️ 과거 시점 Replay / Backtest")
    _r1,_r2,_r3=st.columns(3)
    _replay_date=_r1.date_input("Replay 날짜(KST)",value=datetime.now(KST).date()-timedelta(days=1),key="replay_date")
    _before=_r2.selectbox("킥오프 몇 분 전 상태?",[0,5,15,30,60,120,360],index=3,key="replay_before")
    _versions=sorted({str(x.get("model_version")) for x in load_settled() if x.get("model_version")})
    _ver=_r3.selectbox("모델 버전",["전체"]+_versions,index=0,key="replay_version")
    _rep=replay_day(_replay_date,_before,None if _ver=="전체" else _ver)
    rc1,rc2,rc3=st.columns(3)
    rc1.metric("당시 후보",len(_rep.get("candidates",[])))
    rc2.metric("정산 연결",_rep.get("settled_n",0))
    _sr=_rep.get("single_roi")
    rc3.metric("후보 평균 실제 ROI",("—" if _sr is None or pd.isna(_sr) else f"{_sr*100:+.1f}%"))
    if _rep.get("best_two"):
        st.write("**당시 기준 2폴:**",_rep["best_two"]["combo_label"])
        _cr=_rep.get("best_two_realized_roi")
        st.caption("실제 조합 ROI: "+("아직 미정산" if _cr is None else f"{_cr*100:+.1f}%"))
    st.caption("Replay는 저장된 불변 스냅샷만 사용합니다. 당시 저장하지 않았던 과거 provider 입력을 현재 데이터로 소급해 만들어내지 않으므로 no-lookahead를 지킵니다.")

    _b1,_b2=st.columns(2)
    _start=_b1.date_input("버전 비교 시작",value=datetime.now(KST).date()-timedelta(days=30),key="bt_start")
    _end=_b2.date_input("버전 비교 종료",value=datetime.now(KST).date(),key="bt_end")
    _vb=pd.DataFrame(version_backtest(_start,_end))
    if not _vb.empty:
        _vb["ROI(%)"]=(pd.to_numeric(_vb["roi"],errors="coerce")*100).round(2);_vb["Brier"]=pd.to_numeric(_vb["brier"],errors="coerce").round(4)
        st.dataframe(_vb[["model_version","n","ROI(%)","Brier"]],use_container_width=True,hide_index=True)

    st.markdown("### 5) 🧯 자동 사후 원인 통계")
    _fs=failure_statistics()
    if not _fs.get("losses"):
        st.info("MLB 자동복기 미적중 표본이 쌓이면 실패유형 비중과 반복 패턴을 집계합니다.")
    else:
        st.metric("자동복기 미적중 표본",_fs["losses"])
        _cause=pd.DataFrame(_fs["rows"])
        if not _cause.empty:
            _cause["비중(%)"]=(pd.to_numeric(_cause["share"],errors="coerce")*100).round(1)
            st.dataframe(_cause.rename(columns={"market":"마켓","cause":"원인","n":"건수","market_losses":"마켓 미적중"}),use_container_width=True,hide_index=True)
        if _fs.get("hints"):
            st.markdown("#### 반복 실패 → 모델 수정 후보")
            for h in _fs["hints"]:
                st.write(f"- **{h['cause']}** {h['n']}건 ({h['share']*100:.1f}%) → {h['suggestion']}")
        st.caption("이 영역은 가중치를 자동으로 바꾸지 않습니다. 표본이 반복되는 실패유형만 ‘수정 후보’로 올려 과적합을 막습니다.")

    st.markdown("### 6) 🧩 앙상블 진단")
    _pred=latest_snapshots_for_kst_date(datetime.now(KST).date())
    if _pred.empty or "ensemble_summary" not in _pred.columns:
        st.info("v3.3으로 새 분석을 실행하면 독립모델·시장·최근폼·정밀 컨텍스트의 의견 차이가 여기에 표시됩니다.")
    else:
        _ed=_pred[_pred["ensemble_summary"].notna()].copy()
        if not _ed.empty:
            _ev=pd.DataFrame({
                "경기":_ed.get("away_team","").astype(str)+" @ "+_ed.get("home_team","").astype(str),
                "픽":_ed.get("selection","").astype(str),
                "앙상블":_ed.get("ensemble_summary",""),
                "모델간 최대차이(%p)":pd.to_numeric(_ed.get("ensemble_disagreement_pp"),errors="coerce").round(1),
                "Calibration 표본":pd.to_numeric(_ed.get("calibration_n"),errors="coerce"),
                "최종 보정(%p)":pd.to_numeric(_ed.get("adaptive_delta_pp"),errors="coerce").round(1),
                "Gate":_ed.get("ensemble_gate",""),
            })
            st.dataframe(_ev.head(200),use_container_width=True,hide_index=True)
    st.caption("모델끼리 크게 충돌하면 REVIEW로 보내 자동 다폴에서 제외합니다. 같은 경기 두 옵션 조합 금지와 별개로, 다폴 확률은 정산 데이터에서 추정된 pair 상관을 사용할 수 있을 때 단순 곱셈을 보정합니다.")


with tabs[12]:
    st.subheader("🛡️ 데이터·리스크 센터")
    st.write("v3.4의 감사/안전 계층입니다. 소스 fallback, blind paper-trading 잠금, 확률 기여도, model drift, drawdown을 한 곳에서 확인합니다.")

    st.markdown("### 1) 🩺 Source Health + Fallback")
    _shr=source_health_rows()
    if _shr:
        _shdf=pd.DataFrame(_shr)
        st.dataframe(_shdf,use_container_width=True,hide_index=True)
        st.caption("OK=최근 실제 수집, FALLBACK=대체/예상 소스 사용, WAIT=공식 게시 대기, MISSING=실제 수집되지 않음. MISSING을 평균값으로 꾸미지 않습니다.")
    else:
        st.info("예정 경기 분석을 한 번 실행하면 실제 사용한 소스 상태가 표시됩니다.")

    st.markdown("### 2) 🔒 Blind Paper-Trading / No-lookahead")
    _ps=paper_summary()
    p1,p2,p3=st.columns(3)
    p1.metric("잠긴 스냅샷",_ps.get("locked",0))
    p2.metric("킥오프 전 적격",_ps.get("pregame_eligible",0))
    p3.metric("정산된 적격",_ps.get("settled_eligible",0))
    st.caption("v3.4부터 모든 예측 스냅샷은 append-only로 잠기며, 킥오프 이후 생성된 스냅샷은 Calibration·Correlation·Replay/Backtest 학습표본에서 자동 제외됩니다.")

    st.markdown("### 3) 🧮 Feature Attribution")
    _today=latest_snapshots_for_kst_date(datetime.now(KST).date())
    if _today.empty:
        st.info("오늘 분석 스냅샷이 생기면 확률 기여도를 확인할 수 있습니다.")
    else:
        _today=_today.copy()
        _today["_label"]=_today.get("away_team","").astype(str)+" @ "+_today.get("home_team","").astype(str)+" · "+_today.get("selection","").astype(str)
        _sel=st.selectbox("기여도 확인 픽",list(_today.index),format_func=lambda i:_today.loc[i,"_label"],key="attr_pick")
        _ar=attribution(_today.loc[_sel].to_dict())
        a1,a2=st.columns(2)
        a1.metric("시장 no-vig", "—" if pd.isna(_ar.get("market_prob")) else f"{_ar['market_prob']*100:.1f}%")
        a2.metric("최종 조건부확률", "—" if pd.isna(_ar.get("final_cond_prob")) else f"{_ar['final_cond_prob']*100:.1f}%")
        st.dataframe(pd.DataFrame(_ar.get("probability_contributions") or []),use_container_width=True,hide_index=True)
        if _ar.get("context_ledger"):
            with st.expander("정밀 컨텍스트 ledger"):
                st.dataframe(pd.DataFrame(_ar["context_ledger"]),use_container_width=True,hide_index=True)
        st.caption("확률 기여도는 시장 anchor 대비 앙상블 성분의 감사용 근사 분해입니다. 비선형 라인업/xG/정규화 효과는 잔차로 분리하며 Shapley처럼 정확한 인과분해라고 주장하지 않습니다.")

    st.markdown("### 4) 🚨 Model Drift 경보")
    _dr=drift_rows()
    if _dr:
        _dd=pd.DataFrame(_dr)
        st.dataframe(_dd,use_container_width=True,hide_index=True)
        if any(x.get("status")=="ALERT" for x in _dr):
            st.warning("최근 Brier 또는 CLV가 과거 baseline보다 의미 있게 악화된 시장이 있습니다. 해당 시장은 자동 다폴 포함 전 원인 점검이 필요합니다.")
    else:
        st.info("정산된 blind paper 표본이 쌓이면 종목×마켓별 drift를 자동 감시합니다.")

    st.markdown("### 5) 💰 Bankroll / Drawdown 시뮬레이터")
    _d=st.date_input("시뮬레이션 후보 날짜(KST)",value=datetime.now(KST).date(),key="risk_date")
    _snap=latest_snapshots_for_kst_date(_d)
    _cand=prepare_daily_candidates(_snap)
    if _cand.empty:
        st.info("선택 날짜에 통합 +EV 후보가 없습니다.")
    else:
        r1,r2,r3,r4=st.columns(4)
        _bank=r1.number_input("가상 시작자금",min_value=10000,value=1000000,step=100000,key="risk_bank")
        _kelly=r2.selectbox("Kelly 배수",[0.10,0.25,0.50],index=1,key="risk_kelly")
        _cap=r3.selectbox("픽당 최대 노출",[0.01,0.02,0.03,0.05],index=1,key="risk_cap")
        _dcap=r4.selectbox("1회차 총노출 상한",[0.03,0.05,0.08,0.10],index=2,key="risk_dcap")
        _sim=simulate_bankroll(_cand.head(12).to_dict("records"),bankroll=_bank,paths=2000,cycles=100,kelly_mult=_kelly,per_bet_cap=_cap,daily_cap=_dcap)
        if _sim.get("paths"):
            c1,c2,c3,c4=st.columns(4)
            c1.metric("100회 후 중앙값",f"{_sim['median_final']:,.0f}")
            c2.metric("10% 하위 결과",f"{_sim['p10_final']:,.0f}")
            c3.metric("중앙 최대낙폭",f"{_sim['median_max_drawdown']*100:.1f}%")
            c4.metric("초기자금 50% 이하 확률",f"{_sim['ruin_probability']*100:.1f}%")
            st.write({"사용 후보":_sim.get("legs"),"실효 총노출":f"{_sim.get('effective_daily_exposure',0)*100:.1f}%","90%ile 최대낙폭":f"{_sim.get('p90_max_drawdown',0)*100:.1f}%"})
        st.caption("Monte Carlo 결과는 입력 확률이 맞다는 가정의 위험 시뮬레이션이며 수익 보장이 아닙니다. 보수확률과 노출 상한을 사용해 연패/낙폭을 별도로 봅니다.")
