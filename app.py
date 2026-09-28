
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.providers.api_football import APIFootball
from sports_ev_engine.providers.mlb_statsapi import schedule as mlb_schedule
from sports_ev_engine.market import consensus, clean_odds
from sports_ev_engine.auto_soccer import analyze_event
from sports_ev_engine.competition_form import build_competition_pool
from sports_ev_engine.core.parlay import optimize_parlays
from sports_ev_engine.providers.sofascore_baseball import SofaScoreBaseball
from sports_ev_engine.auto_baseball import analyze_baseball_event, build_league_pool

st.set_page_config(page_title="Sports EV Engine v2.4",layout="wide")
st.title("Sports EV Engine v2.4")
st.caption("종목 선택 → 배당 수집 → 상대전력 Elo + 최근폼 → 시장 prior 캘리브레이션 → BE/Edge/EV → 2~6폴")

def secret(name):
    try:
        if name in st.secrets:return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name)

ODDS_KEY=secret("THE_ODDS_API_KEY")
FOOTBALL_KEY=secret("API_FOOTBALL_KEY")


tabs=st.tabs(["⚡ 완전자동 축구","⚾ KBO/NPB 자동분석","실시간 배당","시장 가격","MLB","다폴","📡 모니터링","설정"])

with tabs[0]:
    st.subheader("완전자동 축구 분석")
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
            soccer=[s for s in sports if s.get("active") and str(s.get("key","")).startswith("soccer_")]
            options={f'{s.get("title")} — {s.get("description","")}':s["key"] for s in soccer}
        except Exception:
            options={"UEFA Nations League":"soccer_uefa_nations_league"}
    else:
        options={"UEFA Nations League":"soccer_uefa_nations_league"}
    label=c1.selectbox("종목",list(options.keys()),index=0)
    sport_key=options[label]
    region=c2.selectbox("배당 지역",["eu","uk","us","au"],index=0)
    recent_n=c3.selectbox("최근 경기 반영", [4,5,6,8,10], index=2)

    date_scope=st.selectbox(
        "경기 범위",
        ["오늘(KST)","앞으로 3일","앞으로 7일","전체"],
        index=0,
        help="API가 반환하는 미래 전체 경기를 전부 분석하지 않고 원하는 날짜 범위만 분석합니다."
    )

    markets=st.multiselect("분석 마켓",["h2h","spreads","totals"],default=["h2h","spreads","totals"])
    min_books=st.slider("컨센서스 최소 북메이커 수",2,6,3)
    run=st.button("🚀 선택 종목 전체 자동분석",type="primary",disabled=not(ODDS_KEY and FOOTBALL_KEY))

    if run:
        try:
            with st.status("배당과 팀 데이터를 자동 분석 중...",expanded=True) as status:
                odds_api=TheOddsAPI(ODDS_KEY)
                events,headers=odds_api.odds(sport_key,region,",".join(markets))
                raw=clean_odds(odds_api.flatten(events))
                # Limit to requested KST date window before any API-Football calls.
                if not raw.empty and date_scope != "전체":
                    kst=ZoneInfo("Asia/Seoul")
                    now_kst=datetime.now(kst)
                    ts=pd.to_datetime(raw["commence_time"],utc=True,errors="coerce").dt.tz_convert(kst)
                    if date_scope=="오늘(KST)":
                        mask=ts.dt.date==now_kst.date()
                    elif date_scope=="앞으로 3일":
                        end=(now_kst+timedelta(days=3))
                        mask=(ts>=now_kst)&(ts<=end)
                    else:
                        end=(now_kst+timedelta(days=7))
                        mask=(ts>=now_kst)&(ts<=end)
                    raw=raw[mask].copy()
                if raw.empty:
                    st.warning("선택한 날짜 범위에 배당이 있는 경기가 없습니다.")
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
                    st.write(f"[{idx}/{total_events}] {home} - {away}")
                    try:
                        analyzed,meta=analyze_event(g,pool,recent_n=recent_n)
                        if not analyzed.empty:
                            analyzed["sport_key"]=sport_key
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
                    ranked=ranked.sort_values(["conservative_ev_roi","edge_pp"],ascending=False)
                st.session_state["ranked"]=ranked
                st.session_state["event_meta"]=event_meta
                remain=headers.get("x-requests-remaining")
                status.update(label=f"완료 — Odds API 남은 요청량 {remain}",state="complete")
        except Exception as e:
            st.error(f"자동분석 실패: {e}")

    if "ranked" in st.session_state and not st.session_state["ranked"].empty:
        ranked=st.session_state["ranked"]
        st.markdown("### 오늘 +EV 후보")
        view=ranked[ranked["grade"]!="PASS"].copy()
        cols=["grade","sanity","display_pick","best_book","best_odds","books",
              "consensus_prob","raw_independent_prob","model_win_prob","push_prob",
              "break_even","raw_market_gap_pp","final_market_gap_pp","edge_pp",
              "ev_roi","conservative_ev_roi","uncertainty_pp","model_weight",
              "home_elo","away_elo","home_form_matches","away_form_matches",
              "home_lambda","away_lambda"]
        view=view[cols]
        for c in ["consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even","model_weight"]:
            view[c]=(view[c]*100).round(1)
        for c in ["ev_roi","conservative_ev_roi"]:
            view[c]=(view[c]*100).round(1)
        view["edge_pp"]=view["edge_pp"].round(1)
        view["raw_market_gap_pp"]=view["raw_market_gap_pp"].round(1)
        view["final_market_gap_pp"]=view["final_market_gap_pp"].round(1)
        view["home_elo"]=view["home_elo"].round(0)
        view["away_elo"]=view["away_elo"].round(0)
        view["home_lambda"]=view["home_lambda"].round(2)
        view["away_lambda"]=view["away_lambda"].round(2)
        st.dataframe(view.head(100),use_container_width=True,hide_index=True)

        st.markdown("""
**sanity 해석**
- `OK`: 모델-시장 괴리 정상 범위
- `CHECK`: 원모델과 시장 차이가 다소 큼
- `HIGH_DISAGREEMENT`: 강한 괴리, 등급 하향
- `OUTLIER_SHRUNK`: 과도한 괴리. 시장 prior로 강하게 축소하고 자동 다폴 제외
""")
        st.caption("raw_independent_prob=Elo+상대보정 최근폼 원모델, model_win_prob=시장 prior로 캘리브레이션한 최종확률. OUTLIER_SHRUNK는 자동 조합 제외.")
    elif "ranked" in st.session_state:
        st.info("현재 필터와 데이터에서 +EV 후보가 없습니다.")

    if st.session_state.get("failures"):
        with st.expander(f"분석하지 못한 경기 {len(st.session_state['failures'])}개 보기"):
            st.dataframe(pd.DataFrame(st.session_state["failures"]),use_container_width=True,hide_index=True)


