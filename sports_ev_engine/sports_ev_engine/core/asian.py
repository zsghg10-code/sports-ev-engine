
from __future__ import annotations
from collections import defaultdict
from typing import Dict, Tuple

def settle_total_under(total_goals: int, line: float) -> tuple[float, float, float]:
    """Return fractions of stake that win, push, lose for Asian UNDER."""
    # quarter lines split into adjacent half-lines, e.g. 2.25 = 2.0 + 2.5
    if abs(line*4 - round(line*4)) > 1e-9:
        raise ValueError("Asian line must be in 0.25 increments")

    q = round(line*4)
    if q % 2 == 1:  # .25 or .75 split
        low = (q-1)/4
        high = (q+1)/4
        a = settle_total_under(total_goals, low)
        b = settle_total_under(total_goals, high)
        return tuple((x+y)/2 for x,y in zip(a,b))

    # integer or half line
    if abs(line - round(line)) < 1e-9:
        n = int(round(line))
        if total_goals < n: return (1.0,0.0,0.0)
        if total_goals == n: return (0.0,1.0,0.0)
        return (0.0,0.0,1.0)
    else:
        if total_goals < line: return (1.0,0.0,0.0)
        return (0.0,0.0,1.0)

def settle_total_over(total_goals: int, line: float) -> tuple[float, float, float]:
    w,p,l = settle_total_under(total_goals, line)
    return (l,p,w)

def settle_home_handicap(home_goals: int, away_goals: int, line: float) -> tuple[float,float,float]:
    """Asian handicap on home team. line=-1 means home -1."""
    if abs(line*4-round(line*4)) > 1e-9:
        raise ValueError("Asian line must be in 0.25 increments")
    q = round(line*4)
    if q % 2 == 1:
        low=(q-1)/4; high=(q+1)/4
        a=settle_home_handicap(home_goals,away_goals,low)
        b=settle_home_handicap(home_goals,away_goals,high)
        return tuple((x+y)/2 for x,y in zip(a,b))
    score = home_goals - away_goals + line
    if score > 0: return (1.0,0.0,0.0)
    if abs(score) < 1e-12: return (0.0,1.0,0.0)
    return (0.0,0.0,1.0)

def probs_from_score_matrix(score_probs: Dict[Tuple[int,int], float], market: str, line: float|None=None):
    w=p=l=0.0
    for (hg,ag), prob in score_probs.items():
        if market=="home_ml":
            outcome=(1,0,0) if hg>ag else ((0,1,0) if hg==ag else (0,0,1))
        elif market=="away_ml":
            outcome=(1,0,0) if ag>hg else ((0,1,0) if hg==ag else (0,0,1))
        elif market=="draw":
            outcome=(1,0,0) if hg==ag else (0,0,1)
        elif market=="under":
            outcome=settle_total_under(hg+ag, float(line))
        elif market=="over":
            outcome=settle_total_over(hg+ag, float(line))
        elif market=="home_ah":
            outcome=settle_home_handicap(hg,ag,float(line))
        elif market=="away_ah":
            outcome=settle_home_handicap(ag,hg,float(line))
        else:
            raise ValueError(f"unknown market {market}")
        w += prob*outcome[0]
        p += prob*outcome[1]
        l += prob*outcome[2]
    return w,p,l
