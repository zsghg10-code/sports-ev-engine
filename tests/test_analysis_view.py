import unittest
import pandas as pd
from sports_ev_engine.analysis_view import match_summary

class AnalysisViewTests(unittest.TestCase):
    def test_direction_value_and_negative_rows_are_separate(self):
        common=dict(event_id='a',home_team='A',away_team='B',market='h2h',books=3,point=None,consensus_prob=.5,break_even=.5,edge_pp=0,scenario_ev_min=-.1,scenario_ev_max=.1,lineup_confirmed=False,scenario_parlay_eligible=False,selection_reason='라인업 미확인')
        rows=pd.DataFrame([dict(common,display_pick='A-B | A',model_win_prob=.6,point_ev_roi=-.12,best_odds=1.4),dict(common,display_pick='A-B | B',model_win_prob=.25,point_ev_roi=-.02,best_odds=3.9)])
        out=match_summary(rows)
        self.assertEqual(out.iloc[0]['확률이 가장 높은 선택'],'A')
        self.assertEqual(out.iloc[0]['배당 대비 EV가 가장 높은 선택'],'B')
        self.assertEqual(out.iloc[0]['판정'],'현재 배당에서 양의 EV 없음')
        self.assertIn('미수집',out.iloc[1]['판정'])
