import json
from pathlib import Path
import pandas as pd

from sports_ev_engine.prediction_store import record_frame, settle_from_scores, evaluation


def _frame():
    return pd.DataFrame([
        dict(event_id='e1',commence_time='2026-09-28T12:00:00Z',home_team='Germany',away_team='Greece',
             market='h2h',selection='Germany',point=None,best_book='Pinnacle',best_odds=1.80,books=4,
             consensus_prob=.56,raw_independent_prob=.66,model_win_prob=.63,push_prob=0.0,
             break_even=1/1.8,edge_pp=7.4,ev_roi=.134,conservative_ev_roi=.08,uncertainty_pp=3.0,
             grade='B',sanity='OK',v3_decision_status='ROBUST',robust_positive_ratio=.93,
             robust_ev_min=.01,robust_ev_p10=.03,robust_ev_max=.20,v3_candidate=True,v3_parlay_eligible=True),
    ])


def test_append_only_dedup_and_settlement(tmp_path):
    pred=tmp_path/'pred.jsonl'; settled=tmp_path/'settled.jsonl'
    f=_frame()
    assert record_frame(f,sport_key='soccer_test',sport_family='soccer',path=pred)==1
    assert record_frame(f,sport_key='soccer_test',sport_family='soccer',path=pred)==0
    score=[{'id':'e1','completed':True,'scores':[{'name':'Germany','score':'2'},{'name':'Greece','score':'0'}]}]
    assert settle_from_scores('soccer_test',score,pred,settled)==1
    assert settle_from_scores('soccer_test',score,pred,settled)==0
    rep=evaluation(settled)
    assert rep['n']==1 and rep['roi_n']==1
    assert rep['overall']['brier']>=0
    assert rep['overall']['roi']>.79


def test_quarter_push_keeps_roi_but_not_binary_calibration(tmp_path):
    pred=tmp_path/'pred.jsonl'; settled=tmp_path/'settled.jsonl'
    f=pd.DataFrame([dict(event_id='e2',home_team='A',away_team='B',market='totals',selection='Under',point=2.25,
                         best_odds=2.0,model_win_prob=.50,push_prob=.10,v3_decision_status='ROBUST')])
    record_frame(f,sport_key='soccer_test',sport_family='soccer',path=pred)
    # 2 goals on U2.25 => half win / half push => +0.5 ROI at 2.00
    score=[{'id':'e2','completed':True,'scores':[{'name':'A','score':'1'},{'name':'B','score':'1'}]}]
    assert settle_from_scores('soccer_test',score,pred,settled)==1
    rep=evaluation(settled)
    assert rep['n']==0
    assert rep['roi_n']==1
    assert abs(rep['overall']['roi']-.5)<1e-12


def test_mlb_pregame_baselines_are_persisted(tmp_path):
    pred=tmp_path/'pred.jsonl'
    f=pd.DataFrame([dict(event_id='m1',home_team='NYY',away_team='BOS',market='h2h',selection='NYY',point=None,
                         best_odds=1.8,model_win_prob=.62,push_prob=0,v3_decision_status='ROBUST',
                         home_starter_expected_ip=5.8,away_starter_expected_ip=5.4,
                         home_starter_recent_bb_pct=.074,away_starter_recent_bb_pct=.091,
                         home_bullpen_pitches_last3=102,away_bullpen_pitches_last3=81)])
    assert record_frame(f,sport_key='baseball_mlb',sport_family='baseball_mlb',path=pred)==1
    row=json.loads(pred.read_text(encoding='utf-8').strip())
    assert row['home_starter_expected_ip']==5.8
    assert row['home_starter_recent_bb_pct']==.074
    assert row['home_bullpen_pitches_last3']==102
