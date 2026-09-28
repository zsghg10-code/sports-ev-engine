import unittest
from unittest.mock import patch
from datetime import date,timedelta
import pandas as pd
from sports_ev_engine.validation import temperature,metrics,validate
from sports_ev_engine.auto_soccer import analyze_event
from sports_ev_engine.free_national import prepare_history,free_pool
from test_free_national import history

class ValidationTests(unittest.TestCase):
    def test_temperature_and_loss(self):
        p=temperature([.7,.2,.1],1.5)
        self.assertAlmostEqual(sum(p),1);self.assertLess(p[0],.7)
        self.assertLess(metrics([{'p':[.8,.1,.1],'y':0}])['log_loss'],metrics([{'p':[.1,.8,.1],'y':0}])['log_loss'])
    def test_future_never_used(self):
        from sports_ev_engine.validation import build_elo as real
        rows=[dict(date=date(2025,1,1)+timedelta(days=i),home='A',away='B',hg=1,ag=0,neutral=True,source='TEST') for i in range(20)]
        rows.append(dict(rows[0],date=date(2027,1,1),hg=35))
        with patch('sports_ev_engine.validation.build_elo',wraps=real) as mocked:
            report,predictions=validate(rows,'2026-01-01')
            self.assertEqual(report['status'],'표본 부족')
            self.assertEqual(report['live_temperature'],1)
            self.assertTrue(all(x['date']<'2026-01-01' for x in predictions))
            self.assertTrue(all(f['goals']['home']!=35 for call in mocked.call_args_list for f in call.args[0]))
    def test_shared_market_weight_probability_sum(self):
        kick='2026-09-29T01:00:00+09:00';r,_=prepare_history(history(pd.Timestamp(kick).date()));pool=free_pool(r,'Turkey','Italy',kick)
        frame=pd.DataFrame([dict(event_id='x',home_team='Turkey',away_team='Italy',commence_time=kick,market='h2h',selection=team,consensus_prob=p,best_odds=1/p,books=3,point=None) for team,p in [('Turkey',.02),('Draw',.28),('Italy',.70)]])
        out,_=analyze_event(frame,pool)
        self.assertAlmostEqual(out.model_win_prob.sum(),1)
        self.assertEqual(out.model_weight.nunique(),1)
        self.assertTrue((out.point_ev_roi==out.ev_roi).all())

if __name__=='__main__':unittest.main()
