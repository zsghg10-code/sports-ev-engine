
from __future__ import annotations

def implied_probability(odds: float) -> float:
    if odds <= 1:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / odds

def devig_power(odds):
    raw = [implied_probability(float(o)) for o in odds]
    lo, hi = 0.01, 10.0
    for _ in range(120):
        mid = (lo + hi) / 2
        s = sum(p ** mid for p in raw)
        if s > 1:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    probs = [p ** k for p in raw]
    s = sum(probs)
    return [p / s for p in probs]
