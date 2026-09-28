from datetime import date
import pandas as pd

from sports_ev_engine.providers.mlb_statsapi import _filter_kst_rows, match_schedule
from sports_ev_engine.official_baseball_model import analyze_official_event


def test_mlb_kst_day_filter_crosses_utc_date():
    rows=[
        {"gamePk":1,"gameDate":"2026-09-28T23:10:00Z","away":"A","home":"B"}, # 9/29 08:10 KST
        {"gamePk":2,"gameDate":"2026-09-29T18:00:00Z","away":"C","home":"D"}, # 9/30 03:00 KST
    ]
    out=_filter_kst_rows(rows,date(2026,9,29))
    assert out["gamePk"].tolist()==[1]
    assert out.iloc[0]["gameDateKST"].startswith("2026-09-29 08:10")


def test_mlb_schedule_match_normalizes_names():
    frame=pd.DataFrame([{"gamePk":7,"gameDate":"2026-09-28T23:10:00Z","home":"New York Yankees","away":"Boston Red Sox"}])
    r=match_schedule(frame,"New York Yankees","Boston Red Sox","2026-09-28T23:10:00Z")
    assert r["gamePk"]==7


def test_mlb_model_supports_side_total_and_spread():
    market=pd.DataFrame([
        {"event_id":"e1","home_team":"New York Yankees","away_team":"Boston Red Sox","commence_time":"2026-09-28T23:10:00Z","market":"h2h","market_id":"h2h","selection":"New York Yankees","point":None,"consensus_prob":.55,"best_odds":1.90,"best_book":"x","books":4},
        {"event_id":"e1","home_team":"New York Yankees","away_team":"Boston Red Sox","commence_time":"2026-09-28T23:10:00Z","market":"h2h","market_id":"h2h","selection":"Boston Red Sox","point":None,"consensus_prob":.45,"best_odds":2.15,"best_book":"x","books":4},
        {"event_id":"e1","home_team":"New York Yankees","away_team":"Boston Red Sox","commence_time":"2026-09-28T23:10:00Z","market":"totals","market_id":"totals|8.5","selection":"Over","point":8.5,"consensus_prob":.51,"best_odds":1.95,"best_book":"x","books":4},
        {"event_id":"e1","home_team":"New York Yankees","away_team":"Boston Red Sox","commence_time":"2026-09-28T23:10:00Z","market":"totals","market_id":"totals|8.5","selection":"Under","point":8.5,"consensus_prob":.49,"best_odds":2.00,"best_book":"x","books":4},
        {"event_id":"e1","home_team":"New York Yankees","away_team":"Boston Red Sox","commence_time":"2026-09-28T23:10:00Z","market":"spreads","market_id":"spreads|-1.5","selection":"New York Yankees","point":-1.5,"consensus_prob":.46,"best_odds":2.15,"best_book":"x","books":4},
    ])
    stats={
        "New York Yankees":{"runs_per_game":4.9,"runs_allowed_per_game":4.1,"win_pct":.58,"recent10_win_pct":.6,"era":3.9,"games":10,"source":"MLB Stats API"},
        "Boston Red Sox":{"runs_per_game":4.5,"runs_allowed_per_game":4.4,"win_pct":.53,"recent10_win_pct":.5,"era":4.2,"games":10,"source":"MLB Stats API"},
    }
    ctx={
        "league":"MLB","stage":"FINAL","starter_confirmed":True,"lineup_confirmed":True,"source":"MLB Stats API",
        "home_starter_stats":{"era":3.4,"whip":1.15,"kbb":4.0},"away_starter_stats":{"era":4.1,"whip":1.3,"kbb":2.8},
        "advanced":{"advanced_completeness":.8,"extra_uncertainty_pp":.5,"statuses":{"recent_form":True,"starter_recent":True,"bullpen":True,"split":True,"velocity":False,"weather":True},"components":{}},
    }
    out,meta=analyze_official_event(market,stats,"MLB",ctx)
    assert not out.empty
    assert set(out["market"])=={"h2h","totals","spreads"}
    assert (out["league"]=="MLB").all()
    assert "v3_decision_status" in out
    assert meta["stage"]=="FINAL"
