
import os
from datetime import date
import pandas as pd
import streamlit as st

from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.providers.api_football import APIFootball
from sports_ev_engine.providers.mlb_statsapi import schedule as mlb_schedule
from sports_ev_engine.market import consensus, clean_odds
from sports_ev_engine.auto_soccer import analyze_event
from sports_ev_engine.core.parlay import optimize_parlays

st.set_page_config(page_title="Sports EV Engine v2.1.1.1",layout="wide")
st.title("Sports EV Engine v2.1.1.1")
st.caption("종목 선택 → 배당 수집 → 축구 최근폼 모델 → BE/Edge/EV → 2~6폴 자동 생성")

def secret(name):
    try:
        if name in st.secrets:return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name)

ODDS_KEY=secret("THE_ODDS_API_KEY")
FOOTBALL_KEY=secret("API_FOOTBALL_KEY")

if "team_cache" not in st.session_state:
    st.session_state["team_cache"]={}

tabs=st.tabs(["⚡ 완전자동 축구","실시간 배당","시장 가격","MLB","다폴","설정"])

with tabs[0]:
    st.subheader("완전자동 축구 분석")
    st.write("배당과 팀 최근 경기 데이터를 자동으로 불러와 승무패·핸디·O/U의 모델확률과 EV를 계산합니다.")
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

    markets=st.multiselect("분석 마켓",["h2h","spreads","totals"],default=["h2h","spreads","totals"])
    min_books=st.slider("컨센서스 최소 북메이커 수",2,6,3)
    run=st.button("🚀 선택 종목 전체 자동분석",type="primary",disabled=not(ODDS_KEY and FOOTBALL_KEY))

    if run:
        try:
            with st.status("배당과 팀 데이터를 자동 분석 중...",expanded=True) as status:
                odds_api=TheOddsAPI(ODDS_KEY)
                events,headers=odds_api.odds(sport_key,region,",".join(markets))
                raw=clean_odds(odds_api.flatten(events))
                st.session_state["raw_odds"]=raw
                market=consensus(raw,min_books=min_books)
                st.session_state["market"]=market
                st.write(f"배당: {len(events)}경기 / {len(raw)}개 항목")

                foot=APIFootball(FOOTBALL_KEY)
                all_rows=[]
                event_meta=[]
                total_events=market["event_id"].nunique() if not market.empty else 0
                for idx,(eid,g) in enumerate(market.groupby("event_id"),start=1):
                    st.write(f"[{idx}/{total_events}] {g.iloc[0]['home_team']} - {g.iloc[0]['away_team']}")
                    analyzed,meta=analyze_event(g,foot,st.session_state["team_cache"],recent_n=recent_n)
                    if not analyzed.empty:
                        analyzed["sport_key"]=sport_key
                        all_rows.append(analyzed)
                    event_meta.append(meta)
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
        cols=["grade","display_pick","best_book","best_odds","books","consensus_prob",
              "model_win_prob","push_prob","break_even","edge_pp","ev_roi",
              "conservative_ev_roi","uncertainty_pp","home_lambda","away_lambda"]
        view=view[cols]
        for c in ["consensus_prob","model_win_prob","push_prob","break_even"]:
            view[c]=(view[c]*100).round(1)
        for c in ["ev_roi","conservative_ev_roi"]:
            view[c]=(view[c]*100).round(1)
        view["edge_pp"]=view["edge_pp"].round(1)
        view["home_lambda"]=view["home_lambda"].round(2)
        view["away_lambda"]=view["away_lambda"].round(2)
        st.dataframe(view.head(100),use_container_width=True,hide_index=True)
        st.caption("A/B/C는 모델 EV와 불확실성 보정을 반영한 등급입니다. PASS는 조합에서 제외됩니다.")
    elif "ranked" in st.session_state:
        st.info("현재 필터와 데이터에서 +EV 후보가 없습니다.")

with tabs[1]:
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

with tabs[2]:
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

with tabs[3]:
    st.subheader("MLB 일정/예고선발")
    d=st.date_input("날짜",date.today())
    if st.button("MLB 불러오기"):
        try: st.session_state["mlb"]=mlb_schedule(str(d))
        except Exception as e: st.error(str(e))
    if "mlb" in st.session_state:
        st.dataframe(st.session_state["mlb"],use_container_width=True,hide_index=True)

with tabs[4]:
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

with tabs[5]:
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
```

**v2.1에서 달라진 점**
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