with tabs[1]:
    st.subheader("⚾ KBO / NPB 완전자동 분석")
    st.write("The Odds API 배당 + SofaScore 경기/라인업/선발 데이터로 최근 득실·상대전력·선발투수를 자동 반영합니다.")
    st.caption("선발/라인업 데이터가 아직 없으면 LOW/MEDIUM 품질로 표시하고 A/B 등급을 제한합니다. 시장 확률을 그대로 복사하지 않고 독립 모델을 만든 뒤 과대 괴리만 보정합니다.")

    if not ODDS_KEY:
        st.warning("THE_ODDS_API_KEY가 필요합니다.")

    b1,b2,b3,b4=st.columns(4)
    league=b1.selectbox("리그",["KBO","NPB"],index=0)
    baseball_key={"KBO":"baseball_kbo","NPB":"baseball_npb"}[league]
    bregion=b2.selectbox("배당 지역",["eu","uk","us","au"],index=0,key="baseball_region")
    brecent=b3.selectbox("최근 경기 반영",[6,8,10,12],index=2)
    bbooks=b4.slider("최소 북메이커",1,5,2,key="baseball_books")
    bmarkets=st.multiselect("야구 분석 마켓",["h2h","spreads","totals"],default=["h2h","spreads","totals"],key="baseball_markets")
    brun=st.button("⚾ 선택 리그 전체 자동분석",type="primary",disabled=not ODDS_KEY)

    if brun:
        try:
            with st.status(f"{league} 배당/최근경기/선발 데이터를 분석 중...",expanded=True) as status:
                odds_api=TheOddsAPI(ODDS_KEY)
                events,headers=odds_api.odds(baseball_key,bregion,",".join(bmarkets))
                rawb=clean_odds(odds_api.flatten(events))
                st.session_state["baseball_raw_odds"]=rawb
                if rawb.empty:
                    st.warning("현재 배당이 있는 경기가 없습니다.")
                    st.session_state["baseball_ranked"]=pd.DataFrame()
                    status.update(label="배당 경기 없음",state="complete")
                    st.stop()

                marketb=consensus(rawb,min_books=bbooks)
                st.session_state["baseball_market"]=marketb
                sofa=SofaScoreBaseball()

                # Build one common league pool using the first current event if we can match it.
                seed=None
                try:
                    r0=marketb.iloc[0]
                    seed=sofa.find_event(r0["home_team"],r0["away_team"],r0["commence_time"])
                except Exception:
                    pass
                pool=build_league_pool(sofa,league,seed)

                all_rows=[]; failures=[]; metas=[]
                groups=list(marketb.groupby("event_id"))
                for i,(eid,g) in enumerate(groups,start=1):
                    home=g.iloc[0]["home_team"]; away=g.iloc[0]["away_team"]
                    st.write(f"[{i}/{len(groups)}] {home} - {away}")
                    try:
                        analyzed,meta=analyze_baseball_event(g,sofa,league,recent_n=brecent,pool=pool)
                        metas.append(meta)
                        if not analyzed.empty:
                            all_rows.append(analyzed)
                        else:
                            failures.append({"경기":f"{home} - {away}","이유":meta.get("reason",meta.get("status","데이터 없음"))})
                    except Exception as e:
                        failures.append({"경기":f"{home} - {away}","이유":str(e)})

                rankedb=pd.concat(all_rows,ignore_index=True) if all_rows else pd.DataFrame()
                if not rankedb.empty:
                    rankedb=rankedb.sort_values(["conservative_ev_roi","edge_pp"],ascending=False)
                st.session_state["baseball_ranked"]=rankedb
                st.session_state["baseball_failures"]=failures
                st.session_state["baseball_meta"]=metas
                status.update(label=f"완료 — Odds API 남은 요청량 {headers.get('x-requests-remaining')}",state="complete")
        except Exception as e:
            st.error(f"야구 자동분석 실패: {e}")

    if "baseball_ranked" in st.session_state and not st.session_state["baseball_ranked"].empty:
        rb=st.session_state["baseball_ranked"]
        st.markdown("### KBO/NPB +EV 후보")
        vb=rb[rb["grade"]!="PASS"].copy()
        cols=[
            "grade","data_quality","sanity","display_pick","best_book","best_odds","books",
            "consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even",
            "edge_pp","conservative_ev_roi","uncertainty_pp","lineup_confirmed",
            "away_starter","away_starter_era","away_starter_whip",
            "home_starter","home_starter_era","home_starter_whip",
            "away_recent_rf","away_recent_ra","home_recent_rf","home_recent_ra",
            "away_expected_runs","home_expected_runs",
        ]
        cols=[c for c in cols if c in vb.columns]
        vb=vb[cols]
        for c in ["consensus_prob","raw_independent_prob","model_win_prob","push_prob","break_even"]:
            if c in vb: vb[c]=(vb[c]*100).round(1)
        if "conservative_ev_roi" in vb: vb["conservative_ev_roi"]=(vb["conservative_ev_roi"]*100).round(1)
        for c in ["edge_pp","uncertainty_pp","away_recent_rf","away_recent_ra","home_recent_rf","home_recent_ra","away_expected_runs","home_expected_runs","away_starter_era","home_starter_era","away_starter_whip","home_starter_whip"]:
            if c in vb: vb[c]=pd.to_numeric(vb[c],errors="coerce").round(2)
        st.dataframe(vb.head(100),use_container_width=True,hide_index=True)
        st.caption("HIGH=양팀 선발+확정 라인업 확인, MEDIUM=일부 확인, LOW=선발/라인업 미확인. LOW에서는 강한 등급을 자동 제한합니다.")

        st.markdown("### 야구 2~6폴")
        sizesb=st.multiselect("야구 폴더 수",[2,3,4,5,6],default=[2,3],key="baseball_parlay_sizes")
        rb_parlay=rb.copy()
        # LOW data-quality is excluded from automatic parlays.
        if "data_quality" in rb_parlay:
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
        st.info("현재 데이터/필터에서 표시할 +EV 후보가 없습니다.")

    if st.session_state.get("baseball_failures"):
        with st.expander(f"분석하지 못한 야구 경기 {len(st.session_state['baseball_failures'])}개"):
            st.dataframe(pd.DataFrame(st.session_state["baseball_failures"]),use_container_width=True,hide_index=True)


