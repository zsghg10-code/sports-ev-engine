from sports_ev_engine.kbo_stability_v362 import guard_row


def base_row(**kw):
    x = {
        "market": "totals", "selection": "Under",
        "v3_decision_status": "ROBUST", "v3_candidate": True,
        "v3_parlay_eligible": True, "parlay_eligible": True,
        "lineup_confirmed": True, "counter_case_risk": "LOW",
        "market_move_pp": 0.0, "market_from_open_pp": 0.0,
    }
    x.update(kw)
    return x


def test_robust_high_counter_is_capped_sensitive():
    r = guard_row(base_row(counter_case_risk="HIGH"), home_meltdown_risk=.05, away_meltdown_risk=.05)
    assert r["v3_decision_status"] == "SENSITIVE"
    assert r["v3_candidate"] is True
    assert r["v3_parlay_eligible"] is False


def test_strong_adverse_market_move_forces_review():
    r = guard_row(base_row(market_move_pp=-3.8), home_meltdown_risk=.05, away_meltdown_risk=.05)
    assert r["v3_decision_status"] == "REVIEW"
    assert r["v3_candidate"] is False


def test_under_high_counter_plus_adverse_move_is_review():
    r = guard_row(base_row(counter_case_risk="HIGH", market_move_pp=-2.4), home_meltdown_risk=.08, away_meltdown_risk=.08)
    assert r["v3_decision_status"] == "REVIEW"
    assert r["v3_candidate"] is False


def test_under_meltdown_risk_cannot_be_robust():
    r = guard_row(base_row(), home_meltdown_risk=.22, away_meltdown_risk=.20)
    assert r["v3_decision_status"] == "SENSITIVE"
    assert r["v3_parlay_eligible"] is False
