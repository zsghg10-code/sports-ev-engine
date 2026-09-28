from datetime import date
import json
import pandas as pd

from sports_ev_engine.daily_combo import latest_snapshots_for_kst_date, prepare_daily_candidates, best_combos


def base(**kw):
    x={
        "event_id":"e1","commence_time":"2026-09-28T16:30:00Z","home_team":"A","away_team":"B",
        "sport_family":"soccer_national","sport_key":"soccer_test","market":"h2h","selection":"A","point":None,
        "best_odds":1.80,"consensus_prob":0.54,"model_win_prob":0.61,"break_even":1/1.80,
        "ev_roi":0.098,"conservative_ev_roi":0.06,"uncertainty_pp":4.0,"v3_decision_status":"ROBUST",
        "v3_candidate":True,"robust_positive_ratio":0.92,"robust_ev_p10":0.025,"counter_case_risk":"LOW",
        "stage":"PRE-LINEUP","data_quality":"MEDIUM","recorded_at":"2026-09-28T10:00:00Z"
    }
    x.update(kw);return x


def test_kst_date_and_latest_dedupe(tmp_path):
    p=tmp_path/"pred.jsonl"
    rows=[base(recorded_at="2026-09-28T09:00:00Z",model_win_prob=0.60),base(recorded_at="2026-09-28T11:00:00Z",model_win_prob=0.63)]
    p.write_text("\n".join(json.dumps(x) for x in rows),encoding="utf-8")
    f=latest_snapshots_for_kst_date(date(2026,9,29),p)
    assert len(f)==1
    assert abs(float(f.iloc[0]["model_win_prob"])-0.63)<1e-9
    assert f.iloc[0]["kickoff_kst"].hour==1


def test_prelineup_is_kept_but_shrunk():
    pre=prepare_daily_candidates(pd.DataFrame([base(stage="PRE-LINEUP")]))
    final=prepare_daily_candidates(pd.DataFrame([base(stage="FINAL",data_quality="HIGH",lineup_confirmed=True)]))
    assert len(pre)==1 and len(final)==1
    assert bool(pre.iloc[0]["daily_provisional"]) is True
    assert final.iloc[0]["daily_adjusted_prob"] > pre.iloc[0]["daily_adjusted_prob"]
    assert final.iloc[0]["daily_quality_score"] > pre.iloc[0]["daily_quality_score"]


def test_high_counter_case_is_visible_but_not_combo_eligible():
    f=prepare_daily_candidates(pd.DataFrame([base(counter_case_risk="HIGH")]))
    assert len(f)==1
    assert bool(f.iloc[0]["daily_combo_eligible"]) is False
    assert f.iloc[0]["daily_candidate_state"]=="검토 후보"
    assert "HIGH" in f.iloc[0]["daily_gate_reason"]


def test_robust_high_uncertainty_candidate_no_longer_disappears():
    row=base(counter_case_risk="HIGH",uncertainty_pp=10.5,ev_roi=.099,conservative_ev_roi=-.20,
             robust_positive_ratio=1.0,robust_ev_p10=.045,v3_candidate=True,stage="FINAL",lineup_confirmed=True)
    f=prepare_daily_candidates(pd.DataFrame([row]))
    assert len(f)==1
    assert abs(float(f.iloc[0]["daily_original_ev"])-.099)<1e-9
    assert bool(f.iloc[0]["daily_combo_eligible"]) is False


def test_combo_never_uses_two_picks_same_event():
    rows=[base(event_id="e1",selection="A",market="h2h"),base(event_id="e1",selection="Over",market="totals",point=2.5),base(event_id="e2",home_team="C",away_team="D",selection="C")]
    c=prepare_daily_candidates(pd.DataFrame(rows))
    combos=best_combos(c,sizes=(2,),top_n=10)[2]
    assert combos
    assert all(len({leg["event_key"] for leg in x["legs"]})==2 for x in combos)


def test_cross_sport_two_leg_combo_supported():
    rows=[
        base(event_id="s1",sport_family="soccer_national",selection="A",stage="FINAL",data_quality="HIGH",lineup_confirmed=True),
        base(event_id="m1",sport_family="baseball_mlb",sport_key="baseball_mlb",home_team="Yankees",away_team="Red Sox",selection="Yankees",stage="FINAL",data_quality="HIGH",lineup_confirmed=True,best_odds=1.75,model_win_prob=0.64,consensus_prob=0.56,break_even=1/1.75),
    ]
    c=prepare_daily_candidates(pd.DataFrame(rows))
    combos=best_combos(c,sizes=(2,),top_n=3)[2]
    assert combos
    assert {x["sport_label"] for x in combos[0]["legs"]}=={"A매치","MLB"}


def test_three_leg_requires_distinct_events_and_positive_ev():
    rows=[]
    for i in range(3):
        rows.append(base(event_id=f"e{i}",home_team=f"H{i}",away_team=f"A{i}",selection=f"H{i}",stage="FINAL",data_quality="HIGH",lineup_confirmed=True,best_odds=1.8,model_win_prob=.62,consensus_prob=.55,break_even=1/1.8))
    c=prepare_daily_candidates(pd.DataFrame(rows))
    combos=best_combos(c,sizes=(3,),top_n=2)[3]
    assert combos and combos[0]["folder_count"]==3 and combos[0]["estimated_ev"]>0
