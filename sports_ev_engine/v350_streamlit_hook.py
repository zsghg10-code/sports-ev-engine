"""v3.6 upload-only Streamlit hook.

Visual order:
자동축구 → A매치 → KBO/NPB → MLB → NHL → NFL → utility tabs.

Also:
- automatically runs due settlement/CLV/review once per session/cooldown
- shows self-learning state in Model Validation
- converts the old MLB-only review wording to all-sport wording
- keeps the old manual button only as an optional immediate rerun
"""
from __future__ import annotations
import inspect
import pandas as pd

_INSTALLED = False
PRIMARY_ORDER = [
    "⚡ 완전자동 축구",
    "🌍 축구 A매치",
    "⚾ KBO/NPB 자동분석",
    "MLB",
    "🏒 NHL 자동분석",
    "🏈 NFL 자동분석",
]


def _visual_order(original_names):
    names = list(original_names)
    added = ["🏒 NHL 자동분석", "🏈 NFL 자동분석"]
    full = names + [x for x in added if x not in names]
    head = [x for x in PRIMARY_ORDER if x in full]
    tail = [x for x in full if x not in head]
    return head + tail


def _rewrite_text(body):
    s = str(body)
    replacements = [
        ("Sports EV Engine v3.4.24", "Sports EV Engine v3.6.0"),
        ("BUILD v3.4.24-national-validation · 2026-10-02",
         "BUILD v3.6.0-autolearn · all-sport review + CLV + online calibration"),
        ("지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB.",
         "지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB · NHL · NFL."),
        ("### ⚾ MLB 자동 사후복기", "### 🔁 전종목 자동 사후복기"),
        ("MLB 자동복기", "전종목 자동복기"),
        ("MLB 자동 사후복기", "전종목 자동 사후복기"),
        ("MLB postgame reviews", "all-sport postgame reviews"),
        ("아직 MLB 자동복기 표본이 없습니다.", "아직 전종목 자동복기 표본이 없습니다."),
        ("MLB 예측이 정산되면 실제 이닝별 득점·선발/불펜 박스스코어를 확인해 승패와 언더오버 실패 경로를 자동 분류합니다.",
         "정산된 모든 종목을 자동복기합니다. MLB는 이닝·선발·불펜까지 정밀복기하고, 축구/KBO/NPB/NHL/NFL은 최종점수·라인·CLV를 함께 사용해 접전/방향성 실패/좋은 가격 여부를 보수적으로 분류합니다."),
    ]
    for a, b in replacements:
        s = s.replace(a, b)
    return s


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    import streamlit as st

    original_tabs = st.tabs
    original_title = st.title
    original_set_page_config = st.set_page_config
    original_caption = st.caption
    original_markdown = st.markdown
    original_info = st.info
    original_success = st.success
    original_button = st.button

    def patched_set_page_config(*args, **kwargs):
        if kwargs.get("page_title") == "Sports EV Engine v3.4.24":
            kwargs["page_title"] = "Sports EV Engine v3.6.0"
        return original_set_page_config(*args, **kwargs)

    def patched_title(body, *args, **kwargs):
        return original_title(_rewrite_text(body), *args, **kwargs)

    def patched_caption(body, *args, **kwargs):
        return original_caption(_rewrite_text(body), *args, **kwargs)

    def patched_markdown(body, *args, **kwargs):
        return original_markdown(_rewrite_text(body), *args, **kwargs)

    def patched_info(body, *args, **kwargs):
        return original_info(_rewrite_text(body), *args, **kwargs)

    def patched_success(body, *args, **kwargs):
        return original_success(_rewrite_text(body), *args, **kwargs)

    def patched_button(label, *args, **kwargs):
        if str(label) == "최근 종료 경기 지금 자동 정산 + MLB 자동복기":
            label = "🔄 즉시 재정산·전종목 복기 다시 실행 (선택)"
            kwargs.setdefault("help", "자동 처리가 기본이며, 이 버튼은 즉시 한 번 더 확인하고 싶을 때만 사용합니다.")
        return original_button(label, *args, **kwargs)

    def patched_tabs(labels, *args, **kwargs):
        original_names = list(labels)
        is_main = (
            "⚡ 완전자동 축구" in original_names
            and "🌍 축구 A매치" in original_names
            and "⚾ KBO/NPB 자동분석" in original_names
            and "MLB" in original_names
            and "🛡️ 데이터·리스크" in original_names
        )
        if not is_main:
            return original_tabs(labels, *args, **kwargs)

        visual_names = _visual_order(original_names)
        visual_tabs = original_tabs(visual_names, *args, **kwargs)
        by_name = {name: tab for name, tab in zip(visual_names, visual_tabs)}
        logical_tabs = [by_name[name] for name in original_names]

        caller = inspect.currentframe().f_back
        g = caller.f_globals if caller is not None else {}
        odds_key = g.get("ODDS_KEY")
        render_calendar = g.get("render_kst_calendar")
        render_final = g.get("render_final_decision_layer")

        auto_result = {"status": "NOT_RUN"}
        try:
            from .automation_tick import maybe_streamlit_cycle
            auto_result = maybe_streamlit_cycle(st, odds_key, cooldown_minutes=20)
        except Exception as exc:
            auto_result = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

        try:
            from .pro_sports_ui import render_pro_sport_analysis
            from . import daily_combo
            daily_combo.SPORT_LABELS.update({
                "hockey_nhl": "NHL",
                "football_nfl": "NFL",
            })
            if callable(render_calendar) and callable(render_final):
                with by_name["🏒 NHL 자동분석"]:
                    render_pro_sport_analysis(
                        sport_key="icehockey_nhl", prefix="nhl", title="🏒 NHL 자동분석",
                        odds_key=odds_key, render_kst_calendar=render_calendar,
                        render_final_decision_layer=render_final,
                    )
                with by_name["🏈 NFL 자동분석"]:
                    render_pro_sport_analysis(
                        sport_key="americanfootball_nfl", prefix="nfl", title="🏈 NFL 자동분석",
                        odds_key=odds_key, render_kst_calendar=render_calendar,
                        render_final_decision_layer=render_final,
                    )
        except Exception as exc:
            with by_name["🏒 NHL 자동분석"]:
                st.error(f"NHL/NFL v3.6 로드 실패: {type(exc).__name__}: {exc}")

        # Add the autonomous status/learning card before the legacy validation UI.
        validation = by_name.get("📊 모델 검증")
        if validation is not None:
            with validation:
                st.markdown("### 🤖 자동 정산 · CLV · 전종목 복기 · 온라인 학습")
                status = str(auto_result.get("status") or "")
                if status == "OK":
                    clv = auto_result.get("clv") or {}
                    settle = auto_result.get("settlement") or {}
                    review = auto_result.get("review") or {}
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("자동 CLV 관측", int(clv.get("observations") or 0))
                    c2.metric("자동 정산", int(settle.get("settled") or 0))
                    c3.metric("전종목 신규 복기", int(review.get("reviewed") or 0))
                    active = sum(1 for x in (auto_result.get("learning") or []) if x.get("활성"))
                    c4.metric("활성 학습 마켓", active)
                    errs = (clv.get("errors") or []) + (settle.get("errors") or []) + (review.get("errors") or [])
                    if errs:
                        st.caption("일부 자동작업 보류: " + " / ".join(str(x) for x in errs[:5]))
                elif status == "NO_ODDS_KEY":
                    st.info("THE_ODDS_API_KEY가 없어 자동 정산/CLV를 실행하지 못했습니다.")
                elif status == "ERROR":
                    st.warning("자동 사이클 오류: " + str(auto_result.get("error") or "unknown"))
                else:
                    st.caption("자동 사이클은 세션당 최대 20분 간격으로 실행됩니다.")

                try:
                    from .self_learning import learning_summary
                    lr = pd.DataFrame(learning_summary())
                    if lr.empty:
                        st.info("아직 자동학습용 정산 표본이 없습니다.")
                    else:
                        st.markdown("#### 온라인 학습 상태")
                        st.dataframe(lr, use_container_width=True, hide_index=True)
                        st.caption(
                            "표본 40개 이상인 종목×마켓부터 활성화됩니다. "
                            "Brier·calibration bias·CLV로 최종 확률을 최대 ±1.5%p만 교정하며 "
                            "코드를 스스로 바꾸지는 않습니다."
                        )
                except Exception as exc:
                    st.caption(f"학습 상태 계산 보류: {type(exc).__name__}: {exc}")

        return logical_tabs

    st.set_page_config = patched_set_page_config
    st.title = patched_title
    st.caption = patched_caption
    st.markdown = patched_markdown
    st.info = patched_info
    st.success = patched_success
    st.button = patched_button
    st.tabs = patched_tabs
