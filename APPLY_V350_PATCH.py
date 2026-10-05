from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def replace_once(text, old, new, label):
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"{label}: 기준 문자열을 찾지 못했습니다. v3.4.25 main에 적용하는지 확인하세요.")
    return text.replace(old, new, 1)

def patch_app(path: Path):
    if not path.exists():
        return
    t = path.read_text(encoding="utf-8")
    if "from sports_ev_engine.pro_sports import PRO_SPORTS_BUILD" not in t:
        anchor = "from sports_ev_engine.bankroll import simulate as simulate_bankroll\n"
        t = replace_once(
            t, anchor,
            anchor + "from sports_ev_engine.pro_sports import PRO_SPORTS_BUILD\n"
                     "from sports_ev_engine.pro_sports_ui import render_pro_sport_analysis\n",
            f"{path}: imports"
        )

    t = t.replace('st.set_page_config(page_title="Sports EV Engine v3.4.24",layout="wide")',
                  'st.set_page_config(page_title="Sports EV Engine v3.5.0",layout="wide")')
    t = t.replace('st.title("Sports EV Engine v3.4.24")',
                  'st.title("Sports EV Engine v3.5.0")')
    t = t.replace('st.caption("BUILD v3.4.24-national-validation · 2026-10-02")',
                  'st.caption("BUILD v3.5.0-nhl-nfl · 2026-10-05")')
    t = t.replace('_BUILD_ID = "3.4.24-national-validation"',
                  '_BUILD_ID = "3.5.0-nhl-nfl"')

    if 'PRO_SPORTS_BUILD != "3.5.0-nhl-nfl"' not in t:
        marker = 'FootballAccessError=football_provider.FootballAccessError\n'
        check = (
            marker +
            'if PRO_SPORTS_BUILD != "3.5.0-nhl-nfl":\n'
            '    st.error("NHL/NFL 분석 모듈 버전이 맞지 않습니다. v3.5.0 UPDATE_ONLY 파일을 모두 반영한 뒤 Reboot하세요.")\n'
            '    st.stop()\n'
        )
        t = replace_once(t, marker, check, f"{path}: build check")

    old_tabs = 'tabs=st.tabs(["⚡ 완전자동 축구","🌍 축구 A매치","⚾ KBO/NPB 자동분석","실시간 배당","시장 가격","MLB","🏆 오늘의 베스트 조합","다폴","📡 모니터링","설정","📊 모델 검증","🧪 모델 연구소","🛡️ 데이터·리스크"])'
    new_tabs = 'tabs=st.tabs(["⚡ 완전자동 축구","🌍 축구 A매치","⚾ KBO/NPB 자동분석","실시간 배당","시장 가격","MLB","🏆 오늘의 베스트 조합","다폴","📡 모니터링","설정","📊 모델 검증","🧪 모델 연구소","🛡️ 데이터·리스크","🏒 NHL 자동분석","🏈 NFL 자동분석"])'
    if old_tabs in t:
        t = t.replace(old_tabs, new_tabs, 1)

    t = t.replace("🏆 오늘의 베스트 조합 · 축구 + 야구 통합", "🏆 오늘의 베스트 조합 · 전 종목 통합")
    t = t.replace("지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB.",
                  "지원 범위: 클럽축구 · A매치 · KBO · NPB · MLB · NHL · NFL.")
    t = t.replace('expected=["클럽축구","A매치","KBO","NPB","MLB"]',
                  'expected=["클럽축구","A매치","KBO","NPB","MLB","NHL","NFL"]')

    if '"nhl_ranked"' not in t:
        target = '"club_filter_label","national_filter_label","baseball_filter_label"'
        repl = target[:-1] + ',"nhl_ranked","nhl_status","nhl_raw_odds","nhl_market","nfl_ranked","nfl_status","nfl_raw_odds","nfl_market"'
        if target in t:
            t = t.replace(target, repl, 1)

    if "with tabs[13]:" not in t:
        t += '''

with tabs[13]:
    render_pro_sport_analysis(
        sport_key="icehockey_nhl", prefix="nhl", title="🏒 NHL 자동분석",
        odds_key=ODDS_KEY, render_kst_calendar=render_kst_calendar,
        render_final_decision_layer=render_final_decision_layer,
    )

with tabs[14]:
    render_pro_sport_analysis(
        sport_key="americanfootball_nfl", prefix="nfl", title="🏈 NFL 자동분석",
        odds_key=ODDS_KEY, render_kst_calendar=render_kst_calendar,
        render_final_decision_layer=render_final_decision_layer,
    )
'''
    path.write_text(t, encoding="utf-8")

