import pandas as pd
import pytest

from sports_ev_engine.pro_sports import (
    TeamRecent, _nhl_matrix, _line_prob_from_matrix, analyze_pro_event, PRO_SPORTS_BUILD
)


class EarlyContext:
    def recent(self, team, kickoff, n=None):
        home = "Home" in team
        return TeamRecent(
            games=8, effective_games=3.4, current_season_games=1, prior_season_games=7,
            wins=5 if home else 3, points_for=3.4 if home else 2.7,
            points_against=2.6 if home else 3.2, margin=.8 if home else -.5,
            rest_days=2.0, shots_for=32 if home else 28, shots_against=27 if home else 31,
            pp_pct=24 if home else 18,
        ), []
    def availability(self, home, away, kickoff):
        return {"available":False,"home_weight":0.0,"away_weight":0.0,"home_count":0,"away_count":0,"source":""}
    def goalie_status(self, home, away, kickoff):
        return {"available":False,"home_confirmed":False,"away_confirmed":False,"home_goalie":"","away_goalie":"","source":""}


class FinalContext(EarlyContext):
    def goalie_status(self, home, away, kickoff):
        return {"available":True,"home_confirmed":True,"away_confirmed":True,"home_goalie":"Home G","away_goalie":"Away G","source":"test"}


def market(two_way=True):
    rows=[
        dict(event_id="x",commence_time="2026-10-06T00:00:00Z",home_team="Home Team",away_team="Away Team",
             market="h2h",market_id="h2h",selection="Home Team",point=None,consensus_prob=.56,best_odds=1.90,best_book="x",books=4),
        dict(event_id="x",commence_time="2026-10-06T00:00:00Z",home_team="Home Team",away_team="Away Team",
             market="h2h",market_id="h2h",selection="Away Team",point=None,consensus_prob=.44,best_odds=2.20,best_book="x",books=4),
        dict(event_id="x",commence_time="2026-10-06T00:00:00Z",home_team="Home Team",away_team="Away Team",
             market="totals",market_id="totals|6.5",selection="Over",point=6.5,consensus_prob=.50,best_odds=2.00,best_book="x",books=4),
        dict(event_id="x",commence_time="2026-10-06T00:00:00Z",home_team="Home Team",away_team="Away Team",
             market="totals",market_id="totals|6.5",selection="Under",point=6.5,consensus_prob=.50,best_odds=2.00,best_book="x",books=4),
    ]
    if not two_way:
        rows.append(dict(event_id="x",commence_time="2026-10-06T00:00:00Z",home_team="Home Team",away_team="Away Team",
             market="h2h",market_id="h2h",selection="Draw",point=None,consensus_prob=.20,best_odds=4.50,best_book="x",books=4))
    return pd.DataFrame(rows)


def test_build_version():
    assert "3.6.3" in PRO_SPORTS_BUILD


def test_nhl_matrix_normalizes():
    m=_nhl_matrix(3.2,2.8)
    assert abs(sum(p for _,_,p in m)-1)<1e-9


def test_regulation_three_way_sums_to_one():
    m=_nhl_matrix(3.0,3.0)
    ph,_=_line_prob_from_matrix(m,market="h2h",selection="Home",point=None,home="Home",away="Away",h2h_scope="REGULATION_3WAY")
    pa,_=_line_prob_from_matrix(m,market="h2h",selection="Away",point=None,home="Home",away="Away",h2h_scope="REGULATION_3WAY")
    pd,_=_line_prob_from_matrix(m,market="h2h",selection="Draw",point=None,home="Home",away="Away",h2h_scope="REGULATION_3WAY")
    assert ph+pa+pd == pytest.approx(1.0,abs=1e-9)
    assert pd > 0


def test_early_season_is_provisional_not_data_hold(monkeypatch):
    import sports_ev_engine.pro_sports as ps
    monkeypatch.setattr(ps,"apply_adaptive_layer",lambda f,family:f)
    out=analyze_pro_event(market(),"icehockey_nhl",context_provider=EarlyContext())
    assert not out.empty
    assert set(out["stage"]) == {"PROVISIONAL"}
    assert not out["v3_decision_status"].eq("DATA_HOLD").any()
    assert out["robust_ev_p10"].notna().all()
    assert out["v3_parlay_eligible"].eq(False).all()
    assert out["home_prior_season_games"].eq(7).all()


def test_goalie_confirmation_can_promote_to_final(monkeypatch):
    import sports_ev_engine.pro_sports as ps
    monkeypatch.setattr(ps,"apply_adaptive_layer",lambda f,family:f)
    out=analyze_pro_event(market(),"icehockey_nhl",context_provider=FinalContext())
    assert not out.empty
    assert set(out["stage"]) == {"FINAL"}
    assert out["home_goalie_confirmed"].all()
    assert out["away_goalie_confirmed"].all()


def test_three_way_scope_detected(monkeypatch):
    import sports_ev_engine.pro_sports as ps
    monkeypatch.setattr(ps,"apply_adaptive_layer",lambda f,family:f)
    out=analyze_pro_event(market(two_way=False),"icehockey_nhl",context_provider=EarlyContext())
    assert (out.loc[out["market"].eq("h2h"),"nhl_h2h_scope"]=="REGULATION_3WAY").all()
    assert out.loc[out["selection"].eq("Draw"),"model_win_prob"].notna().all()
