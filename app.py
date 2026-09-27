import os
from datetime import date
import pandas as pd
import streamlit as st

from sports_ev_engine.providers.the_odds_api import TheOddsAPI
from sports_ev_engine.providers.mlb_statsapi import schedule as mlb_schedule
from sports_ev_engine.providers.api_football import APIFootball
from sports_ev_engine.market import build_market_consensus
from sports_ev_engine.pipeline import rank_bets, build_parlays
from sports_ev_engine.models.soccer import SoccerFeatures, price_market
from sports_ev_engine.models.mlb import MLBFeatures, win_probability as mlb_win_probability
from sports_ev_engine.models.nfl import NFLFeatures, win_probability as nfl_win_probability

st.set_page_config(page_title="Sports EV Engine v2", layout="wide")
st.title("Sports EV Engine v2")
st.caption("오늘 경기 자동 불러오기 · 실시간 배당 · 무마진 시장확률 · 독립모델 EV · 2~6폴 자동조합")


def get_secret(name):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name)


ODDS_KEY = get_secret("THE_ODDS_API_KEY")
FOOTBALL_KEY = get_secret("API_FOOTBALL_KEY")

TABS = st.tabs([
    "오늘 자동분석",
    "실시간 배당",
    "축구 모델",
    "MLB",
    "NFL 모델",
    "라인업/부상",
    "후보 EV",
    "다폴",
    "설정",
])

with TABS[0]:
    st.subheader("오늘 배당 자동분석")
    st.write("여러 북메이커의 실시간 배당을 불러온 뒤 마진을 제거해 시장 공정확률과 가장 좋은 가격을 비교합니다.")

    if not ODDS_KEY:
        st.warning("실시간 배당 API 키가 아직 없습니다. 맨 오른쪽 '설정' 탭에서 연결하면 이 기능이 켜집니다.")

    if ODDS_KEY and st.button("사용 가능한 종목 불러오기"):
        try:
            api = TheOddsAPI(ODDS_KEY)
            sports = api.sports()
            active = [s for s in sports if s.get("active")]
            st.session_state["sports_catalog"] = active
            st.success(f"사용 가능한 종목 {len(active)}개를 불러왔습니다.")
        except Exception as e:
            st.error(f"종목 목록 불러오기 실패: {e}")

    if "sports_catalog" in st.session_state:
        sports = st.session_state["sports_catalog"]
        labels = {f"{s.get('title')} — {s.get('description','')}": s.get("key") for s in sports}
        selected_label = st.selectbox("종목 선택", list(labels.keys()))
        sport_key = labels[selected_label]
    else:
        sport_key = st.text_input("Sport key", value="soccer_uefa_nations_league")

    c1, c2 = st.columns(2)
    region = c1.selectbox("배당 지역", ["eu", "us", "uk", "au"], index=0)
    markets = c2.multiselect("마켓", ["h2h", "spreads", "totals"], default=["h2h", "spreads", "totals"])

    if st.button("오늘 배당 자동 불러오기", type="primary", disabled=not bool(ODDS_KEY)):
        try:
            api = TheOddsAPI(ODDS_KEY)
            events, headers = api.odds(sport_key, regions=region, markets=",".join(markets))
            live = api.flatten(events)
            consensus = build_market_consensus(live)
            st.session_state["live_odds"] = live
            st.session_state["market_consensus"] = consensus
            st.success(f"{len(events)}경기 / {len(live)}개 배당 항목을 불러왔습니다.")
            remaining = headers.get("x-requests-remaining")
            if remaining is not None:
                st.caption(f"Odds API 남은 요청량: {remaining}")
        except Exception as e:
            st.error(f"배당 불러오기 실패: {e}")

    if "market_consensus" in st.session_state and not st.session_state["market_consensus"].empty:
        show = st.session_state["market_consensus"].copy()
        show["consensus_prob"] = (show["consensus_prob"] * 100).round(2)
        show["best_be"] = (show["best_be"] * 100).round(2)
        show["market_edge_pp"] = show["market_edge_pp"].round(2)
        show["market_ev"] = (show["market_ev"] * 100).round(2)
        st.markdown("### 시장 대비 가격 우위 TOP")
        st.dataframe(show.head(100), use_container_width=True, hide_index=True)
        st.info("여기 market_ev는 '독립 스포츠 모델 EV'가 아니라 여러 북메이커의 무마진 컨센서스 대비 가격 우위입니다.")

