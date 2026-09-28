import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import pandas as pd
from sports_ev_engine.free_national import *
from sports_ev_engine.auto_soccer import analyze_event


def history(day):
    return pd.DataFrame([dict(date=str(day-timedelta(days=n*7)),home_team='Turkey',away_team='Italy',home_score=2,away_score=1,tournament='TEST',neutral=False,score_basis=90,home_xg=2.5,away_xg=.5,source='SYNTHETIC TEST') for n in range(1,7)])

class FreeTests(unittest.TestCase):
    def setUp(self):
        self.kick='2026-09-29T01:00:00+09:00'
        self.rows,_=prepare_history(history(pd.Timestamp(self.kick).date()))
    def test_history_validation_and_override(self):
        f=history(pd.Timestamp(self.kick).date()); f.loc[0,'home_score']=-1
        with self.assertRaises(ValueError):prepare_history(f)
        changed=dict(self.rows[0],hg=3)
        self.assertEqual(combine_history(self.rows,[changed])[0]['hg'],3)
        f=history(pd.Timestamp(self.kick).date());f.loc[0,'date']=''
        with self.assertRaises(ValueError):prepare_history(f)
    def test_no_same_day_or_future_and_stale_guard(self):
        extra=dict(self.rows[0],date=pd.Timestamp(self.kick).date(),hg=40)
        p=free_pool(self.rows+[extra],'Turkey','Italy',self.kick)
        self.assertEqual(len(p['fixtures']),6)
        with self.assertRaises(ValueError):free_pool(self.rows,'Turkey','Italy','2027-09-29T01:00:00+09:00')
    def test_xg_absence_lineup(self):
        p=free_pool(self.rows,'Turkey','Italy',self.kick)
        h,a,ok,_=adjust_lambdas(1.5,1.5,p)
        self.assertGreater(h,1.5);self.assertLess(a,1.5);self.assertFalse(ok)
        p['manual_context']={'home':{'attack':-20,'confirmed':True},'away':{'confirmed':True}}
        h2,a2,ok,_=adjust_lambdas(1.5,1.5,p)
        self.assertAlmostEqual(h2,h*.8);self.assertEqual(a2,a);self.assertTrue(ok)
    def test_event_evidence(self):
        f=pd.DataFrame([dict(kickoff=self.kick,home_team='Turkey',away_team='Italy',team=t,lineup_confirmed=True,lineup_players=';'.join(str(i) for i in range(11)),missing_players='',attack_change_pct=0,defense_change_pct=0,neutral=True,source='TEST',checked_at='2026-09-28T20:00:00+09:00') for t in ['Turkey','Italy']])
        self.assertEqual(len(event_context(f,'Turkey','Italy',self.kick,now='2026-09-28T21:00:00+09:00')),2)
        f.loc[0,'lineup_players']='one'
        with self.assertRaises(ValueError):event_context(f,'Turkey','Italy',self.kick,now='2026-09-28T21:00:00+09:00')
    def test_model_and_club_regression(self):
        p=free_pool(self.rows,'Turkey','Italy',self.kick)
        odds=pd.DataFrame([dict(event_id='test',home_team='Turkey',away_team='Italy',commence_time=self.kick,market='h2h',selection=t,point=None,consensus_prob=prob,best_odds=1/prob,books=2,best_book='TEST') for t,prob in [('Turkey',.45),('Draw',.3),('Italy',.25)]])
        out,_=analyze_event(odds,p)
        self.assertEqual(len(out),3);self.assertFalse(out.parlay_eligible.any())
        for _,r in out.iterrows():self.assertAlmostEqual(r.model_win_prob+r.push_prob+r.model_lose_prob,1)
        p['international']=False
        out,_=analyze_event(odds,p);self.assertEqual(len(out),3)
    def test_app_free_without_football_key(self):
        from streamlit.testing.v1 import AppTest
        now=datetime.now(timezone.utc);kick=now+timedelta(days=1)
        rows,_=prepare_history(history(kick.date()))
        for row in rows:row['source']='https://www.espn.com/soccer/match/_/gameId/TEST'
        events=[dict(id='test',home_team='Turkey',away_team='Italy',commence_time=kick.isoformat(),bookmakers=[dict(key=b,title=b,markets=[dict(key='h2h',outcomes=[dict(name='Turkey',price=2.2),dict(name='Draw',price=3.2),dict(name='Italy',price=3.4)])]) for b in ['one','two']])]
        catalog=[dict(key='soccer_uefa_nations_league',title='UEFA Nations League',active=True,description='TEST')]
        with patch.dict(os.environ,{'THE_ODDS_API_KEY':'test','API_FOOTBALL_KEY':''}),patch('sports_ev_engine.providers.the_odds_api.TheOddsAPI.sports',return_value=catalog),patch('sports_ev_engine.providers.the_odds_api.TheOddsAPI.odds',return_value=(events,{})),patch('sports_ev_engine.free_national.download_history',return_value=(rows,0)),patch('sports_ev_engine.auto_national.collect',return_value=(rows,[],[])),patch('sports_ev_engine.providers.api_football.APIFootball.__init__',side_effect=AssertionError('Free mode called paid provider')):
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.selectbox(key="club_competition_v291").options,[])
            button=next(b for b in at.button if b.label=='🌍 선택 대회 A매치 자동분석')
            button.click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['national_failures'],[])
            self.assertEqual(len(at.session_state['national_ranked']),3)
            result=at.session_state['national_ranked']
            self.assertTrue((result.scenario_count==27).all())
            self.assertTrue(result.selection_reason.str.len().gt(0).all())
            self.assertTrue(any('A매치 후보' in m.value for m in at.markdown))
            # Populate the positive-candidate branch too, without provider traffic.
            result=result.copy()
            result['scenario_candidate']=True
            result['selection_status']='SCENARIO_PASS'
            at.session_state['national_ranked']=result
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any('가정 최저 EV(%)' in d.value.columns for d in at.dataframe))

if __name__=='__main__':unittest.main()
