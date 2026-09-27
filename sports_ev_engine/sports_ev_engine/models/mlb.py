
from __future__ import annotations
import math
from dataclasses import dataclass

def sigmoid(x): return 1/(1+math.exp(-x))

@dataclass
class MLBFeatures:
    home_wrcr: float      # wRC+ vs opposing handedness
    away_wrcr: float
    home_sp_xfip: float
    away_sp_xfip: float
    home_sp_kbb: float    # K-BB% as decimal
    away_sp_kbb: float
    home_bullpen_xfip: float
    away_bullpen_xfip: float
    home_bullpen_fatigue: float = 0.0  # standardized 0..1
    away_bullpen_fatigue: float = 0.0
    home_park_run_factor: float = 1.0
    weather_run_factor: float = 1.0
    home_adv_runs: float = 0.18

def estimate_run_means(f: MLBFeatures):
    # Transparent, tunable heuristic model. Backtesting/calibration should replace constants.
    lg=4.35
    def offense(wrc): return max(0.65,min(1.35,wrc/100))
    home_pitch=(f.away_sp_xfip/4.20)*0.62 + (f.away_bullpen_xfip/4.20)*0.38
    away_pitch=(f.home_sp_xfip/4.20)*0.62 + (f.home_bullpen_xfip/4.20)*0.38
    home_k_adj=math.exp(-1.2*(f.away_sp_kbb-0.14))
    away_k_adj=math.exp(-1.2*(f.home_sp_kbb-0.14))
    home_fat=1+0.10*f.away_bullpen_fatigue
    away_fat=1+0.10*f.home_bullpen_fatigue
    env=f.home_park_run_factor*f.weather_run_factor
    h=max(1.5,lg*offense(f.home_wrcr)*home_pitch*home_k_adj*home_fat*env + f.home_adv_runs)
    a=max(1.5,lg*offense(f.away_wrcr)*away_pitch*away_k_adj*away_fat*env)
    return h,a

def win_probability(f: MLBFeatures):
    hr,ar=estimate_run_means(f)
    # Pythagorean/logistic approximation from expected run differential
    p=sigmoid((hr-ar)/1.35)
    return {"home_win":p,"away_win":1-p,"home_runs":hr,"away_runs":ar,"total_runs":hr+ar}

def total_over_probability(f: MLBFeatures, line: float):
    hr,ar=estimate_run_means(f)
    lam=hr+ar
    # Poisson total approximation
    import math
    maxk=30
    probs=[math.exp(-lam)*lam**k/math.factorial(k) for k in range(maxk+1)]
    over=sum(p for k,p in enumerate(probs) if k>line)
    push=sum(p for k,p in enumerate(probs) if abs(k-line)<1e-9)
    under=max(0,1-over-push)
    return {"over":over,"push":push,"under":under,"lambda":lam}
