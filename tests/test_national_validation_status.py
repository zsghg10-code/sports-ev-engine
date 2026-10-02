import unittest
from datetime import datetime, timezone
import pandas as pd
from sports_ev_engine.national_policy import finalize_national
from sports_ev_engine.validation import validation_summary, load_validation_report
from sports_ev_engine.final_view import compact_table
from sports_ev_engine.daily_combo import prepare_daily_candidates

class NationalValidationStatusTests(unittest.TestCase):
    def row(self):
        return dict(selection_status='SCENARIO_PASS',v3_decision_status='ROBUST',point_ev_roi=.2,
                    scenario_ev_min=.1,lineup_confirmed=True,injury_available=True,
                    home_xg_for=1.2,home_xg_against=.8,away_xg_for=1,away_xg_against=1,
                    xg_samples_home=3,xg_samples_away=4,xg_collection_status='OK',
                    home_rest_days=4,away_rest_days=3,data_checked_at='2026-10-02T14:00:00Z',
                    xg_checked_at='2026-10-02T14:00:00Z',counter_case_risk='LOW')
    def final(self,row): return finalize_national(row,now='2026-10-02T14:10:00Z')
    def test_auto_promotion_without_model_profitability(self):
        row=self.row();row['lineup_confirmed']=False
        self.assertEqual(self.final(row)['national_status_label'],'잠정 후보')
        row['lineup_confirmed']=True
        out=self.final(row)
        self.assertEqual(out['national_status_label'],'최종 후보')
        self.assertTrue(out['v3_parlay_eligible'])
        self.assertEqual(out['model_validation_status'],'장기 수익성 미검증')
    def test_every_missing_signal_blocks_promotion(self):
        for key,value in [('lineup_confirmed','False'),('injury_available',False),('home_xg_for',float('nan')),
                          ('xg_samples_home',2),('home_rest_days',None),('data_checked_at','bad'),
                          ('xg_checked_at','2026-10-01T14:00:00Z')]:
            row=self.row();row[key]=value
            out=self.final(row)
            self.assertFalse(out['match_data_verified'],key)
            self.assertFalse(out['v3_parlay_eligible'],key)
    def test_four_states_and_adaptive_gate(self):
        for changes,status in [({'point_ev_roi':-.1},'NO_BET'),({'selection_status':'SENSITIVE'},'COMBO_EXCLUDE'),
                               ({'adaptive_gate':'MODEL_CONFLICT_REVIEW'},'COMBO_EXCLUDE'),
                               ({'counter_case_risk':'HIGH'},'COMBO_EXCLUDE')]:
            row=self.row();row.update(changes)
            self.assertEqual(self.final(row)['national_status'],status)
    def test_actual_validation_evidence(self):
        report=dict(split_date='2026-01-01',test_n=100,holdout_before={'brier':.3,'log_loss':.8},bins=[{'n':100}])
        self.assertEqual(validation_summary(report)['completed_count'],1)
        self.assertFalse(validation_summary(report)['profitability_validated'])
        report.update(market_blend_validated=True,roi_validated=True,roi=.03,roi_n=100)
        self.assertEqual(validation_summary(report)['completed_count'],2)
        self.assertEqual(validation_summary(load_validation_report('/missing'))['completed_count'],0)
        report['test_n']=95
        self.assertEqual(validation_summary(report)['completed_count'],0)

    def test_daily_national_provisional_cannot_enter_combo(self):
        row=self.row()
        row.update(sport_family='soccer_national',event_id='x',home_team='A',away_team='B',
                   market='h2h',selection='A',best_odds=2,model_win_prob=.6,consensus_prob=.5,
                   break_even=.5,robust_positive_ratio=1,robust_ev_p10=.1,v3_candidate=True,
                   commence_time='2026-10-02T18:00:00Z',stage='FINAL')
        now=pd.Timestamp.now(tz='UTC').isoformat()
        row.update(data_checked_at=now,xg_checked_at=now)
        complete=prepare_daily_candidates(pd.DataFrame([row]))
        self.assertTrue(complete.iloc[0].daily_combo_eligible)
        row['injury_available']=False
        incomplete=prepare_daily_candidates(pd.DataFrame([row]))
        self.assertEqual(incomplete.iloc[0].daily_candidate_state,'잠정 후보')
        self.assertFalse(incomplete.iloc[0].daily_combo_eligible)
        # Old snapshots with only ROBUST cannot bypass the new data gate.
        row.pop('selection_status')
        legacy=prepare_daily_candidates(pd.DataFrame([row]))
        self.assertFalse(legacy.iloc[0].daily_combo_eligible)

    def test_compact_table_uses_korean_status(self):
        row=self.final(self.row())
        row.update(market='h2h',selection='A',home_team='A',away_team='B',best_odds=2,
                   model_win_prob=.6,break_even=.5,edge_pp=10,ev_roi=.2,uncertainty_pp=4)
        table=compact_table(pd.DataFrame([row]))
        self.assertEqual(table.iloc[0]['상태'],'최종 후보')

    def test_mixed_baseball_row_is_not_nationalized(self):
        now=pd.Timestamp.now(tz='UTC').isoformat()
        row=self.row();row.update(data_checked_at=now,xg_checked_at=now,sport_family='soccer_national',
            event_id='n',market='h2h',selection='A',home_team='A',away_team='B',best_odds=2,
            model_win_prob=.6,consensus_prob=.5,break_even=.5,robust_positive_ratio=1,robust_ev_p10=.1)
        national=self.final(row)
        baseball=dict(sport_family='baseball_mlb',event_id='b',market='h2h',selection='C',
            home_team='C',away_team='D',best_odds=2,model_win_prob=.6,consensus_prob=.5,
            break_even=.5,ev_roi=.2,v3_decision_status='ROBUST',v3_candidate=True,
            stage='FINAL',lineup_confirmed=True,counter_case_risk='LOW')
        daily=prepare_daily_candidates(pd.DataFrame([national,baseball]))
        b=daily[daily.event_id.eq('b')].iloc[0]
        self.assertTrue(b.daily_combo_eligible)
        self.assertEqual(b.daily_candidate_state,'조합 가능')