with TABS[1]:
    st.subheader("실시간 배당 원본")
    if "live_odds" not in st.session_state:
        st.info("'오늘 자동분석'에서 먼저 배당을 불러오세요.")
    else:
        df = st.session_state["live_odds"]
        q = st.text_input("팀/선택 검색")
        if q:
            mask = df.astype(str).apply(lambda c: c.str.contains(q, case=False, na=False)).any(axis=1)
            df = df[mask]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("배당 CSV 다운로드", df.to_csv(index=False).encode("utf-8-sig"), "live_odds.csv")

with TABS[2]:
    st.subheader("축구 독립 모델")
    c = st.columns(4)
    home_elo = c[0].number_input("Home Elo", value=1550.0)
    away_elo = c[1].number_input("Away Elo", value=1500.0)
    hxgf = c[2].number_input("Home xG for", value=1.45)
    hxga = c[3].number_input("Home xG against", value=1.05)
    c = st.columns(4)
    axgf = c[0].number_input("Away xG for", value=1.10)
    axga = c[1].number_input("Away xG against", value=1.35)
    habs = c[2].number_input("Home absence xG penalty", value=0.0)
    aabs = c[3].number_input("Away absence xG penalty", value=0.0)
    c = st.columns(3)
    mean = c[0].number_input("Competition goals/game", value=2.55)
    market = c[1].selectbox("Market", ["home_ml", "away_ml", "draw", "under", "over", "home_ah", "away_ah"])
    line = c[2].number_input("Line", value=2.0, step=0.25)
    sf = SoccerFeatures(home_elo, away_elo, hxgf, hxga, axgf, axga, habs, aabs, competition_goal_mean=mean)
    priced = price_market(sf, market, None if market in ("home_ml", "away_ml", "draw") else line)
    st.json({k: round(v, 4) for k, v in priced.items() if k != "matrix"})

with TABS[3]:
    st.subheader("MLB 오늘 경기 + 모델")
    d = st.date_input("날짜", value=date.today(), key="mlb_date")
    if st.button("MLB 일정/예고선발 자동 불러오기"):
        try:
            st.session_state["mlb_schedule"] = mlb_schedule(str(d))
        except Exception as e:
            st.error(str(e))
    if "mlb_schedule" in st.session_state:
        st.dataframe(st.session_state["mlb_schedule"], use_container_width=True, hide_index=True)
        st.caption("MLB StatsAPI는 API 키 없이 작동합니다.")

    st.markdown("### MLB 독립 모델 계산기")
    vals = {}
    labels = [
        ("home_wrcr", "Home wRC+", 105.0), ("away_wrcr", "Away wRC+", 95.0),
        ("home_sp_xfip", "Home SP xFIP", 3.7), ("away_sp_xfip", "Away SP xFIP", 4.3),
        ("home_sp_kbb", "Home SP K-BB", 0.18), ("away_sp_kbb", "Away SP K-BB", 0.12),
        ("home_bullpen_xfip", "Home BP xFIP", 3.9), ("away_bullpen_xfip", "Away BP xFIP", 4.2),
    ]
    cols = st.columns(4)
    for i, (key, label, default) in enumerate(labels):
        vals[key] = cols[i % 4].number_input(label, value=default, key="mlb_" + key)
    mf = MLBFeatures(**vals)
    st.json({k: round(v, 4) for k, v in mlb_win_probability(mf).items()})

