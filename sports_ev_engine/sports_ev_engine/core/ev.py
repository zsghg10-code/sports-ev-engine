
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class EVResult:
    win_prob: float
    push_prob: float
    lose_prob: float
    odds: float
    break_even_win_prob: float
    edge_pp: float
    ev_roi: float
    conservative_ev_roi: float
    kelly_full: float
    kelly_scaled: float
    grade: str

def break_even_with_push(odds: float, push_prob: float = 0.0) -> float:
    # EV = W*(odds-1) - L = 0, L = 1-W-P -> W*odds = 1-P
    return max(0.0, (1.0 - push_prob) / odds)

def expected_roi(odds: float, win_prob: float, push_prob: float = 0.0) -> float:
    lose = max(0.0, 1.0 - win_prob - push_prob)
    return win_prob * (odds - 1.0) - lose

def kelly_fraction(odds: float, win_prob: float, push_prob: float = 0.0) -> float:
    """
    Kelly with push by conditioning on resolved outcomes.
    For a push, bankroll is unchanged.
    """
    resolved = 1.0 - push_prob
    if resolved <= 0:
        return 0.0
    p = win_prob / resolved
    q = 1.0 - p
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * p - q) / b)

def analyze_bet(
    odds: float,
    win_prob: float,
    push_prob: float = 0.0,
    uncertainty_pp: float = 0.0,
    kelly_scale: float = 0.25,
    kelly_cap: float = 0.05,
) -> EVResult:
    lose = max(0.0, 1.0 - win_prob - push_prob)
    be = break_even_with_push(odds, push_prob)
    edge_pp = (win_prob - be) * 100.0
    ev = expected_roi(odds, win_prob, push_prob)

    conservative_win = max(0.0, win_prob - uncertainty_pp/100.0)
    conservative_ev = expected_roi(odds, conservative_win, push_prob)

    k_full = kelly_fraction(odds, win_prob, push_prob)
    k_scaled = min(kelly_cap, k_full * kelly_scale)

    adj_edge = edge_pp - uncertainty_pp
    if conservative_ev > 0.05 and adj_edge >= 4:
        grade = "A"
    elif conservative_ev > 0.02 and adj_edge >= 2:
        grade = "B"
    elif conservative_ev > 0:
        grade = "C"
    else:
        grade = "PASS"

    return EVResult(
        win_prob=win_prob,
        push_prob=push_prob,
        lose_prob=lose,
        odds=odds,
        break_even_win_prob=be,
        edge_pp=edge_pp,
        ev_roi=ev,
        conservative_ev_roi=conservative_ev,
        kelly_full=k_full,
        kelly_scaled=k_scaled,
        grade=grade,
    )
