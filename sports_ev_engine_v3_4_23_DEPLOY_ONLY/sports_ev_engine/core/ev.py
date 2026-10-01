
from dataclasses import dataclass

@dataclass
class EVResult:
    odds: float
    win_prob: float
    push_prob: float
    lose_prob: float
    break_even: float
    edge_pp: float
    ev_roi: float
    conservative_ev_roi: float
    kelly_scaled: float
    grade: str

def break_even_with_push(odds, push_prob=0.0):
    return max(0.0, (1.0 - push_prob) / odds)

def expected_roi(odds, win_prob, push_prob=0.0):
    lose = max(0.0, 1.0 - win_prob - push_prob)
    return win_prob * (odds - 1.0) - lose

def kelly_fraction(odds, win_prob, push_prob=0.0):
    resolved = 1.0 - push_prob
    if resolved <= 0:
        return 0.0
    p = win_prob / resolved
    q = 1.0 - p
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - q) / b)

def analyze_bet(odds, win_prob, push_prob=0.0, uncertainty_pp=0.0,
                kelly_scale=0.25, kelly_cap=0.05):
    lose = max(0.0, 1.0-win_prob-push_prob)
    be = break_even_with_push(odds, push_prob)
    edge = (win_prob-be)*100
    ev = expected_roi(odds, win_prob, push_prob)
    conservative_win = max(0.0, win_prob-uncertainty_pp/100)
    cev = expected_roi(odds, conservative_win, push_prob)
    kelly = min(kelly_cap, kelly_fraction(odds, win_prob, push_prob)*kelly_scale)
    adjusted_edge = edge-uncertainty_pp
    if cev >= 0.05 and adjusted_edge >= 4:
        grade = "A"
    elif cev >= 0.02 and adjusted_edge >= 2:
        grade = "B"
    elif cev > 0:
        grade = "C"
    else:
        grade = "PASS"
    return EVResult(odds,win_prob,push_prob,lose,be,edge,ev,cev,kelly,grade)