with TABS[4]:
    st.subheader("NFL 독립 모델")
    vals = {}
    labels = [
        ("home_epa_play", "Home EPA/play", 0.08), ("away_epa_play", "Away EPA/play", 0.02),
        ("home_success_rate", "Home success rate", 0.47), ("away_success_rate", "Away success rate", 0.43),
        ("home_def_epa_play", "Home def EPA/play", -0.02), ("away_def_epa_play", "Away def EPA/play", 0.03),
    ]
    cols = st.columns(3)
    for i, (key, label, default) in enumerate(labels):
        vals[key] = cols[i % 3].number_input(label, value=default, key="nfl_" + key)
    nf = NFLFeatures(**vals)
    st.json({k: round(v, 4) for k, v in nfl_win_probability(nf).items()})

with TABS[5]:
    st.subheader("축구 확정 라인업 / 부상")
    if not FOOTBALL_KEY:
        st.warning("API_FOOTBALL_KEY가 아직 없습니다. '설정'에서 연결하세요.")
    fixture_id = st.number_input("Fixture ID", min_value=0, value=0, step=1)
    if st.button("라인업/부상 불러오기", disabled=(not FOOTBALL_KEY or fixture_id == 0)):
        try:
            api = APIFootball(FOOTBALL_KEY)
            st.markdown("#### 라인업")
            st.json(api.lineups(int(fixture_id)))
            st.markdown("#### 부상")
            st.json(api.injuries(int(fixture_id)))
        except Exception as e:
            st.error(str(e))

with TABS[6]:
    st.subheader("독립 모델 후보 EV")
    st.write("독립 모델이 만든 model_win_prob을 넣으면 BE / Edge / EV / 불확실성 조정 EV를 계산합니다.")
    up = st.file_uploader("후보 CSV", type=["csv"])
    sample = pd.DataFrame([
        {"event_id": "oviedo-sporting", "selection": "Under 2", "odds": 1.94, "model_win_prob": 0.46, "push_prob": 0.27, "uncertainty_pp": 3, "group": "soccer"},
        {"event_id": "car-cle", "selection": "Carolina ML", "odds": 1.77, "model_win_prob": 0.60, "push_prob": 0.00, "uncertainty_pp": 4, "group": "nfl"},
    ])
    st.download_button("샘플 CSV", sample.to_csv(index=False).encode("utf-8-sig"), "candidate_sample.csv")
    if up:
        try:
            ranked = rank_bets(pd.read_csv(up))
            st.session_state["ranked"] = ranked
            st.dataframe(ranked, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"분석 실패: {e}")

with TABS[7]:
    st.subheader("2~6폴 자동조합")
    if "ranked" not in st.session_state:
        st.info("'후보 EV'에서 후보 CSV를 먼저 분석하세요.")
    else:
        sizes = st.multiselect("폴더 수", [2, 3, 4, 5, 6], default=[2, 3, 4])
        results = build_parlays(st.session_state["ranked"], sizes=sizes, top_n=10)
        for n in sizes:
            st.markdown(f"### {n}폴")
            rows = []
            for p in results[n]:
                rows.append({
                    "legs": " + ".join(x.selection for x in p.legs),
                    "odds": round(p.nominal_odds, 3),
                    "approx_hit_prob": round(p.approx_hit_prob, 4),
                    "approx_ev": round(p.approx_ev, 4),
                    "score": round(p.score, 4),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

with TABS[8]:
    st.subheader("API 연결")
    st.markdown('''
Streamlit에서 오른쪽 아래 **Manage app → Settings → Secrets**로 들어가서 아래처럼 입력하세요.

```toml
THE_ODDS_API_KEY = "여기에_The_Odds_API_키"
API_FOOTBALL_KEY = "여기에_API_Football_키"
```

저장하면 앱이 자동으로 재시작됩니다.

- **The Odds API**: 실시간 배당 자동수집
- **API-Football**: 축구 라인업/부상
- **MLB StatsAPI**: 별도 키 없이 일정/예고선발 사용
''')
    st.write({
        "THE_ODDS_API_KEY": "연결됨" if ODDS_KEY else "미연결",
        "API_FOOTBALL_KEY": "연결됨" if FOOTBALL_KEY else "미연결",
    })