def patch_daily_combo(path: Path):
    t=path.read_text(encoding="utf-8")
    if '"football_nfl": "NFL"' not in t:
        old='"baseball_mlb": "MLB",\n}'
        new='"baseball_mlb": "MLB",\n    "hockey_nhl": "NHL",\n    "football_nfl": "NFL",\n}'
        t=replace_once(t,old,new,f"{path}: labels")
    if 'if skey == "icehockey_nhl"' not in t:
        old='    if skey == "baseball_npb":\n        return "NPB"\n    if skey.startswith("soccer_"):'
        new='    if skey == "baseball_npb":\n        return "NPB"\n    if skey == "icehockey_nhl":\n        return "NHL"\n    if skey == "americanfootball_nfl":\n        return "NFL"\n    if skey.startswith("soccer_"):'
        t=replace_once(t,old,new,f"{path}: sport label")
    if 'stage == "CONTEXT VERIFIED"' not in t:
        old='    if stage == "FINAL" or (quality == "HIGH" and lineup):\n        return 1.00, "확정", "FINAL"'
        new='    if stage == "FINAL" or (quality == "HIGH" and lineup):\n        return 1.00, "확정", "FINAL"\n    if stage == "CONTEXT VERIFIED":\n        return 0.96, "컨텍스트 확인", stage\n    if stage == "CONTEXT PARTIAL":\n        return 0.88, "미확정", stage'
        t=replace_once(t,old,new,f"{path}: stage")
    path.write_text(t,encoding="utf-8")

def patch_store(path: Path):
    t=path.read_text(encoding="utf-8")
    t=t.replace('MODEL_VERSION = "3.4.24-national-validation"',
                'MODEL_VERSION = "3.5.0-nhl-nfl"')
    marker='"home_recent_gf", "home_recent_ga", "away_recent_gf", "away_recent_ga", "home_elo", "away_elo",'
    if marker in t and '"home_recent_points_for"' not in t:
        repl=marker + '\n        "home_form_matches", "away_form_matches", "home_recent_points_for", "home_recent_points_against",\n        "away_recent_points_for", "away_recent_points_against", "home_recent_margin", "away_recent_margin",\n        "home_expected_points", "away_expected_points", "home_expected_goals", "away_expected_goals",'
        t=t.replace(marker,repl,1)
    path.write_text(t,encoding="utf-8")

def main():
    required=[
        ROOT/"app.py",
        ROOT/"sports_ev_engine"/"daily_combo.py",
        ROOT/"sports_ev_engine"/"prediction_store.py",
        ROOT/"sports_ev_engine"/"__init__.py",
    ]
    missing=[str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit("저장소 루트에서 실행하세요. 누락: "+", ".join(missing))

    patch_app(ROOT/"app.py")
    patch_app(ROOT/"sports_ev_engine"/"app.py")
    patch_daily_combo(ROOT/"sports_ev_engine"/"daily_combo.py")
    patch_store(ROOT/"sports_ev_engine"/"prediction_store.py")

    initp=ROOT/"sports_ev_engine"/"__init__.py"
    t=initp.read_text(encoding="utf-8")
    t=t.replace('__version__ = "3.4.25-kbo-safety"',
                '__version__ = "3.5.0-nhl-nfl"')
    initp.write_text(t,encoding="utf-8")

    for p in [ROOT/"VERSION.txt", ROOT/"sports_ev_engine"/"VERSION.txt"]:
        if p.exists():
            p.write_text("3.5.0-nhl-nfl\n",encoding="utf-8")

    print("Sports EV Engine v3.5.0 NHL/NFL patch applied.")
    print("Next: python -m pytest -q tests/test_v350_nhl_nfl.py")

if __name__=="__main__":
    main()
