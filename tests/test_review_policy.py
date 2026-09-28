import unittest
import pandas as pd
from sports_ev_engine.review_policy import candidate_mask,review_mask
from sports_ev_engine.core.parlay import optimize_parlays
from sports_ev_engine.auto_soccer import analyze_event
from sports_ev_engine.free_national import prepare_history,free_pool
from test_free_national import history

class ReviewTests(unittest.TestCase):
    def test_review_is_not_candidate_or_parlay(self):
        rows=pd.DataFrame([dict(grade=g,sanity=s,conservative_ev_roi=.10,event_id=str(i),best_odds=2,model_win_prob=.6,uncertainty_pp=3,display_pick=str(i)) for i,(g,s) in enumerate([('REVIEW','OUTLIER_SHRUNK'),('B','HIGH_DISAGREEMENT'),('C','OK'),('B','CHECK')])])
        self.assertEqual(candidate_mask(rows).tolist(),[False,False,True,True])
        self.assertEqual(review_mask(rows).tolist(),[True,True,False,False])
        combos=optimize_parlays(rows,sizes=[2])[2]
        self.assertEqual(len(combos),1);self.assertEqual(combos[0]['조합'],'2 + 3')
    def test_future_and_extra_time_do_not_change_model(self):
        kick='2026-09-29T01:00:00+09:00'
        records,_=prepare_history(history(pd.Timestamp(kick).date()))
        pool=free_pool(records,'Turkey','Italy',kick)
        rows=pd.DataFrame([dict(event_id='test',home_team='Turkey',away_team='Italy',commence_time=kick,market='h2h',selection='Turkey',point=None,consensus_prob=.45,best_odds=2.2,books=2,best_book='TEST')])
        before,_=analyze_event(rows,pool)
        import copy
        fx=copy.deepcopy(pool['fixtures'][0]);fx['goals']={'home':0,'away':30}
        fx['fixture']['timestamp']=int(pd.Timestamp(kick).timestamp())+100
        pool['fixtures'].append(fx)
        aet=copy.deepcopy(fx);aet['fixture']['timestamp']-=10000;aet['fixture']['status']['short']='AET'
        pool['fixtures'].append(aet)
        pool['elo']={'turkiye':10,'italy':9999}
        after,_=analyze_event(rows,pool)
        self.assertEqual(before.iloc[0].home_elo,after.iloc[0].home_elo)
        self.assertEqual(before.iloc[0].model_win_prob,after.iloc[0].model_win_prob)

if __name__=='__main__':unittest.main()