with tabs[2]:
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

with tabs[3]:
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

with tabs[4]:
    st.subheader("MLB 일정/예고선발")
    d=st.date_input("날짜",date.today())
    if st.button("MLB 불러오기"):
        try: st.session_state["mlb"]=mlb_schedule(str(d))
        except Exception as e: st.error(str(e))
    if "mlb" in st.session_state:
        st.dataframe(st.session_state["mlb"],use_container_width=True,hide_index=True)

with tabs[5]:
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


with tabs[6]:
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
        "KBO/NPB 데이터":"SofaScore 공개 데이터 — 별도 키 불필요",
    })

    if telegram_token and telegram_chat:
        if st.button("📨 Telegram 테스트 알림 보내기"):
            try:
                from sports_ev_engine.telegram_notify import TelegramNotifier
                TelegramNotifier(telegram_token,telegram_chat)(
                    "✅ Sports EV Engine v2.4\nTelegram 알림 연결 테스트 성공"
                )
                st.success("테스트 알림을 보냈습니다.")
            except Exception as e:
                st.error(f"Telegram 테스트 실패: {e}")
    else:
        st.info("Telegram 알림을 쓰려면 설정 탭의 Secrets에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 추가하세요.")

    st.markdown("""
### 백그라운드 실행
Streamlit Community Cloud 화면 자체는 스마트폰을 닫은 뒤 계속 감시하는 용도로는 적합하지 않습니다.

축구는 **`monitor.py`**, KBO/NPB는 **`monitor_baseball.py`**가 실제 백그라운드 감시 프로그램입니다.
Railway / Render / VPS 같은 항상 실행되는 Python worker에서:

```bash
python monitor.py
# KBO + NPB는 별도 worker
python monitor_baseball.py
```

를 실행하면 휴대폰과 Streamlit을 닫아도 계속 감시합니다.

`monitor_once.py`는 cron/스케줄러에서 한 번만 실행할 때 사용합니다.
""")

