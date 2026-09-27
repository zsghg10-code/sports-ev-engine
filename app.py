
import os
import pandas as pd
import streamlit as st

from sports_ev_engine.core.ev import analyze_bet
from sports_ev_engine.core.odds import devig_power
from sports_ev_engine.models.soccer import SoccerFeatures, price_market
from sports_ev_engine.models.mlb import MLBFeatures, win_probability as mlb_win_probability
from sports_ev_engine.models.nfl import NFLFeatures, win_probability as nfl_win_probability
from sports_ev_engine.pipeline import rank_bets, build_parlays

st.set_page_config(page_title="Sports EV Engine", layout="wide")
st.title("Sports EV Engine")
st.caption("실시간 배당 + 독립 스포츠 모델 + 적특/Asian line + EV + 다폴 최적화")

tabs=st.tabs(["오늘의 후보 CSV","축구 모델","MLB 모델","NFL 모델","다폴 최적화","데이터 연결"])

with tabs[0]:
    st.subheader("후보 일괄 분석")
    st.write("event_id, selection, odds, model_win_prob 필수 / push_prob, uncertainty_pp 선택")
    f=st.file_uploader("후보 CSV",type=["csv"])
    sample=pd.DataFrame([
        {"event_id":"soc_1","selection":"Under 2","odds":1.94,"model_win_prob":0.46,"push_prob":0.27,"uncertainty_pp":3},
        {"event_id":"nfl_1","selection":"CAR ML","odds":1.77,"model_win_prob":0.60,"push_prob":0,"uncertainty_pp":4},
    ])
    st.download_button("샘플 CSV",sample.to_csv(index=False).encode("utf-8-sig"),"candidate_sample.csv")
    if f:
        df=pd.read_csv(f)
        ranked=rank_bets(df)
        st.dataframe(ranked,use_container_width=True,hide_index=True)
        st.session_state["ranked"]=ranked

with tabs[1]:
    st.subheader("축구 독립 모델")
    c=st.columns(4)
    home_elo=c[0].number_input("Home Elo",value=1550.0)
    away_elo=c[1].number_input("Away Elo",value=1500.0)
    hxgf=c[2].number_input("Home xG for",value=1.45)
    hxga=c[3].number_input("Home xG against",value=1.05)
    c=st.columns(4)
    axgf=c[0].number_input("Away xG for",value=1.10)
    axga=c[1].number_input("Away xG against",value=1.35)
    habs=c[2].number_input("Home absence xG penalty",value=0.0)
    aabs=c[3].number_input("Away absence xG penalty",value=0.0)
    c=st.columns(3)
    mean=c[0].number_input("Competition goals/game",value=2.55)
    market=c[1].selectbox("Market",["home_ml","away_ml","draw","under","over","home_ah","away_ah"])
    line=c[2].number_input("Line",value=2.0,step=0.25)
    sf=SoccerFeatures(home_elo,away_elo,hxgf,hxga,axgf,axga,habs,aabs,competition_goal_mean=mean)
    priced=price_market(sf,market,None if market in ("home_ml","away_ml","draw") else line)
    st.json({k:round(v,4) for k,v in priced.items() if k!="matrix"})

with tabs[2]:
    st.subheader("MLB 독립 모델")
    vals={}
    labels=[("home_wrcr","Home wRC+",105.0),("away_wrcr","Away wRC+",95.0),
            ("home_sp_xfip","Home SP xFIP",3.7),("away_sp_xfip","Away SP xFIP",4.3),
            ("home_sp_kbb","Home SP K-BB",0.18),("away_sp_kbb","Away SP K-BB",0.12),
            ("home_bullpen_xfip","Home BP xFIP",3.9),("away_bullpen_xfip","Away BP xFIP",4.2)]
    cols=st.columns(4)
    for i,(key,label,default) in enumerate(labels):
        vals[key]=cols[i%4].number_input(label,value=default,key="mlb_"+key)
    mf=MLBFeatures(**vals)
    st.json({k:round(v,4) for k,v in mlb_win_probability(mf).items()})

with tabs[3]:
    st.subheader("NFL 독립 모델")
    vals={}
    labels=[("home_epa_play","Home EPA/play",0.08),("away_epa_play","Away EPA/play",0.02),
            ("home_success_rate","Home success rate",0.47),("away_success_rate","Away success rate",0.43),
            ("home_def_epa_play","Home def EPA/play",-0.02),("away_def_epa_play","Away def EPA/play",0.03)]
    cols=st.columns(3)
    for i,(key,label,default) in enumerate(labels):
        vals[key]=cols[i%3].number_input(label,value=default,key="nfl_"+key)
    nf=NFLFeatures(**vals)
    st.json({k:round(v,4) for k,v in nfl_win_probability(nf).items()})

with tabs[4]:
    st.subheader("다폴 최적화")
    if "ranked" not in st.session_state:
        st.info("먼저 '오늘의 후보 CSV'에서 후보를 업로드하세요.")
    else:
        ranked=st.session_state["ranked"]
        sizes=st.multiselect("폴더 수",[2,3,4,5,6],default=[2,3,4])
        res=build_parlays(ranked,sizes=sizes,top_n=10)
        for n in sizes:
            st.markdown(f"### {n}폴")
            rows=[]
            for p in res[n]:
                rows.append({
                    "legs":" + ".join(x.selection for x in p.legs),
                    "odds":p.nominal_odds,
                    "approx_hit_prob":p.approx_hit_prob,
                    "approx_ev":p.approx_ev,
                    "score":p.score,
                })
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

with tabs[5]:
    st.subheader("자동 데이터 연결")
    st.markdown("""
    - **실시간 배당:** The Odds API (`THE_ODDS_API_KEY`)
    - **축구 일정/라인업/부상:** API-Football (`API_FOOTBALL_KEY`)
    - **MLB 일정/예고선발/라인업:** MLB StatsAPI (키 불필요)
    - **날씨:** Open-Meteo (키 불필요)
    - **NFL 고급 지표:** nflverse CSV 또는 별도 데이터 공급원 연결용 어댑터 제공

    `.env.example`에 키를 넣고 provider 모듈을 호출하면 됩니다.
    현재 UI는 모델 검증을 위해 입력값을 노출해 두었고, `run_daily.py`가 자동 파이프라인 예시입니다.
    """)
