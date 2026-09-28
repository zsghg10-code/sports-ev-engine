from datetime import datetime, timezone
import json
import pandas as pd

from sports_ev_engine.prediction_store import (
    record_frame, record_market_frame, settle_from_scores, evaluation,
)
from sports_ev_engine.smart_refresh import market_trigger_reasons
from sports_ev_engine import persistent_store


def _market_frame(odds=1.80, prob=.56, point=None, market='h2h', selection='A'):
    return pd.DataFrame([{
        'event_id':'e1','commence_time':'2026-09-29T10:00:00Z','home_team':'A','away_team':'B',
        'market':market,'selection':selection,'point':point,'best_book':'Pinnacle','best_odds':odds,
        'books':4,'consensus_prob':prob,'model_win_prob':.63,'push_prob':0.0,'break_even':1/odds,
        'edge_pp':7.0,'ev_roi':.13,'conservative_ev_roi':.08,'uncertainty_pp':3.0,
        'v3_decision_status':'ROBUST','robust_positive_ratio':.92,'robust_ev_p10':.02,
    }])


def test_positive_clv_is_attached_at_settlement(tmp_path):
    pred=tmp_path/'prediction_snapshots.jsonl';settled=tmp_path/'settled_predictions.jsonl';obs=tmp_path/'market_observations.jsonl'
    f=_market_frame(1.85,.54)
    assert record_frame(f,sport_key='soccer_test',sport_family='soccer',path=pred)==1
    # Use a genuinely later pre-kickoff market state that moved toward the pick.
    record_market_frame(_market_frame(1.68,.60),sport_key='soccer_test',source='worker',observed_at='2026-09-29T09:50:00Z',path=obs)
    # Prediction recorded_at is "now" in the test environment (2026-09-29); force it earlier for deterministic CLV.
    row=json.loads(pred.read_text().strip());row['recorded_at']='2026-09-29T08:00:00Z'
    pred.write_text(json.dumps(row)+'\n')
    score=[{'id':'e1','completed':True,'scores':[{'name':'A','score':'2'},{'name':'B','score':'0'}]}]
    assert settle_from_scores('soccer_test',score,pred,settled,obs)==1
    out=json.loads(settled.read_text().strip())
    assert out['closing_odds']==1.68
    assert out['clv_market_prob_pp']>0
    assert out['clv_odds_pct']>0
    rep=evaluation(settled)
    assert rep['overall']['clv_n']==1
    assert rep['overall']['avg_clv_prob_pp']>0


def test_market_move_and_line_move_trigger():
    prev=[{
        'event_id':'e1','sport_key':'baseball_mlb','market':'totals','selection':'Over','point':8.5,
        'consensus_prob':.50,'best_odds':1.95,'observed_at':'2026-09-29T07:00:00Z'
    }]
    cur=pd.DataFrame([{
        'event_id':'e1','sport_key':'baseball_mlb','commence_time':'2026-09-29T10:00:00Z',
        'market':'totals','selection':'Over','point':9.0,'consensus_prob':.53,'best_odds':1.90
    }])
    reasons=market_trigger_reasons(cur,prev,now=datetime(2026,9,29,8,30,tzinfo=timezone.utc),sport_key='baseball_mlb')
    assert any('라인 이동' in x for x in reasons)


def test_persistent_merge_prefers_local_same_id():
    remote=[{'fingerprint':'x','v':1},{'fingerprint':'y','v':2}]
    local=[{'fingerprint':'x','v':9}]
    out=persistent_store.merge(local,remote,id_field='fingerprint')
    m={x['fingerprint']:x['v'] for x in out}
    assert m=={'x':9,'y':2}

def test_total_line_clv_uses_latest_changed_line(tmp_path):
    from sports_ev_engine.prediction_store import _clv_fields
    snap={
        'event_id':'e1','sport_key':'baseball_mlb','market':'totals','selection':'Over','point':8.5,
        'best_odds':1.91,'consensus_prob':.51,'recorded_at':'2026-09-29T08:00:00Z','commence_time':'2026-09-29T10:00:00Z'
    }
    obs=[
        {'event_id':'e1','sport_key':'baseball_mlb','market':'totals','selection':'Over','point':8.5,'best_odds':1.90,'consensus_prob':.52,'observed_at':'2026-09-29T09:00:00Z'},
        {'event_id':'e1','sport_key':'baseball_mlb','market':'totals','selection':'Over','point':9.0,'best_odds':1.95,'consensus_prob':.50,'observed_at':'2026-09-29T09:55:00Z'},
    ]
    x=_clv_fields(snap,obs)
    assert x['closing_point']==9.0
    assert x['clv_line_points']==0.5
    assert x['clv_market_prob_pp'] is None
