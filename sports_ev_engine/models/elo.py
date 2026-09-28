
from __future__ import annotations
import math
from sports_ev_engine.models.soccer_auto import norm_name

def expected_score(rating_a, rating_b):
    return 1.0 / (1.0 + 10.0 ** ((rating_b-rating_a)/400.0))

def build_elo(fixtures, base=1500.0, k=22.0, home_adv=55.0):
    """
    Build competition-relative Elo from completed fixtures.
    This gives opponent-strength adjustment without requiring another paid provider.
    """
    ratings = {}
    history = []
    completed = []
    for fx in fixtures:
        st = fx.get("fixture",{}).get("status",{}).get("short")
        goals = fx.get("goals",{})
        if st not in {"FT","AET","PEN"}:
            continue
        if goals.get("home") is None or goals.get("away") is None:
            continue
        completed.append(fx)

    completed.sort(key=lambda x:x.get("fixture",{}).get("timestamp",0))

    for fx in completed:
        home = norm_name(fx["teams"]["home"]["name"])
        away = norm_name(fx["teams"]["away"]["name"])
        hg = int(fx["goals"]["home"])
        ag = int(fx["goals"]["away"])

        rh = ratings.get(home, base)
        ra = ratings.get(away, base)
        exp_h = expected_score(rh + home_adv, ra)
        actual_h = 1.0 if hg > ag else 0.5 if hg == ag else 0.0

        gd = abs(hg-ag)
        gd_mult = 1.0 if gd <= 1 else min(1.75, 1.0 + 0.18*(gd-1))
        delta = k * gd_mult * (actual_h-exp_h)

        ratings[home] = rh + delta
        ratings[away] = ra - delta
        history.append((fx.get("fixture",{}).get("timestamp",0),home,away,ratings[home],ratings[away]))

    return ratings

def elo_win_strength(home_elo, away_elo, home_adv=55.0):
    return expected_score(home_elo+home_adv, away_elo)

def opponent_adjusted_form(fixtures, team_name, ratings, cutoff_ts=None, recent_n=6, decay=0.86):
    """
    Recent goals/results, adjusted for opponent strength.
    Scoring against a strong opponent counts more; conceding to a strong opponent counts less.
    """
    tn = norm_name(team_name)
    rows = []
    for fx in fixtures:
        st = fx.get("fixture",{}).get("status",{}).get("short")
        if st not in {"FT","AET","PEN"}:
            continue
        ts = int(fx.get("fixture",{}).get("timestamp",0))
        if cutoff_ts is not None and ts >= cutoff_ts:
            continue
        teams = fx.get("teams",{})
        home = norm_name(teams.get("home",{}).get("name",""))
        away = norm_name(teams.get("away",{}).get("name",""))
        if tn not in {home,away}:
            continue
        goals = fx.get("goals",{})
        if goals.get("home") is None or goals.get("away") is None:
            continue
        if home == tn:
            gf,ga = goals["home"],goals["away"]
            opp = away
        else:
            gf,ga = goals["away"],goals["home"]
            opp = home
        rows.append((ts,float(gf),float(ga),opp))

    rows.sort(reverse=True)
    rows = rows[:int(recent_n)]
    if not rows:
        return None

    gf=ga=pts=w=0.0
    for i,(ts,raw_gf,raw_ga,opp) in enumerate(rows):
        wt = decay**i
        opp_elo = ratings.get(opp,1500.0)
        # modest adjustment: +/- ~12% for a 200 Elo gap
        attack_mult = math.exp((opp_elo-1500.0)/1700.0)
        defense_mult = math.exp(-(opp_elo-1500.0)/1700.0)
        adj_gf = raw_gf * attack_mult
        adj_ga = raw_ga * defense_mult
        gf += wt*adj_gf
        ga += wt*adj_ga
        pts += wt*(3 if raw_gf>raw_ga else 1 if raw_gf==raw_ga else 0)
        w += wt

    return {"gf":gf/w,"ga":ga/w,"ppg":pts/w,"matches":len(rows)}
