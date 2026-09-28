import pandas as pd
from sports_ev_engine.final_view import compact_table,event_summary,data_status,split_events


def sample():
    common=dict(event_id='e1',home_team='Germany',away_team='Greece',commence_time='2026-09-28T16:30:00Z',best_book='Pinnacle',books=4,
                uncertainty_pp=4.0,counter_case_risk='LOW',signal_coverage=.8,lineup_confirmed=True,
                deep_context_attempted=True,robust_positive_ratio=.92,robust_ev_p10=.01,
                sanity='OK',home_form_matches=6,away_form_matches=6)
    rows=[
        dict(common,market='h2h',selection='Germany',point=None,model_win_prob=.631,break_even=.606,edge_pp=2.5,ev_roi=.041,v3_decision_status='ROBUST',v3_candidate=True,v3_parlay_eligible=True,counter_case_summary=''),
        dict(common,market='h2h',selection='Draw',point=None,model_win_prob=.224,break_even=.244,edge_pp=-2.0,ev_roi=-.082,v3_decision_status='PASS',v3_candidate=False,v3_parlay_eligible=False,counter_case_summary=''),
        dict(common,market='h2h',selection='Greece',point=None,model_win_prob=.145,break_even=.182,edge_pp=-3.7,ev_roi=-.203,v3_decision_status='PASS',v3_candidate=False,v3_parlay_eligible=False,counter_case_summary=''),
        dict(common,market='totals',selection='Over',point=2.5,model_win_prob=.558,break_even=.526,edge_pp=3.2,ev_roi=.060,v3_decision_status='ROBUST',v3_candidate=True,v3_parlay_eligible=True,counter_case_summary=''),
        dict(common,market='totals',selection='Under',point=2.5,model_win_prob=.442,break_even=.513,edge_pp=-7.1,ev_roi=-.138,v3_decision_status='PASS',v3_candidate=False,v3_parlay_eligible=False,counter_case_summary=''),
    ]
    return pd.DataFrame(rows)


def test_compact_manual_style():
    t=compact_table(sample())
    assert list(t['옵션'])==['Germany 승','무','Greece 승','O2.5','U2.5']
    assert t.iloc[0]['추정확률']=='63.1%'
    assert t.iloc[0]['상태']=='ROBUST'


def test_summary_and_status():
    s=event_summary(sample())
    assert s['model_best']=='Germany 승'
    assert s['total_best']=='O2.5'
    assert '동일 경기' in s['parlay']
    assert data_status(sample())=='LINEUP CONFIRMED'
    assert 25 <= s['confidence'] <= 95


def test_split():
    x=split_events(sample())
    assert len(x)==1 and x[0][1]=='Germany vs Greece · 09/29 01:30 KST'
