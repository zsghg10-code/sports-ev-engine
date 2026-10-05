import math

def test_placeholder_contract():
    # Integration tests live in the production repo; this package test simply
    # documents the v3.6 safety caps expected by deployment.
    from sports_ev_engine.self_learning import MIN_N, MAX_MOVE_PP
    assert MIN_N >= 40
    assert 0 < MAX_MOVE_PP <= 2.0

def test_generic_review_never_calls_plain_loss_good_pick():
    from sports_ev_engine.universal_postgame import classify_generic
    row = {
        "sport_family": "football_nfl",
        "market": "h2h",
        "selection": "Home",
        "home_team": "Home",
        "away_team": "Away",
        "home_score": 10,
        "away_score": 30,
        "settle_win": 0,
        "settle_push": 0,
        "settle_loss": 1,
        "model_win_prob": .67,
        "clv_market_prob_pp": None,
    }
    x = classify_generic(row)
    assert x["postgame_quality"] != "CONFIRMED"
    assert x["postgame_class"] != "MODEL_CONFIRMATION"
