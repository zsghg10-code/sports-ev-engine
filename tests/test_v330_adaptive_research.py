import unittest
from unittest.mock import patch
from datetime import date
import pandas as pd

from sports_ev_engine.adaptive_model import calibration_curve, apply_adaptive_layer
from sports_ev_engine.correlation_engine import correlation_adjusted_hit
from sports_ev_engine.change_log import change_logs
from sports_ev_engine.postgame_stats import failure_statistics
from sports_ev_engine.replay_backtest import replay_snapshots, version_backtest


class AdaptiveResearchTests(unittest.TestCase):
    def _settled(self,n=60,fam="baseball_mlb",market="h2h"):
        rows=[]
        for i in range(n):
            p=[.30,.40,.50,.60,.70][i%5]
            # deliberately overconfident: 60% bucket wins roughly half
            y=1.0 if i%4 in (0,1) else 0.0
            rows.append({
                "event_id":f"e{i}","selection":"A","point":None,"sport_family":fam,"market":market,
                "pre_adaptive_model_win_prob":p,"model_win_prob":p,"push_prob":0,
                "settle_win":y,"settle_loss":1-y,"settle_push":0,"recorded_at":f"2026-08-{(i%28)+1:02d}T00:00:00Z",
                "commence_time":f"2026-08-{(i%28)+1:02d}T10:00:00Z","v3_candidate":True,"realized_roi":.8 if y else -1,
                "model_version":"3.3.0"
            })
        return rows

    def test_calibration_waits_for_sample(self):
        c=calibration_curve(self._settled(20),"baseball_mlb","h2h")
        self.assertFalse(c["active"])
        self.assertEqual(c["n"],20)

    def test_calibration_activates(self):
        c=calibration_curve(self._settled(80),"baseball_mlb","h2h")
        self.assertTrue(c["active"])
        self.assertGreaterEqual(len(c["points"]),3)

    def test_calibration_dedupes_refresh_snapshots(self):
        base=self._settled(50)
        dup=[]
        for r in base:
            dup.append(r)
            x=dict(r);x["recorded_at"]="2026-09-01T00:00:00Z";dup.append(x)
        c=calibration_curve(dup,"baseball_mlb","h2h")
        self.assertEqual(c["n"],50)

    def test_adaptive_market_coherence(self):
        f=pd.DataFrame([
            {"event_id":"x","market":"h2h","selection":"H","home_team":"H","away_team":"A","best_odds":2.0,"consensus_prob":.49,"raw_independent_prob":.58,"model_win_prob":.54,"push_prob":0,"uncertainty_pp":2,"v3_candidate":True,"v3_parlay_eligible":True},
            {"event_id":"x","market":"h2h","selection":"A","home_team":"H","away_team":"A","best_odds":2.0,"consensus_prob":.51,"raw_independent_prob":.42,"model_win_prob":.46,"push_prob":0,"uncertainty_pp":2,"v3_candidate":True,"v3_parlay_eligible":True},
        ])
        out=apply_adaptive_layer(f,"baseball_mlb",settled_rows=[])
        self.assertAlmostEqual(float(out.model_win_prob.sum()),1.0,places=8)
        self.assertIn("ensemble_summary",out.columns)

    def test_correlation_adjusts_when_history_exists(self):
        rows=[]
        for d in range(1,26):
            y=1.0 if d%2==0 else 0.0
            for fam,m in [("baseball_mlb","h2h"),("soccer_club","h2h")]:
                rows.append({"event_id":f"{fam}{d}","market":m,"selection":"x","point":None,"sport_family":fam,
                             "v3_candidate":True,"settle_push":0,"settle_win":y,"recorded_at":f"2026-08-{d:02d}T00:00:00Z",
                             "commence_time":f"2026-08-{d:02d}T10:00:00Z"})
        legs=[{"sport_family":"baseball_mlb","market":"h2h","daily_adjusted_prob":.6},
              {"sport_family":"soccer_club","market":"h2h","daily_adjusted_prob":.6}]
        hit,meta=correlation_adjusted_hit(legs,rows)
        self.assertGreater(meta["pairs_used"],0)
        self.assertNotAlmostEqual(hit,.36,places=6)

    def test_change_log_explains_market_and_lineup(self):
        rows=[
            {"event_id":"x","sport_key":"s","market":"h2h","selection":"H","point":None,"recorded_at":"2026-09-01T01:00:00Z","model_win_prob":.60,"consensus_prob":.55,"stage":"PRE-LINEUP","lineup_confirmed":False,"signal_coverage":.5},
            {"event_id":"x","sport_key":"s","market":"h2h","selection":"H","point":None,"recorded_at":"2026-09-01T02:00:00Z","model_win_prob":.64,"consensus_prob":.57,"stage":"FINAL","lineup_confirmed":True,"signal_coverage":.8},
        ]
        logs=change_logs(rows)
        self.assertEqual(len(logs),1)
        self.assertIn("라인업",logs[0]["reason"])
        self.assertIn("시장",logs[0]["reason"])

    def test_failure_stats_flags_repeat(self):
        reviews=[]
        for i in range(10):
            reviews.append({"event_id":f"e{i}","market":"totals","selection":"Under","point":8.5,"settle_loss":1,
                            "postgame_class_ko":"GOOD PICK + 후반 불펜 붕괴","reviewed_at":f"2026-09-{i+1:02d}T00:00:00Z"})
        rep=failure_statistics(reviews,min_flag_n=5)
        self.assertEqual(rep["losses"],10)
        self.assertTrue(rep["hints"])

    def test_replay_no_lookahead_cutoff(self):
        preds=[
            {"event_id":"x","sport_key":"baseball_mlb","sport_family":"baseball_mlb","market":"h2h","selection":"H","point":None,
             "commence_time":"2026-09-29T10:00:00Z","recorded_at":"2026-09-29T08:00:00Z","model_version":"3.3.0"},
            {"event_id":"x","sport_key":"baseball_mlb","sport_family":"baseball_mlb","market":"h2h","selection":"H","point":None,
             "commence_time":"2026-09-29T10:00:00Z","recorded_at":"2026-09-29T09:50:00Z","model_version":"3.3.0"},
        ]
        with patch("sports_ev_engine.replay_backtest.load_predictions",return_value=preds):
            out=replay_snapshots(date(2026,9,29),30,"3.3.0")
        self.assertEqual(len(out),1)
        self.assertEqual(out.iloc[0]["recorded_at"],"2026-09-29T08:00:00Z")

    def test_version_backtest_dedupes_refresh(self):
        r=self._settled(5)
        dup=[]
        for x in r:
            dup.extend([x,{**x,"recorded_at":"2026-09-02T00:00:00Z"}])
        with patch("sports_ev_engine.replay_backtest.load_settled",return_value=dup):
            out=version_backtest(date(2026,8,1),date(2026,9,30))
        self.assertEqual(out[0]["n"],5)


if __name__=="__main__":unittest.main()
