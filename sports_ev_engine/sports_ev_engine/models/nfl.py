
from __future__ import annotations
import math
from dataclasses import dataclass

def sigmoid(x): return 1/(1+math.exp(-x))

@dataclass
class NFLFeatures:
    home_epa_play: float
    away_epa_play: float
    home_success_rate: float
    away_success_rate: float
    home_def_epa_play: float  # lower better
    away_def_epa_play: float
    home_qb_adj: float = 0.0   # points
    away_qb_adj: float = 0.0
    home_ol_adj: float = 0.0
    away_ol_adj: float = 0.0
    home_injury_adj: float = 0.0
    away_injury_adj: float = 0.0
    home_field_points: float = 1.5

def projected_margin(f: NFLFeatures):
    off = (f.home_epa_play-f.away_epa_play)*24
    defense = (f.away_def_epa_play-f.home_def_epa_play)*18
    sr = (f.home_success_rate-f.away_success_rate)*14
    personnel = (f.home_qb_adj-f.away_qb_adj)+(f.home_ol_adj-f.away_ol_adj)+(f.home_injury_adj-f.away_injury_adj)
    return off+defense+sr+personnel+f.home_field_points

def win_probability(f: NFLFeatures):
    margin=projected_margin(f)
    # NFL single-game scoring margin sd ~ 13-14; logistic scale approximation.
    p=sigmoid(margin/6.7)
    return {"home_win":p,"away_win":1-p,"projected_margin":margin}

def cover_probability(f: NFLFeatures, home_spread: float):
    margin=projected_margin(f)
    # Normal approximation; positive home_spread means +points.
    from statistics import NormalDist
    sd=13.4
    z=(margin+home_spread)/sd
    p=NormalDist().cdf(z)
    return {"home_cover":p,"away_cover":1-p,"projected_margin":margin}
