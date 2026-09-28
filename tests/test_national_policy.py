import unittest
import pandas as pd
from sports_ev_engine.national_policy import assess,scenario_matrices,reference_pairs
from sports_ev_engine.models.soccer_auto import price_from_matrix,score_matrix

class NationalPolicyTests(unittest.TestCase):
    def row(self,odds=3):
        return dict(best_odds=odds,consensus_prob=.45,market='h2h',home_form_matches=6,away_form_matches=6,sanity='OK',point_ev_roi=.1,lineup_confirmed=False)
    def test_positive_can_pass_without_legacy_haircut(self):
        row=self.row();r=assess(row,scenario_matrices(1.3,1.3),'home',None)
        self.assertEqual(r['selection_status'],'SCENARIO_PASS')
        self.assertGreater(r['scenario_ev_min'],0)
        self.assertFalse(r['scenario_parlay_eligible'])
        row['lineup_confirmed']=True
        self.assertTrue(assess(row,scenario_matrices(1.3,1.3),'home',None)['scenario_parlay_eligible'])
    def test_fragile_edge_and_disagreement_do_not_enter_pairs(self):
        row=self.row(2.4)
        self.assertEqual(assess(row,scenario_matrices(1.3,1.3),'home',None)['selection_status'],'SENSITIVE')
        row=self.row();row['sanity']='HIGH_DISAGREEMENT'
        self.assertEqual(assess(row,scenario_matrices(1.3,1.3),'home',None)['selection_status'],'REVIEW')
        row=self.row();row['home_form_matches']=3
        self.assertEqual(assess(row,scenario_matrices(1.3,1.3),'home',None)['selection_status'],'DATA_HOLD')
    def test_integer_and_quarter_refunds(self):
        for line in (2,2.25):
            row=self.row(2);row.update(market='totals',consensus_prob=.5)
            matrix=score_matrix(1.1,1.1)
            w,p,l=price_from_matrix(matrix,'totals','over',line)
            r=assess(row,[matrix],'over',line)
            direct=[]
            for weight in (.15,.25,.35):
                fw=weight*w+(1-weight)*.5*(1-p)
                direct.append(fw-(1-fw-p))
            self.assertAlmostEqual(r['scenario_ev_min'],min(direct))
            self.assertAlmostEqual(r['scenario_ev_max'],max(direct))
    def test_bad_odds_hold_and_distinct_pairs(self):
        self.assertEqual(assess(self.row(float('nan')),[],'home',None)['selection_status'],'DATA_HOLD')
        base=dict(scenario_parlay_eligible=True,scenario_ev_min=.02,model_win_prob=.6,best_odds=2,display_pick='x',event_id='1')
        rows=pd.DataFrame([base,dict(base),dict(base,event_id='2',scenario_parlay_eligible=False)])
        self.assertEqual(reference_pairs(rows),[])
