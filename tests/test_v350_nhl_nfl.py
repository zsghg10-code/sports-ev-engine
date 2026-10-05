import pandas as pd
from sports_ev_engine.pro_sports import analyze_pro_event, _nhl_matrix, SPORTS, TeamRecent

class FakeContext:
    def __init__(self, sport):
        self.sport=sport
    def recent(self, team, kickoff, n=None):
        if "Home" in team:
            return TeamRecent(games=6,wins=4,points_for=27,points_against=20,margin=7,rest_days=7), []
        return TeamRecent(games=6,wins=2,points_for=19,points_against=25,margin=-6,rest_days=6), []
    def availability(self, home, away, kickoff):
        return {"available":False,"home_weight":0.0,"away_weight":0.0,
                "qb_verified":False,"goalie_verified":False,"source":"MISSING"}

def _market():
    rows=[]
    for market, selection, point, p, odds in [
        ("h2h","Home Team",None,.58,1.72),
        ("h2h","Away Team",None,.42,2.30),
        ("totals","Over",45.5,.50,1.95),
        ("totals","Under",45.5,.50,1.95),
    ]:
        rows.append(dict(event_id="x",commence_time="2026-10-10T00:00:00Z",
                         home_team="Home Team",away_team="Away Team",market=market,
                         market_id=f"{market}|{point or ''}",selection=selection,point=point,
                         consensus_prob=p,best_odds=odds,best_book="test",books=4))
    return pd.DataFrame(rows)

def test_nhl_matrix_normalized():
    m=_nhl_matrix(3.3,2.7)
    assert abs(sum(m.values())-1)<1e-9

def test_nfl_output_has_manual_analysis_fields(monkeypatch):
    import sports_ev_engine.pro_sports as ps
    monkeypatch.setattr(ps, "apply_adaptive_layer", lambda f, family: f)
    out=analyze_pro_event(_market(),"americanfootball_nfl",context_provider=FakeContext("nfl"))
    assert not out.empty
    for col in ["model_win_prob","break_even","edge_pp","ev_roi","uncertainty_pp",
                "v3_decision_status","v3_candidate","v3_parlay_eligible",
                "signal_summary","missing_signals","counter_case_risk"]:
        assert col in out.columns
    assert out["model_win_prob"].between(0,1).all()
    assert out["missing_signals"].astype(str).str.contains("qb_status").any()

def test_nhl_missing_goalie_is_auditable(monkeypatch):
    import sports_ev_engine.pro_sports as ps
    monkeypatch.setattr(ps, "apply_adaptive_layer", lambda f, family: f)
    out=analyze_pro_event(_market(),"icehockey_nhl",context_provider=FakeContext("nhl"))
    assert not out.empty
    assert out["missing_signals"].astype(str).str.contains("starting_goalie").any()

def test_supported_sports():
    assert "americanfootball_nfl" in SPORTS
    assert "icehockey_nhl" in SPORTS
