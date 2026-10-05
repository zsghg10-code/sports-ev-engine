"""Upload-only Streamlit hook for Sports EV Engine v3.5 NHL/NFL tabs.

No local patch script is required. Importing sports_ev_engine installs this hook.
When the existing v3.4.25 app creates its main st.tabs(), the hook appends NHL/NFL
tabs and renders the v3 analysis panels inside them.
"""
from __future__ import annotations
import inspect

_INSTALLED = False


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

    def patched_set_page_config(*args, **kwargs):
        if kwargs.get("page_title") == "Sports EV Engine v3.4.24":
            kwargs["page_title"] = "Sports EV Engine v3.5.0"
        return original_set_page_config(*args, **kwargs)

    def patched_title(body, *args, **kwargs):
        if str(body) == "Sports EV Engine v3.4.24":
            body = "Sports EV Engine v3.5.0"
        return original_title(body, *args, **kwargs)

    def patched_caption(body, *args, **kwargs):
        s = str(body)
        if s == "BUILD v3.4.24-national-validation · 2026-10-02":
            body = "BUILD v3.5.0-nhl-nfl · upload-only · based on v3.4.25 KBO safety"
        elif "지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB." in s:
            body = s.replace(
                "지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB.",
                "지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB · NHL · NFL."
            )
        return original_caption(body, *args, **kwargs)

    def patched_tabs(labels, *args, **kwargs):
        names = list(labels)
        is_main = (
            "⚡ 완전자동 축구" in names
            and "MLB" in names
            and "🛡️ 데이터·리스크" in names
        )
        if not is_main or "🏒 NHL 자동분석" in names:
            return original_tabs(labels, *args, **kwargs)

        tabs = original_tabs(
            names + ["🏒 NHL 자동분석", "🏈 NFL 자동분석"],
            *args, **kwargs
        )

        # At this point the existing app has already defined these globals.
        caller = inspect.currentframe().f_back
        g = caller.f_globals if caller is not None else {}
        odds_key = g.get("ODDS_KEY")
        render_calendar = g.get("render_kst_calendar")
        render_final = g.get("render_final_decision_layer")

        try:
            from .pro_sports_ui import render_pro_sport_analysis
            from . import daily_combo
            daily_combo.SPORT_LABELS.update({
                "hockey_nhl": "NHL",
                "football_nfl": "NFL",
            })

            if callable(render_calendar) and callable(render_final):
                with tabs[-2]:
                    render_pro_sport_analysis(
                        sport_key="icehockey_nhl",
                        prefix="nhl",
                        title="🏒 NHL 자동분석",
                        odds_key=odds_key,
                        render_kst_calendar=render_calendar,
                        render_final_decision_layer=render_final,
                    )
                with tabs[-1]:
                    render_pro_sport_analysis(
                        sport_key="americanfootball_nfl",
                        prefix="nfl",
                        title="🏈 NFL 자동분석",
                        odds_key=odds_key,
                        render_kst_calendar=render_calendar,
                        render_final_decision_layer=render_final,
                    )
            else:
                with tabs[-2]:
                    st.error("NHL 탭을 연결할 앱 헬퍼를 찾지 못했습니다. v3.4.25 main 기준인지 확인하세요.")
                with tabs[-1]:
                    st.error("NFL 탭을 연결할 앱 헬퍼를 찾지 못했습니다. v3.4.25 main 기준인지 확인하세요.")
        except Exception as exc:
            with tabs[-2]:
                st.error(f"NHL/NFL v3.5 로드 실패: {type(exc).__name__}: {exc}")

        return tabs

    st.set_page_config = patched_set_page_config
    st.title = patched_title
    st.caption = patched_caption
    st.tabs = patched_tabs
