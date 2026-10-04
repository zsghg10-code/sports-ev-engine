import pandas as pd

from sports_ev_engine.kbo_safety_patch import reconcile_kbo_starters, regressed_recent_rate
from sports_ev_engine.daily_combo import prepare_daily_candidates


def _base(**kw):
    x = {
        "event_id": "k1", "commence_time": "2099-10-04T05:00:00Z",
        "home_team": "Samsung Lions", "away_team": "Doosan Bears",
        "sport_family": "baseball_kbo", "sport_key": "baseball_kbo",
        "market": "h2h", "selection": "Samsung Lions", "point": None,
        "best_odds": 1.80, "consensus_prob": .54, "model_win_prob": .61,
        "break_even": 1/1.80, "edge_pp": 5.4, "ev_roi": .098,
        "conservative_ev_roi": .05, "uncertainty_pp": 3.5,
        "v3_decision_status": "ROBUST", "v3_candidate": True,
        "v3_parlay_eligible": True, "robust_positive_ratio": .93,
        "robust_ev_p10": .025, "counter_case_risk": "LOW",
        "stage": "FINAL", "data_quality": "HIGH", "lineup_confirmed": True,
        "starter_confirmed": True, "recorded_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    x.update(kw)
    return x


def test_starter_conflict_blocks_confirmation_and_uses_new_name_for_diagnostics():
    r = reconcile_kbo_starters(
        "Fedde", "Benjamin", "Lee Seunghyun", "Benjamin",
        official_confirmed=True, naver_authoritative=True,
    )
    assert r["starter_source_conflict"] is True
    assert r["starter_confirmed"] is False
    assert r["home_starter"] == "Lee Seunghyun"
    assert "Fedde" in r["starter_conflict_detail"]


def test_matching_crosscheck_remains_confirmed():
    r = reconcile_kbo_starters(
        "Lee Seunghyun", "Benjamin", "Lee Seunghyun", "Benjamin",
        official_confirmed=True, naver_authoritative=True,
    )
    assert r["starter_source_conflict"] is False
    assert r["starter_confirmed"] is True
    assert r["starter_verified"] is True


def test_single_blowout_is_regressed_before_recent_run_factor():
    recent = {
        "available": True,
        "runs_for_per_game": 6.2,
        "raw": [{"rf": 13}, {"rf": 4}, {"rf": 5}, {"rf": 4}, {"rf": 5}],
    }
    v = regressed_recent_rate(recent, "rf", 4.5)
    assert v < (13 + 4 + 5 + 4 + 5) / 5
    assert recent["blowout_regression_applied"] is True


def test_sensitive_is_visible_single_but_never_daily_combo_leg():
    row = _base(v3_decision_status="SENSITIVE", v3_candidate=True, v3_parlay_eligible=False)
    out = prepare_daily_candidates(pd.DataFrame([row]))
    assert len(out) == 1
    assert bool(out.iloc[0]["daily_single_eligible"]) is True
    assert bool(out.iloc[0]["daily_combo_eligible"]) is False


def test_kbo_totals_requires_four_point_edge_for_parlay():
    row = _base(
        market="totals", selection="Over", point=9.5,
        edge_pp=3.2, best_odds=1.90, model_win_prob=.56,
        consensus_prob=.52, break_even=1/1.90,
        v3_candidate=True, v3_parlay_eligible=True,
    )
    out = prepare_daily_candidates(pd.DataFrame([row]))
    assert len(out) == 1
    assert bool(out.iloc[0]["daily_single_eligible"]) is True
    assert bool(out.iloc[0]["daily_combo_eligible"]) is False
