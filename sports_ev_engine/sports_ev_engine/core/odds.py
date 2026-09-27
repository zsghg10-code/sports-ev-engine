
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

@dataclass
class MarketOutcome:
    name: str
    odds: float

def implied_probability(decimal_odds: float) -> float:
    if decimal_odds <= 1:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / decimal_odds

def devig_proportional(odds: Sequence[float]) -> list[float]:
    """Remove bookmaker margin by proportional normalization."""
    raw = [implied_probability(o) for o in odds]
    s = sum(raw)
    return [p / s for p in raw]

def devig_power(odds: Sequence[float], tol: float = 1e-12) -> list[float]:
    """
    Power-method de-vig. Finds k such that sum((1/odds)^k)=1.
    Often more realistic than simple proportional normalization.
    """
    raw = [implied_probability(o) for o in odds]
    lo, hi = 0.01, 10.0
    for _ in range(120):
        mid = (lo + hi) / 2
        s = sum(p ** mid for p in raw)
        if s > 1:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = (lo + hi) / 2
    probs = [p ** k for p in raw]
    s = sum(probs)
    return [p/s for p in probs]

def no_vig_two_way(odds_a: float, odds_b: float) -> tuple[float, float]:
    p = devig_proportional([odds_a, odds_b])
    return p[0], p[1]
