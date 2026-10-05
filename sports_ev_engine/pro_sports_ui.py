"""Streamlit UI helper for NHL/NFL v3.5.0 analysis tabs."""
from __future__ import annotations
import pandas as pd
import streamlit as st

from .pro_sports import SPORTS, analyze_pro_board
from .prediction_store import record_frame, auto_settle
from .core.parlay import optimize_parlays


def render_pro_sport_analysis(
    *,
    sport_key,
    prefix,
    title,
    odds_key,
    render_kst_calendar,
    render_final_decision_layer,
):
    cfg = SPORTS[sport_key]
    st.subheader(title)
    st.caption(
        "독립 모델 → 실제 최근 경기/휴식·가용성 컨텍스트 → 반증/실패경로 → "
        "시장 캘리브레이션 → 스트레스 시나리오 → EV/ROBUST → 기록·사후정산"
    )
    st.info(
        "QB/선발 골리/부상 정보가 공개 피드에서 검증되지 않으면 임의로 채우지 않고 "
        "MISSING으로 남기며 불확실성·다폴 게이트에 페널티를 줍니다."
    )
    if not odds_key:
        st.warning("THE_ODDS_API_KEY가 필요합니다.")
        return

    c1, c2, c3 = st.columns(3)
    region = c1.selectbox("배당 지역", ["us", "eu", "uk", "au"], index=0, key=f"{prefix}_region")
    min_books = c2.slider("컨센서스 최소 북메이커 수", 2, 6, 3, key=f"{prefix}_min_books")
    recent_options = [4, 5, 6, 8, 10] if cfg["label"] == "NFL" else [5, 6, 8, 10, 12]
    default_n = cfg["recent_n"]
    default_i = recent_options.index(default_n) if default_n in recent_options else 2
    recent_n = c3.selectbox("최근 경기 반영", recent_options, index=default_i, key=f"{prefix}_recent_n")

    date_only, selected_date, _ = render_kst_calendar(prefix)
    markets = st.multiselect(
        "분석 마켓", ["h2h", "spreads", "totals"],
        default=["h2h", "spreads", "totals"], key=f"{prefix}_markets"
    )
    run = st.button(
        f"🚀 {cfg['label']} 전체 자동분석",
        type="primary", key=f"{prefix}_run", disabled=not markets
    )

    if run:
        from .providers.the_odds_api import TheOddsAPI
        api = TheOddsAPI(odds_key)
        with st.status(f"{cfg['label']} 전체 경기 수집·분석 중...", expanded=False) as status:
            ranked, status_rows, raw, market, headers = analyze_pro_board(
                api, sport_key, region=region, markets=tuple(markets),
                min_books=min_books, selected_date=selected_date if date_only else None,
                recent_n=recent_n,
            )
            saved = 0
            settle = {"settled": 0, "error": ""}
            if isinstance(ranked, pd.DataFrame) and not ranked.empty:
                saved = record_frame(
                    ranked, sport_key=sport_key, sport_family=cfg["family"]
                )
                settle = auto_settle(api, sport_key)
            st.session_state[f"{prefix}_ranked"] = ranked
            st.session_state[f"{prefix}_status"] = status_rows
            st.session_state[f"{prefix}_raw_odds"] = raw
            st.session_state[f"{prefix}_market"] = market
            status.update(
                label=f"완료 · 예측 스냅샷 {saved}개 · 자동정산 {settle.get('settled',0)}개",
                state="complete",
            )

    status_rows = st.session_state.get(f"{prefix}_status")
    if status_rows:
        st.markdown("### 경기별 수집·분석 상태")
        st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)

    ranked = st.session_state.get(f"{prefix}_ranked")
    if not isinstance(ranked, pd.DataFrame):
        return
    if ranked.empty:
        if f"{prefix}_ranked" in st.session_state:
            st.info("선택 날짜에 분석 가능한 시장이 없습니다.")
        return

    render_final_decision_layer(
        ranked, prefix,
        title=f"🧠 v3 FINAL Decision Layer · {cfg['label']}",
        include_spread=True,
    )

    st.markdown(f"### 상세 진단 · {cfg['label']} 전체 옵션")
    cols = [c for c in [
        "v3_decision_status","v3_candidate","v3_parlay_eligible",
        "robust_positive_ratio","robust_ev_p10","robust_ev_min",
        "counter_case_risk","signal_coverage","stage","data_quality",
        "commence_time","home_team","away_team","market","selection","point",
        "best_book","best_odds","books","consensus_prob","raw_independent_prob",
        "model_win_prob","push_prob","break_even","edge_pp","ev_roi",
        "conservative_ev_roi","kelly_scaled","uncertainty_pp","sanity",
        "home_form_matches","away_form_matches",
        "home_recent_points_for","home_recent_points_against",
        "away_recent_points_for","away_recent_points_against",
        "home_recent_margin","away_recent_margin","home_rest_days","away_rest_days",
        "home_expected_points","away_expected_points",
        "home_expected_goals","away_expected_goals",
        "missing_signals","signal_summary","counter_case_summary",
        "adaptive_gate","calibration_n","calibration_active","calibration_reliability",
    ] if c in ranked.columns]
    shown = ranked[cols].copy()
    for c in ["consensus_prob","raw_independent_prob","model_win_prob","push_prob",
              "break_even","robust_positive_ratio","signal_coverage"]:
        if c in shown:
            shown[c] = (pd.to_numeric(shown[c], errors="coerce") * 100).round(1)
    for c in ["robust_ev_p10","robust_ev_min","ev_roi","conservative_ev_roi"]:
        if c in shown:
            shown[c] = (pd.to_numeric(shown[c], errors="coerce") * 100).round(1)
    st.dataframe(shown, use_container_width=True, hide_index=True)

    st.markdown(f"### {cfg['label']} 2~6폴")
    sizes = st.multiselect(
        f"{cfg['label']} 폴더 수", [2,3,4,5,6], default=[2,3],
        key=f"{prefix}_parlay_sizes"
    )
    pool = ranked.copy()
    if "v3_parlay_eligible" in pool:
        pool = pool[pool["v3_parlay_eligible"].fillna(False).astype(bool)].copy()
    results = optimize_parlays(pool, sizes=sizes, top_n=10) if not pool.empty else {n:[] for n in sizes}
    for n in sizes:
        st.markdown(f"#### {n}폴 TOP")
        f = pd.DataFrame(results.get(n, []))
        if not f.empty:
            if "배당" in f: f["배당"] = pd.to_numeric(f["배당"], errors="coerce").round(2)
            if "근사 적중확률" in f: f["근사 적중확률"] = (pd.to_numeric(f["근사 적중확률"], errors="coerce")*100).round(1)
            if "근사 EV" in f: f["근사 EV"] = (pd.to_numeric(f["근사 EV"], errors="coerce")*100).round(1)
        st.dataframe(f, use_container_width=True, hide_index=True)
