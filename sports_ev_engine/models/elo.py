from __future__ import annotations
import math
from sports_ev_engine.models.soccer_auto import norm_name

PATCH_BUILD = "3.4.16-robust-form-xg"


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
        exp_h = expected_score(rh + (0.0 if fx.get("neutral") else home_adv), ra)
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


def _soft_cap_goals(value: float, cap: float=3.0, tail_scale: float=1.10) -> float:
    """Robustify one match's goal count without throwing the match away.

    Values up to ``cap`` are untouched.  Above the cap the extra goals saturate
    smoothly, so a 6-0/7-0 against a minnow remains a strong attacking signal but
    cannot dominate a six-match form average as if every goal were equally
    predictive.  This is used only for international recent-form features.
    """
    v=max(0.0,float(value))
    if v <= cap:
        return v
    excess=v-cap
    # Saturating tail: cap + tail_scale is the asymptotic maximum contribution.
    return cap + tail_scale*(1.0-math.exp(-excess/max(tail_scale,1e-9)))


def opponent_adjusted_form(fixtures, team_name, ratings, cutoff_ts=None, recent_n=6,
                           decay=0.86, international=False):
    """
    Recent goals/results, adjusted for opponent strength.

    v3.4.16 international mode adds two protections against blowout bias:
    1) a smooth soft-cap for extreme single-match goal counts; and
    2) a stronger Elo opponent correction than the old +/- ~12% per 200 Elo.

    Scoring heavily against a very weak opponent therefore still helps the form
    estimate, but much less than scoring the same number against an average or
    strong opponent.  Club behaviour is intentionally unchanged unless
    ``international=True`` is passed.
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
    raw_gf_sum=raw_ga_sum=0.0
    robust_gf_sum=robust_ga_sum=0.0
    opp_mult_sum=0.0
    blowout_shrunk=0
    for i,(ts,raw_gf,raw_ga,opp) in enumerate(rows):
        wt = decay**i
        opp_elo = ratings.get(opp,1500.0)

        if international:
            # Stronger opponent adjustment for national teams.  A 300 Elo weaker
            # opponent discounts attacking output by ~24%; the reverse credits
            # output against a 300 Elo stronger opponent by ~31%.
            attack_mult = math.exp((opp_elo-1500.0)/1100.0)
            defense_mult = math.exp(-(opp_elo-1500.0)/1100.0)
            rgf=_soft_cap_goals(raw_gf,3.0,1.10)
            rga=_soft_cap_goals(raw_ga,3.0,1.10)
            if rgf < raw_gf-1e-9 or rga < raw_ga-1e-9:
                blowout_shrunk += 1
        else:
            # Preserve pre-v3.4.16 club behaviour.
            attack_mult = math.exp((opp_elo-1500.0)/1700.0)
            defense_mult = math.exp(-(opp_elo-1500.0)/1700.0)
            rgf=raw_gf; rga=raw_ga

        # Avoid one poorly connected Elo node creating an absurd multiplier.
        attack_mult=max(.68,min(1.47,attack_mult))
        defense_mult=max(.68,min(1.47,defense_mult))
        adj_gf = rgf * attack_mult
        adj_ga = rga * defense_mult
        gf += wt*adj_gf
        ga += wt*adj_ga
        pts += wt*(3 if raw_gf>raw_ga else 1 if raw_gf==raw_ga else 0)
        raw_gf_sum += wt*raw_gf
        raw_ga_sum += wt*raw_ga
        robust_gf_sum += wt*rgf
        robust_ga_sum += wt*rga
        opp_mult_sum += wt*attack_mult
        w += wt

    return {
        "gf":gf/w,"ga":ga/w,"ppg":pts/w,"matches":len(rows),
        "raw_gf":raw_gf_sum/w,"raw_ga":raw_ga_sum/w,
        "robust_gf":robust_gf_sum/w,"robust_ga":robust_ga_sum/w,
        "avg_attack_opp_mult":opp_mult_sum/w,
        "blowout_matches_shrunk":blowout_shrunk,
        "form_mode":"international_robust_opponent_adjusted" if international else "legacy_opponent_adjusted",
    }