with tabs[7]:
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
```

**v2.4 KBO/NPB 추가**
- `baseball_kbo`, `baseball_npb` 실시간 배당 자동수집
- SofaScore 최근 경기/라인업/선발 자동 매칭
- 최근 득점·실점 + 상대전력 보정
- 선발 시즌 ERA/WHIP/K-BB 사용 가능 시 자동 반영
- Negative Binomial 득점분포로 승패/핸디/O-U 가격화
- 데이터 품질 HIGH/MEDIUM/LOW 표시
- 선발/라인업 미확인 LOW는 강한 등급 자동 제한
- KBO/NPB 배당 급변 및 라인업 변화 Telegram 알림
- `monitor_baseball.py`로 KBO+NPB 백그라운드 감시
- SofaScore 데이터는 The Odds API 크레딧을 사용하지 않음

**v2.3에서 추가된 모니터링**
- 20K credits 최적화 시간대별 감시 주기
- h2h 자주 조회 / spreads+totals 지정 시점 스냅샷
- 무마진 확률 2.0→1.5→1.0%p 동적 재분석 기준
- 4%p 급변 즉시 트리거
- 핸디/토탈 0.25 이동 즉시 트리거
- 일반 가격 변화는 2회 연속 관측 후 재분석
- 경기 90분 전부터 라인업 15분 감시
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

라인업/개별 선수 중요도까지 완전자동 반영하는 것은 다음 단계입니다.
""")
