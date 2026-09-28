"""MLB post-game evidence collector and objective review classifier.

The classifier deliberately distinguishes between:
- objectively identifiable late/tail events,
- identifiable pregame-model miss candidates (e.g. backed starter collapses early), and
- unresolved variance/review cases.

It never upgrades a losing pick to GOOD_PICK solely because the pregame EV was positive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json, math, re
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .mlb_statsapi import schedule_kst, match_schedule
from ..prediction_store import load_settled
from .. import persistent_store

BASE="https://statsapi.mlb.com/api/v1"
LIVE_BASE="https://statsapi.mlb.com/api/v1.1"
KST=ZoneInfo("Asia/Seoul")


def _num(v):
    try:
        if v is None or v=="": return None
        return float(v)
    except (TypeError,ValueError): return None


def _ip(v):
    """Convert baseball innings notation (5.2 == 5 + 2/3) to decimal innings."""
    if v is None:return None
    try:
        s=str(v)
        if "." not in s:return float(s)
        a,b=s.split(".",1)
        outs=int(b[:1] or 0)
        return int(a)+outs/3
    except Exception:return None


def _pct(x):
    x=_num(x)
    return None if x is None else x*100


def _safe_mean(vals):
    vals=[_num(x) for x in vals]
    vals=[x for x in vals if x is not None]
    return sum(vals)/len(vals) if vals else None


def _load_jsonl(path):
    p=Path(path)
    if not p.exists():return []
    out=[]
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:out.append(json.loads(line))
            except Exception:pass
    return out


def _append_jsonl(path,rows):
    if not rows:return
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("a",encoding="utf-8") as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False,sort_keys=True,default=str)+"\n")
    persistent_store.mirror("postgame_review", rows, id_field="fingerprint")


def _side_for_selection(snapshot):
    sel=str(snapshot.get("selection") or "")
    home=str(snapshot.get("home_team") or "")
    away=str(snapshot.get("away_team") or "")
    if sel==home:return "home"
    if sel==away:return "away"
    return None


def _is_over(snapshot):return "over" in str(snapshot.get("selection") or "").lower()
def _is_under(snapshot):return "under" in str(snapshot.get("selection") or "").lower()


def _cumulative_innings(innings):
    home=away=0
    rows=[]
    for inn in innings or []:
        n=int(inn.get("num") or len(rows)+1)
        hr=int(((inn.get("home") or {}).get("runs") or 0))
        ar=int(((inn.get("away") or {}).get("runs") or 0))
        home+=hr;away+=ar
        rows.append({"inning":n,"home_runs":hr,"away_runs":ar,"cum_home":home,"cum_away":away,"cum_total":home+away})
    return rows


def _lead_state(rows,side,inning):
    r=next((x for x in rows if x["inning"]==inning),None)
    if not r:return None
    a=r["cum_home"] if side=="home" else r["cum_away"]
    b=r["cum_away"] if side=="home" else r["cum_home"]
    return a-b


def _cross_inning(rows,point,direction="over"):
    p=_num(point)
    if p is None:return None
    for r in rows:
        t=r["cum_total"]
        if direction=="over" and t>p:return r["inning"]
        if direction=="under" and t>=p:return r["inning"]
    return None


def _starter_box(side_box):
    pids=side_box.get("pitchers") or []
    players=side_box.get("players") or {}
    if not pids:return {}
    pid=pids[0]
    p=players.get(f"ID{pid}",{})
    st=((p.get("stats") or {}).get("pitching") or {})
    bf=_num(st.get("battersFaced"));bb=_num(st.get("baseOnBalls"));so=_num(st.get("strikeOuts"))
    return {
        "id":pid,"name":((p.get("person") or {}).get("fullName")),
        "ip":_ip(st.get("inningsPitched")),"runs":_num(st.get("runs")),"earned_runs":_num(st.get("earnedRuns")),
        "pitches":_num(st.get("numberOfPitches")) or _num(st.get("pitchesThrown")),"bf":bf,
        "bb":bb,"so":so,"bb_pct":bb/bf if bb is not None and bf else None,
        "k_pct":so/bf if so is not None and bf else None,
    }


def _bullpen_box(side_box):
    pids=side_box.get("pitchers") or []
    players=side_box.get("players") or {}
    out={"ip":0.0,"runs":0.0,"earned_runs":0.0,"pitches":0.0,"bb":0.0,"so":0.0,"pitchers":0}
    for pid in pids[1:]:
        p=players.get(f"ID{pid}",{});st=((p.get("stats") or {}).get("pitching") or {})
        ip=_ip(st.get("inningsPitched"))
        if ip is None:continue
        out["pitchers"]+=1;out["ip"]+=ip
        for k,src in (("runs","runs"),("earned_runs","earnedRuns"),("pitches","numberOfPitches"),("bb","baseOnBalls"),("so","strikeOuts")):
            v=_num(st.get(src))
            if k=="pitches" and v is None:v=_num(st.get("pitchesThrown"))
            out[k]+=v or 0
    return out


def _team_box(side_box):
    stats=side_box.get("teamStats") or {}
    bat=stats.get("batting") or {};field=stats.get("fielding") or {}
    return {
        "runs":_num(bat.get("runs")),"hits":_num(bat.get("hits")),"walks":_num(bat.get("baseOnBalls")),
        "strikeouts":_num(bat.get("strikeOuts")),"left_on_base":_num(bat.get("leftOnBase")),
        "errors":_num(field.get("errors")),
    }


class MLBPostgameProvider:
    def __init__(self,timeout=20,session=None):
        self.timeout=timeout;self.s=session or requests.Session()

    def _get(self,url):
        r=self.s.get(url,timeout=self.timeout);r.raise_for_status();return r.json()

    def game_evidence(self,game_pk):
        box=self._get(f"{BASE}/game/{int(game_pk)}/boxscore")
        try:feed=self._get(f"{LIVE_BASE}/game/{int(game_pk)}/feed/live")
        except Exception:feed={}
        live=feed.get("liveData") or {}
        lines=((live.get("linescore") or {}).get("innings") or [])
        if not lines:
            try:
                ls=self._get(f"{BASE}/game/{int(game_pk)}/linescore")
                lines=ls.get("innings") or []
            except Exception:lines=[]
        innings=_cumulative_innings(lines)
        teams=box.get("teams") or {}
        hbox=teams.get("home") or {};abox=teams.get("away") or {}
        return {
            "gamePk":int(game_pk),"innings":innings,"extra_innings":bool(innings and innings[-1]["inning"]>9),
            "home_starter":_starter_box(hbox),"away_starter":_starter_box(abox),
            "home_bullpen":_bullpen_box(hbox),"away_bullpen":_bullpen_box(abox),
            "home_team_stats":_team_box(hbox),"away_team_stats":_team_box(abox),
        }

    def match_game(self,snapshot):
        ts=pd.Timestamp(snapshot.get("commence_time"))
        if ts.tzinfo is None:ts=ts.tz_localize("UTC")
        d=ts.tz_convert(KST).date()
        # Normal case first; postponed/rescheduled games get a narrow +/-2 KST-day fallback.
        for off in (0,-1,1,-2,2):
            frame=schedule_kst(d+timedelta(days=off))
            row=match_schedule(frame,snapshot.get("home_team"),snapshot.get("away_team"),snapshot.get("commence_time"))
            if row:return row
        return None


def classify_postgame(snapshot,evidence):
    """Return an evidence-based retrospective label for one settled MLB snapshot."""
    market=str(snapshot.get("market") or "")
    w=_num(snapshot.get("settle_win")) or 0
    l=_num(snapshot.get("settle_loss")) or 0
    p=_num(snapshot.get("settle_push")) or 0
    home_score=_num(snapshot.get("home_score")) or 0
    away_score=_num(snapshot.get("away_score")) or 0
    rows=evidence.get("innings") or []
    side=_side_for_selection(snapshot)
    point=_num(snapshot.get("point"))
    decision=str(snapshot.get("v3_decision_status") or "")
    robust=_num(snapshot.get("robust_positive_ratio"))
    robust_flag=(decision=="ROBUST") or (robust is not None and robust>=.80)

    base={
        "postgame_class":"REVIEW","postgame_class_ko":"추가 검토",
        "postgame_reason":"결과만으로 사전 분석의 품질을 단정하지 않음",
        "postgame_quality":"REVIEW","tail_event":False,"model_miss_candidate":False,
    }
    if p and not l and not w:
        return {**base,"postgame_class":"PUSH","postgame_class_ko":"적특/푸시","postgame_reason":"정산 결과가 푸시라 방향성 평가에서 제외","postgame_quality":"NEUTRAL"}
    if w>l:
        reason="사전 확률 방향과 실제 결과가 일치"
        if market=="h2h" and side:
            opp="away" if side=="home" else "home"
            opp_st=evidence.get(f"{opp}_starter") or {}
            picked_stats=evidence.get(f"{side}_team_stats") or {}
            if (_num(opp_st.get("ip")) or 9)<=3.0 and (_num(opp_st.get("runs")) or 0)>=4:
                reason="상대 선발 조기붕괴가 승리 경로로 현실화"
            elif (_num(picked_stats.get("runs")) or 0)>=6:
                reason="예상한 공격 우위가 실제 득점으로 확인"
        elif market=="totals":
            total=home_score+away_score
            reason=f"예측한 {'고득점' if _is_over(snapshot) else '저득점'} 방향과 실제 총득점 {int(total)}점이 일치"
        return {**base,"postgame_class":"MODEL_CONFIRMATION","postgame_class_ko":"사전 방향 확인","postgame_reason":reason,"postgame_quality":"CONFIRMED"}

    # H2H / moneyline loss paths.
    if market=="h2h" and side:
        opp="away" if side=="home" else "home"
        st=evidence.get(f"{side}_starter") or {}
        bp=evidence.get(f"{side}_bullpen") or {}
        team=evidence.get(f"{side}_team_stats") or {}
        pred_exp_ip=_num(snapshot.get(f"{side}_starter_expected_ip"))
        pred_bb=_num(snapshot.get(f"{side}_starter_recent_bb_pct"))
        act_ip=_num(st.get("ip"));act_runs=_num(st.get("runs"));act_bb=_num(st.get("bb_pct"))
        early_collapse=(act_ip is not None and act_ip<=3.0 and (act_runs or 0)>=4)
        if early_collapse:
            parts=["베팅한 팀 선발이 3이닝 이하에서 4실점 이상으로 조기붕괴"]
            if pred_exp_ip is not None:parts.append(f"예상 최근평균 {pred_exp_ip:.1f}이닝 → 실제 {act_ip:.1f}이닝")
            if pred_bb is not None and act_bb is not None:parts.append(f"사전 최근 BB% {pred_bb*100:.1f}% → 실제 {act_bb*100:.1f}%")
            return {**base,"postgame_class":"MODEL_OVERCONFIDENCE_STARTER_COLLAPSE","postgame_class_ko":"사전 확률 과대평가 후보 · 선발 조기붕괴","postgame_reason":" · ".join(parts),"postgame_quality":"MODEL_MISS_CANDIDATE","model_miss_candidate":True}
        # Led late and lost -> objective tail / bullpen reversal.
        late_leads=[inn for inn in (7,8,9) if (_lead_state(rows,side,inn) or 0)>0]
        if late_leads:
            last=max(late_leads)
            bp_runs=_num(bp.get("runs")) or 0
            return {**base,"postgame_class":"GOOD_PICK_LATE_REVERSAL","postgame_class_ko":"GOOD PICK + 후반 역전","postgame_reason":f"{last}회 종료 시점까지 리드했지만 최종 패배"+(f" · 불펜 {bp_runs:.0f}실점" if bp_runs else ""),"postgame_quality":"GOOD_PICK_TAIL","tail_event":True}
        if evidence.get("extra_innings"):
            d9=_lead_state(rows,side,9)
            if d9==0:
                return {**base,"postgame_class":"GOOD_PICK_EXTRA_INNING_TAIL","postgame_class_ko":"GOOD PICK + 연장 승부 tail","postgame_reason":"9회 종료 동점 이후 연장에서 패배","postgame_quality":"GOOD_PICK_TAIL","tail_event":True}
        # Strong starter but almost no run support.
        if act_ip is not None and act_ip>=6.0 and (act_runs or 0)<=2 and (_num(team.get("runs")) or 0)<=2:
            return {**base,"postgame_class":"OFFENSE_UNDERPERFORMANCE","postgame_class_ko":"타선 기대이하 / 득점지원 부족","postgame_reason":f"선발 {act_ip:.1f}이닝 {(act_runs or 0):.0f}실점에도 팀 득점이 {(_num(team.get('runs')) or 0):.0f}점","postgame_quality":"VARIANCE_OR_OFFENSE_MISS"}
        return {**base,"postgame_class":"SIDE_LOSS_REVIEW","postgame_class_ko":"승패 미적중 · 원인 추가검토","postgame_reason":"조기붕괴·후반역전·연장 tail 중 명확한 단일 원인이 확인되지 않음"}

    if market=="totals" and point is not None:
        total=home_score+away_score
        last_inning=rows[-1]["inning"] if rows else 9
        after7=next((x["cum_total"] for x in rows if x["inning"]==7),None)
        after8=next((x["cum_total"] for x in rows if x["inning"]==8),None)
        hs=evidence.get("home_starter") or {};aws=evidence.get("away_starter") or {}
        starter_collapse=any(((_num(x.get("ip")) is not None and _num(x.get("ip"))<=3.0 and (_num(x.get("runs")) or 0)>=4) for x in (hs,aws)))
        bp_runs=(_num((evidence.get("home_bullpen") or {}).get("runs")) or 0)+(_num((evidence.get("away_bullpen") or {}).get("runs")) or 0)
        if _is_under(snapshot):
            # Under survived deep into the game, then crossed late.
            cross=_cross_inning(rows,point,"over")
            comfortable7=(after7 is not None and after7<=point-1.5)
            comfortable8=(after8 is not None and after8<=point-1.0)
            if evidence.get("extra_innings") and after8 is not None and after8<=point:
                return {**base,"postgame_class":"GOOD_PICK_EXTRA_INNING_TOTAL_TAIL","postgame_class_ko":"GOOD PICK + 연장 오버 tail","postgame_reason":f"정규 후반까지 U{point:g} 범위였으나 연장 득점으로 최종 {int(total)}점","postgame_quality":"GOOD_PICK_TAIL","tail_event":True}
            if cross is not None and cross>=8 and (comfortable7 or comfortable8):
                return {**base,"postgame_class":"GOOD_PICK_LATE_TOTAL_TAIL","postgame_class_ko":"GOOD PICK + 후반 대량득점 tail","postgame_reason":f"7회 {after7 if after7 is not None else '—'}점 / 8회 {after8 if after8 is not None else '—'}점에서 언더가 유지되다가 {cross}회에 라인 돌파 · 최종 {int(total)}점","postgame_quality":"GOOD_PICK_TAIL","tail_event":True}
            if starter_collapse:
                return {**base,"postgame_class":"MODEL_OVERCONFIDENCE_STARTER_COLLAPSE","postgame_class_ko":"사전 언더 확률 과대평가 후보 · 선발 조기붕괴","postgame_reason":"한 명 이상의 선발이 3이닝 이하 4실점 이상으로 무너져 언더의 핵심 전제가 조기에 훼손","postgame_quality":"MODEL_MISS_CANDIDATE","model_miss_candidate":True}
            if after7 is not None and after7>point:
                return {**base,"postgame_class":"MODEL_MISS_TOTAL_DIRECTION","postgame_class_ko":"사전 언더 방향성 실패 후보","postgame_reason":f"7회 종료 전에 이미 {after7}점으로 U{point:g} 라인을 넘어 late tail로 보기 어려움","postgame_quality":"MODEL_MISS_CANDIDATE","model_miss_candidate":True}
            return {**base,"postgame_class":"UNDER_LOSS_REVIEW","postgame_class_ko":"언더 미적중 · 원인 추가검토","postgame_reason":f"최종 {int(total)}점 · late-tail 또는 조기붕괴 기준이 명확히 충족되지 않음"}
        if _is_over(snapshot):
            # If game was still far below line very late, direction itself likely missed.
            if after7 is not None and after7<=max(1,point-3.0):
                return {**base,"postgame_class":"MODEL_MISS_TOTAL_DIRECTION","postgame_class_ko":"사전 오버 방향성 실패 후보","postgame_reason":f"7회 종료 총 {after7}점으로 O{point:g} 필요 득점과 큰 차이가 남음 · 최종 {int(total)}점","postgame_quality":"MODEL_MISS_CANDIDATE","model_miss_candidate":True}
            # High traffic but low runs -> sequencing variance, not automatically a model miss.
            h=evidence.get("home_team_stats") or {};a=evidence.get("away_team_stats") or {}
            traffic=sum((_num(x.get("hits")) or 0)+(_num(x.get("walks")) or 0) for x in (h,a))
            lob=sum((_num(x.get("left_on_base")) or 0) for x in (h,a))
            if traffic>=20 and lob>=14:
                return {**base,"postgame_class":"GOOD_PICK_RUN_SEQUENCING_LOSS","postgame_class_ko":"GOOD PICK 후보 + 득점권/잔루 분산","postgame_reason":f"안타+볼넷 {traffic:.0f}, 잔루 {lob:.0f}인데 최종 {int(total)}점으로 득점 전환이 낮았음","postgame_quality":"GOOD_PICK_VARIANCE"}
            return {**base,"postgame_class":"OVER_LOSS_REVIEW","postgame_class_ko":"오버 미적중 · 원인 추가검토","postgame_reason":f"최종 {int(total)}점 · 명확한 조기 방향성 실패/득점분산 기준이 확인되지 않음"}

    if market=="spreads":
        if side and evidence.get("extra_innings") and _lead_state(rows,side,9)==0:
            return {**base,"postgame_class":"GOOD_PICK_EXTRA_INNING_TAIL","postgame_class_ko":"GOOD PICK + 연장 핸디 tail","postgame_reason":"정규 9회 동점 이후 연장 결과로 핸디캡 미적중","postgame_quality":"GOOD_PICK_TAIL","tail_event":True}
        return {**base,"postgame_class":"SPREAD_LOSS_REVIEW","postgame_class_ko":"런라인 미적중 · 원인 추가검토","postgame_reason":"런라인 결과에 대한 명확한 tail/조기붕괴 기준이 확인되지 않음"}

    return base


def build_review_row(snapshot,evidence,classification,game_row=None):
    side=_side_for_selection(snapshot)
    picked_st=evidence.get(f"{side}_starter") if side else None
    exp_ip=_num(snapshot.get(f"{side}_starter_expected_ip")) if side else None
    exp_bb=_num(snapshot.get(f"{side}_starter_recent_bb_pct")) if side else None
    return {
        "fingerprint":snapshot.get("fingerprint"),"event_id":snapshot.get("event_id"),"gamePk":(game_row or {}).get("gamePk") or evidence.get("gamePk"),
        "commence_time":snapshot.get("commence_time"),"home_team":snapshot.get("home_team"),"away_team":snapshot.get("away_team"),
        "market":snapshot.get("market"),"selection":snapshot.get("selection"),"point":snapshot.get("point"),"best_odds":snapshot.get("best_odds"),
        "model_win_prob":snapshot.get("model_win_prob"),"break_even":snapshot.get("break_even"),"edge_pp":snapshot.get("edge_pp"),"ev_roi":snapshot.get("ev_roi"),
        "v3_decision_status":snapshot.get("v3_decision_status"),"robust_positive_ratio":snapshot.get("robust_positive_ratio"),
        "home_score":snapshot.get("home_score"),"away_score":snapshot.get("away_score"),"settle_win":snapshot.get("settle_win"),"settle_push":snapshot.get("settle_push"),"settle_loss":snapshot.get("settle_loss"),
        **classification,
        "expected_starter_ip":exp_ip,"actual_starter_ip":(picked_st or {}).get("ip") if picked_st else None,
        "expected_starter_bb_pct":exp_bb,"actual_starter_bb_pct":(picked_st or {}).get("bb_pct") if picked_st else None,
        "home_starter_actual":evidence.get("home_starter"),"away_starter_actual":evidence.get("away_starter"),
        "home_bullpen_actual":evidence.get("home_bullpen"),"away_bullpen_actual":evidence.get("away_bullpen"),
        "home_team_actual":evidence.get("home_team_stats"),"away_team_actual":evidence.get("away_team_stats"),
        "innings":evidence.get("innings"),"extra_innings":evidence.get("extra_innings"),
        "reviewed_at":datetime.now(timezone.utc).isoformat(),"review_engine":"mlb-postgame-v1",
    }


def _load_reviews(path="data/postgame_reviews.jsonl"):
    local=_load_jsonl(path)
    if not persistent_store.enabled():return local
    remote=persistent_store.load("postgame_review")
    return persistent_store.merge(local,remote,id_field="fingerprint")

def analyze_settled_mlb(settled_path="data/settled_predictions.jsonl",review_path="data/postgame_reviews.jsonl",provider=None):
    settled=[x for x in load_settled(settled_path) if x.get("sport_key")=="baseball_mlb"]
    existing=_load_reviews(review_path);done={x.get("fingerprint") for x in existing}
    provider=provider or MLBPostgameProvider()
    cache={};match_cache={};out=[];errors=[]
    for s in settled:
        fp=s.get("fingerprint")
        if not fp or fp in done:continue
        try:
            match_key=s.get("event_id") or (s.get("home_team"),s.get("away_team"),s.get("commence_time"))
            if match_key not in match_cache:
                match_cache[match_key]=provider.match_game(s)
            game=match_cache.get(match_key)
            if not game or not game.get("gamePk"):
                errors.append(f"{s.get('home_team')}-{s.get('away_team')}: gamePk match failed");continue
            gp=int(game["gamePk"])
            if gp not in cache:cache[gp]=provider.game_evidence(gp)
            ev=cache[gp]
            cls=classify_postgame(s,ev)
            out.append(build_review_row(s,ev,cls,game));done.add(fp)
        except Exception as e:
            errors.append(f"{s.get('home_team')}-{s.get('away_team')}: {type(e).__name__}: {e}")
    _append_jsonl(review_path,out)
    return {"reviewed":len(out),"errors":errors}


def postgame_reviews(path="data/postgame_reviews.jsonl"):
    return _load_reviews(path)
