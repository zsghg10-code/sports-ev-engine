import math
import pandas as pd

from sports_ev_engine.reasoning_engine import (
    apply_soccer_context, build_counter_cases, decision_fields,
    scenario_assessment, SignalLedger,
)
from sports_ev_engine.core.parlay import optimize_parlays


def test_missing_deep_context_does_not_change_lambdas_or_uncertainty():
    h,a,unc,ledger=apply_soccer_context(1.7,0.9,{"deep_context_attempted":False})
    assert math.isclose(h,1.7)
    assert math.isclose(a,0.9)
    assert unc==0
    assert "recent_xg" in ledger.missing


def test_robust_requires_stress_survival():
    scenarios=[(.70,0.0,"s") for _ in range(9)]
    r=scenario_assessment(odds=1.80,market_prob=.56,model_weight=.55,
                          scenarios=scenarios,base_ev=.08,sanity="OK",
                          lineup_required=True,lineup_confirmed=True)
    assert r["robust_status"]=="ROBUST"
    assert r["robust_positive_ratio"]>=.85
    assert r["robust_ev_p10"]>0

    weak=[(.48,0.0,"s") for _ in range(9)]
    s=scenario_assessment(odds=1.90,market_prob=.52,model_weight=.60,
                          scenarios=weak,base_ev=.01,sanity="OK")
    assert s["robust_status"] in {"SENSITIVE","FRAGILE"}
    assert not s["robust_parlay_eligible"]


def test_countercase_signal_coverage_only_when_provided():
    _,risk=build_counter_cases(sample_matches=8,lineup_confirmed=None,sanity="OK",
                               uncertainty_pp=3,signal_coverage=None)
    assert risk=="LOW"
    cases,risk=build_counter_cases(sample_matches=8,lineup_confirmed=None,sanity="OK",
                                   uncertainty_pp=3,signal_coverage=.2)
    assert risk=="HIGH"
    assert any(x["code"]=="LOW_SIGNAL_COVERAGE" for x in cases)


def test_parlay_optimizer_uses_only_v3_robust_rows():
    df=pd.DataFrame([
        dict(event_id='1',display_pick='A',best_odds=1.8,model_win_prob=.65,uncertainty_pp=3,
             grade='B',sanity='OK',conservative_ev_roi=.05,v3_parlay_eligible=True,sport_key='x'),
        dict(event_id='2',display_pick='B',best_odds=1.9,model_win_prob=.62,uncertainty_pp=3,
             grade='B',sanity='OK',conservative_ev_roi=.05,v3_parlay_eligible=True,sport_key='x'),
        dict(event_id='3',display_pick='C',best_odds=2.1,model_win_prob=.60,uncertainty_pp=3,
             grade='A',sanity='OK',conservative_ev_roi=.20,v3_parlay_eligible=False,sport_key='x'),
    ])
    out=optimize_parlays(df,sizes=[2])[2]
    assert len(out)==1
    assert out[0]["조합"]=="A + B"
