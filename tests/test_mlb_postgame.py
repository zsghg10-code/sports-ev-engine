from sports_ev_engine.providers.mlb_postgame import classify_postgame


def base_snapshot(market="h2h",selection="New York Yankees",point=None):
    return {
        "market":market,"selection":selection,"point":point,
        "home_team":"New York Yankees","away_team":"Boston Red Sox",
        "settle_win":0,"settle_push":0,"settle_loss":1,
        "home_score":3,"away_score":5,
        "v3_decision_status":"ROBUST","robust_positive_ratio":0.9,
        "home_starter_expected_ip":5.8,"home_starter_recent_bb_pct":0.074,
    }


def ev(innings, home_starter=None, away_starter=None, home_bp=None, away_bp=None, home_stats=None, away_stats=None, extra=False):
    return {
        "innings":innings,"extra_innings":extra,
        "home_starter":home_starter or {"ip":6,"runs":2,"bb_pct":.07},
        "away_starter":away_starter or {"ip":6,"runs":2,"bb_pct":.07},
        "home_bullpen":home_bp or {"runs":0},"away_bullpen":away_bp or {"runs":0},
        "home_team_stats":home_stats or {"runs":3,"hits":8,"walks":3,"left_on_base":7},
        "away_team_stats":away_stats or {"runs":5,"hits":9,"walks":3,"left_on_base":7},
    }


def rows(vals):
    h=a=0;out=[]
    for i,(hr,ar) in enumerate(vals,1):
        h+=hr;a+=ar;out.append({"inning":i,"home_runs":hr,"away_runs":ar,"cum_home":h,"cum_away":a,"cum_total":h+a})
    return out


def test_side_early_starter_collapse_is_model_miss_candidate():
    s=base_snapshot()
    e=ev(rows([(0,0),(0,4),(0,1),(1,0),(0,0),(0,0),(1,0),(1,0),(0,0)]),home_starter={"ip":2.1,"runs":5,"bb_pct":.158})
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="MODEL_OVERCONFIDENCE_STARTER_COLLAPSE"
    assert c["model_miss_candidate"] is True
    assert "5.8" in c["postgame_reason"]


def test_side_late_lead_loss_is_good_pick_tail():
    s=base_snapshot()
    r=rows([(1,0),(0,0),(0,1),(1,0),(0,0),(0,0),(1,0),(0,3),(0,1)])
    e=ev(r,home_bp={"runs":4})
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="GOOD_PICK_LATE_REVERSAL"
    assert c["tail_event"] is True


def test_under_late_cross_is_tail_loss():
    s=base_snapshot("totals","Under",8.5)
    s.update(home_score=5,away_score=5)
    r=rows([(1,0),(0,1),(1,0),(0,0),(1,0),(0,1),(0,1),(2,1),(1,1)]) # after7=5, late cross
    e=ev(r)
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="GOOD_PICK_LATE_TOTAL_TAIL"
    assert c["tail_event"] is True


def test_under_early_cross_is_direction_miss():
    s=base_snapshot("totals","Under",8.5)
    s.update(home_score=6,away_score=5)
    r=rows([(2,1),(1,1),(1,0),(0,1),(1,0),(0,1),(1,1),(0,0),(0,0)])
    e=ev(r,home_starter={"ip":5,"runs":4},away_starter={"ip":5,"runs":4})
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="MODEL_MISS_TOTAL_DIRECTION"
    assert c["model_miss_candidate"] is True


def test_over_low_seventh_is_direction_miss():
    s=base_snapshot("totals","Over",9.5)
    s.update(home_score=2,away_score=2)
    r=rows([(0,0),(1,0),(0,0),(0,1),(0,0),(1,0),(0,0),(0,1),(0,1)])
    e=ev(r)
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="MODEL_MISS_TOTAL_DIRECTION"


def test_over_high_traffic_low_runs_is_sequencing_candidate():
    s=base_snapshot("totals","Over",8.5)
    s.update(home_score=3,away_score=4)
    r=rows([(1,1),(0,0),(0,1),(1,0),(0,0),(1,1),(0,1),(0,0),(0,0)])
    e=ev(r,home_stats={"runs":3,"hits":10,"walks":3,"left_on_base":8},away_stats={"runs":4,"hits":9,"walks":2,"left_on_base":8})
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="GOOD_PICK_RUN_SEQUENCING_LOSS"


def test_hit_is_model_confirmation():
    s=base_snapshot("totals","Under",8.5)
    s.update(settle_win=1,settle_loss=0,home_score=2,away_score=3)
    e=ev(rows([(0,0),(1,0),(0,1),(0,0),(0,0),(1,0),(0,1),(0,0),(0,0)]))
    c=classify_postgame(s,e)
    assert c["postgame_class"]=="MODEL_CONFIRMATION"


def test_analyze_settled_mlb_appends_once(tmp_path):
    from sports_ev_engine.providers.mlb_postgame import analyze_settled_mlb, postgame_reviews
    import json
    settled=tmp_path/'settled.jsonl'; review=tmp_path/'reviews.jsonl'
    snap=base_snapshot()
    snap.update({
        "fingerprint":"fp1","sport_key":"baseball_mlb","event_id":"odds1","commence_time":"2026-09-29T00:00:00Z",
        "best_odds":1.8,"model_win_prob":.62,"break_even":.5556,"edge_pp":6.4,"ev_roi":.116,
    })
    settled.write_text(json.dumps(snap)+"\n",encoding='utf-8')
    class P:
        def match_game(self,s):return {"gamePk":123}
        def game_evidence(self,gp):
            return ev(rows([(1,0),(0,0),(0,1),(1,0),(0,0),(0,0),(1,0),(0,3),(0,1)]),home_bp={"runs":4})
    r=analyze_settled_mlb(settled,review,provider=P())
    assert r["reviewed"]==1
    assert analyze_settled_mlb(settled,review,provider=P())["reviewed"]==0
    out=postgame_reviews(review)
    assert len(out)==1 and out[0]["postgame_class"]=="GOOD_PICK_LATE_REVERSAL"
