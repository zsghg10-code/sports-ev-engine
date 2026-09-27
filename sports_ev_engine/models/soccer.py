
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, Tuple
from sports_ev_engine.core.asian import probs_from_score_matrix

@dataclass
class SoccerFeatures:
    home_elo: float
    away_elo: float
    home_xg_for: float
    home_xg_against: float
    away_xg_for: float
    away_xg_against: float
    home_absence_xg: float = 0.0
    away_absence_xg: float = 0.0
    home_rest_days: float = 6.0
    away_rest_days: float = 6.0
    home_adv_elo: float = 75.0
    competition_goal_mean: float = 2.55

def poisson_pmf(k, lam):
    return math.exp(-lam)*(lam**k)/math.factorial(k)

def estimate_lambdas(f: SoccerFeatures) -> tuple[float,float]:
    # Independent model: xG strength + Elo + absences + rest.
    avg_team=f.competition_goal_mean/2
    hxg=max(0.15,(f.home_xg_for + f.away_xg_against)/2)
    axg=max(0.15,(f.away_xg_for + f.home_xg_against)/2)
    elo_diff=(f.home_elo+f.home_adv_elo)-f.away_elo
    elo_mult=math.exp(elo_diff/900.0)
    rest_home=max(-3,min(3,f.home_rest_days-f.away_rest_days))*0.015
    rest_away=-rest_home
    home=max(0.12,hxg*(elo_mult**0.45)-f.home_absence_xg+rest_home)
    away=max(0.12,axg*(elo_mult**-0.45)-f.away_absence_xg+rest_away)
    # shrink total toward competition environment
    total=home+away
    target=f.competition_goal_mean
    shrink=0.25
    factor=((1-shrink)*total+shrink*target)/max(total,1e-6)
    return home*factor,away*factor

def score_matrix(home_lambda: float, away_lambda: float, max_goals: int=10) -> Dict[Tuple[int,int],float]:
    out={}
    s=0.0
    for h in range(max_goals+1):
        ph=poisson_pmf(h,home_lambda)
        for a in range(max_goals+1):
            p=ph*poisson_pmf(a,away_lambda)
            out[(h,a)]=p; s+=p
    # absorb truncated tail proportionally
    return {k:v/s for k,v in out.items()}

def market_probs(f: SoccerFeatures):
    hl,al=estimate_lambdas(f)
    m=score_matrix(hl,al)
    h=sum(p for (hg,ag),p in m.items() if hg>ag)
    d=sum(p for (hg,ag),p in m.items() if hg==ag)
    a=1-h-d
    return {"home_lambda":hl,"away_lambda":al,"home":h,"draw":d,"away":a,"matrix":m}

def price_market(f: SoccerFeatures, market: str, line: float|None=None):
    res=market_probs(f)
    w,p,l=probs_from_score_matrix(res["matrix"],market,line)
    return {"win_prob":w,"push_prob":p,"lose_prob":l,**{k:v for k,v in res.items() if k!="matrix"}}
