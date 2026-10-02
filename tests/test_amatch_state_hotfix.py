import pandas as pd
from sports_ev_engine.national_policy import finalize_national

def base(now):
    return dict(
        point_ev_roi=.08, selection_status="SCENARIO_PASS",
        v3_decision_status="ROBUST", counter_case_risk="LOW", adaptive_gate="OK",
        scenario_candidate=True, scenario_parlay_eligible=True,
        v3_candidate=True, v3_parlay_eligible=True, parlay_eligible=False,
        lineup_confirmed=True,
        home_xg_for=1.2, home_xg_against=1.0, away_xg_for=1.1, away_xg_against=1.2,
        xg_samples_home=3, xg_samples_away=3, xg_collection_status="OK",
        injury_available=True, home_rest_days=4, away_rest_days=4,
        data_checked_at=now.isoformat(), xg_checked_at=now.isoformat()
    )

def test_preserve_flags():
    now=pd.Timestamp("2026-10-03T00:00:00Z")
    r=base(now)
    out=finalize_national(r,now=now)
    assert out["national_status"]=="FINAL_BET"
    assert out["scenario_candidate"] is True
    assert out["v3_candidate"] is True
    assert out["parlay_eligible"] is False

def test_high_risk_never_becomes_provisional():
    now=pd.Timestamp("2026-10-03T00:00:00Z")
    r=base(now)
    r["counter_case_risk"]="HIGH"
    r["lineup_confirmed"]=False
    out=finalize_national(r,now=now)
    assert out["national_status"]=="COMBO_EXCLUDE"
