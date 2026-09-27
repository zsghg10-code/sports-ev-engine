
from __future__ import annotations
import itertools
import math
from dataclasses import dataclass
from typing import Iterable, Sequence
import numpy as np

@dataclass
class Leg:
    event_id: str
    selection: str
    odds: float
    win_prob: float
    push_prob: float = 0.0
    conservative_ev: float = 0.0
    uncertainty_pp: float = 0.0
    group: str = ""  # same event/game group

@dataclass
class Parlay:
    legs: tuple[Leg,...]
    nominal_odds: float
    approx_hit_prob: float
    approx_ev: float
    score: float

def _pair_penalty(a: Leg, b: Leg) -> float:
    """
    Conservative penalty for unmodeled correlation.
    Same-event legs are penalized heavily and excluded by default in optimizer.
    """
    if a.event_id == b.event_id:
        return 0.80
    if a.group and a.group == b.group:
        return 0.95
    return 1.0

def optimize_parlays(
    legs: Sequence[Leg],
    n_legs: int = 2,
    top_n: int = 10,
    min_leg_ev: float = 0.0,
    allow_same_event: bool = False,
) -> list[Parlay]:
    usable=[x for x in legs if x.conservative_ev >= min_leg_ev]
    out=[]
    for combo in itertools.combinations(usable,n_legs):
        if not allow_same_event and len({x.event_id for x in combo})<len(combo):
            continue
        odds=math.prod(x.odds for x in combo)
        p=math.prod(x.win_prob for x in combo)
        penalty=1.0
        for a,b in itertools.combinations(combo,2):
            penalty *= _pair_penalty(a,b)
        p *= penalty
        ev=p*odds-1
        avg_unc=sum(x.uncertainty_pp for x in combo)/len(combo)
        # favors positive EV, moderate hit probability, and lower uncertainty
        score=ev + 0.20*p - 0.005*avg_unc
        out.append(Parlay(combo,odds,p,ev,score))
    out.sort(key=lambda x:x.score, reverse=True)
    return out[:top_n]

def monte_carlo_correlated_binary(probs, corr, n=100000, seed=42):
    """
    Gaussian-copula approximation for joint binary events.
    Useful when user supplies/learns a correlation matrix.
    """
    probs=np.asarray(probs,float)
    corr=np.asarray(corr,float)
    rng=np.random.default_rng(seed)
    z=rng.multivariate_normal(np.zeros(len(probs)),corr,size=n)
    try:
        from scipy.stats import norm
        thresh=norm.ppf(probs)
    except Exception:
        # approximate inverse-normal fallback using stdlib NormalDist
        from statistics import NormalDist
        nd=NormalDist()
        thresh=np.array([nd.inv_cdf(float(p)) for p in probs])
    hits=(z<=thresh).all(axis=1)
    return float(hits.mean())
