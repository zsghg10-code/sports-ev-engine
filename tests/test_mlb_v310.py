from pathlib import Path
import pandas as pd

from sports_ev_engine.providers.mlb_deep import (
    statcast_raw_batted_ball, pitch_discipline_from_pbp, lineup_hash,
    MLBDeepContext,
)
from sports_ev_engine.official_baseball_model import analyze_official_event


class FakeBase:
    def __init__(self, payloads=None):
        self.payloads=payloads or {}
        import requests
        self.s=requests.Session()
    def _get(self,path,params=None):
        key=(path, tuple(sorted((params or {}).items())))
        return self.payloads.get(key, self.payloads.get(path, {}))


def test_statcast_raw_batted_ball_metrics():
    df=pd.DataFrame([
        {"game_pk":1,"at_bat_number":1,"events":"single","launch_speed":101,"launch_speed_angle":6,"bb_type":"line_drive"},
        {"game_pk":1,"at_bat_number":2,"events":"home_run","launch_speed":105,"launch_speed_angle":6,"bb_type":"fly_ball"},
        {"game_pk":1,"at_bat_number":3,"events":"field_out","launch_speed":88,"launch_speed_angle":3,"bb_type":"ground_ball"},
        {"game_pk":1,"at_bat_number":4,"events":"strikeout","launch_speed":None,"launch_speed_angle":None,"bb_type":None},
    ])
    out=statcast_raw_batted_ball(df)
    assert out["available"]
    assert out["bbe"]==3
    assert round(out["hardhit_pct"],3)==round(2/3,3)
    assert round(out["barrel_pct"],3)==round(2/3,3)
    assert out["home_runs"]==1
    assert out["hr_fb"]==1.0


def test_pitch_discipline_whiff_chase_zone_contact():
    pbp={"allPlays":[{"matchup":{"pitcher":{"id":99}},"playEvents":[
        {"isPitch":True,"details":{"code":"S","type":{"code":"FF"}},"pitchData":{"zone":12,"startSpeed":96}},
        {"isPitch":True,"details":{"code":"F","type":{"code":"FF"}},"pitchData":{"zone":5,"startSpeed":97}},
        {"isPitch":True,"details":{"code":"X","type":{"code":"SL"}},"pitchData":{"zone":7,"startSpeed":87}},
        {"isPitch":True,"details":{"code":"B","type":{"code":"SL"}},"pitchData":{"zone":13,"startSpeed":86}},
    ]}]}
    base=FakeBase({"/game/1/playByPlay":pbp})
    out=pitch_discipline_from_pbp(base,[1],99)
    assert out["available"]
    assert out["pitches"]==4
    assert round(out["whiff_pct"],3)==round(1/3,3)
    assert round(out["contact_pct"],3)==round(2/3,3)
    assert round(out["zone_pct"],3)==0.5
    assert round(out["chase_pct"],3)==0.5
    assert len(out["pitch_mix_recent"])==2


def test_lineup_hash_changes_with_order():
    a=[{"player_id":1,"order":1},{"player_id":2,"order":2}]
    b=[{"player_id":2,"order":1},{"player_id":1,"order":2}]
    assert lineup_hash(a)!=lineup_hash(b)


def test_market_movement_tracks_observed_snapshots(tmp_path):
    base=FakeBase()
    deep=MLBDeepContext(base,data_dir=tmp_path)
    f1=pd.DataFrame([{"market":"h2h","selection":"A","point":None,"consensus_prob":.55,"best_odds":1.9}])
    first=deep.market_movement("e1",f1)
    assert not first["available"]
    f2=pd.DataFrame([{"market":"h2h","selection":"A","point":None,"consensus_prob":.58,"best_odds":1.8}])
    second=deep.market_movement("e1",f2)
    assert second["available"]
    rec=second["by_key"]["h2h|A|"]
    assert round(rec["move_pp"],2)==3.0


def test_lineup_change_watch(tmp_path):
    base=FakeBase()
    deep=MLBDeepContext(base,data_dir=tmp_path)
    one={"confirmed":True,"home":[{"player_id":1,"order":1}],"away":[{"player_id":2,"order":1}]}
    two={"confirmed":True,"home":[{"player_id":3,"order":1}],"away":[{"player_id":2,"order":1}]}
    a=deep.lineup_change("e1",one)
    b=deep.lineup_change("e1",two)
    assert a["available"] and not a["changed"]
    assert b["changed"]


def test_v310_model_emits_deep_signal_fields():
    market=pd.DataFrame([
        {"event_id":"e1","home_team":"H","away_team":"A","commence_time":"2026-09-29T00:00:00Z","market":"h2h","market_id":"h2h","selection":"H","point":None,"consensus_prob":.54,"best_odds":1.95,"best_book":"x","books":4},
        {"event_id":"e1","home_team":"H","away_team":"A","commence_time":"2026-09-29T00:00:00Z","market":"h2h","market_id":"h2h","selection":"A","point":None,"consensus_prob":.46,"best_odds":2.10,"best_book":"x","books":4},
    ])
    stats={
        "H":{"runs_per_game":5.0,"runs_allowed_per_game":4.0,"win_pct":.6,"recent10_win_pct":.6,"era":4.0,"games":10,"source":"MLB"},
        "A":{"runs_per_game":4.2,"runs_allowed_per_game":4.7,"win_pct":.48,"recent10_win_pct":.4,"era":4.6,"games":10,"source":"MLB"},
    }
    statuses={k:True for k in [
        "recent_form","starter_recent","bullpen","split","velocity","weather",
        "plate_discipline","statcast_quality","batted_ball_regression","pitch_mix","starter_workload",
        "bullpen_exact","lineup_platoon_exact","pitch_matchup","availability_news","lineup_change",
        "market_movement","roof","umpire","travel_rest","bvp","bullpen_manager","news_scan"]}
    ctx={
        "league":"MLB","stage":"FINAL","starter_confirmed":True,"lineup_confirmed":True,"source":"MLB v3.1",
        "home_starter_stats":{"era":3.5,"whip":1.15,"kbb":4.0},"away_starter_stats":{"era":4.2,"whip":1.30,"kbb":2.8},
        "advanced":{"advanced_completeness":1.0,"extra_uncertainty_pp":.2,"statuses":statuses,
                    "components":{"home_lineup_platoon_factor":1.02,"away_starter_deep_factor":1.02},
                    "deep_v31":{"market_movement":{"by_key":{"h2h|H|":{"move_pp":1.5,"from_open_pp":2.0}}}}},
    }
    out,_=analyze_official_event(market,stats,"MLB",ctx)
    assert not out.empty
    assert "plate_discipline_used" in out.columns
    assert bool(out.iloc[0]["plate_discipline_used"])
    assert out.iloc[0]["market_move_pp"]==1.5
    assert "whiff_chase_zone_contact" in out.iloc[0]["signal_summary"]
