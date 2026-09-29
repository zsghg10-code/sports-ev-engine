"""Human-readable, match-specific explanations while preserving audit codes."""
from __future__ import annotations

import math
import re
from typing import Any


def _num(v: Any, default=float("nan")) -> float:
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _get(row: Any, key: str, default=None):
    try:
        v=row.get(key, default)
    except Exception:
        return default
    try:
        if v is None: return default
        if isinstance(v,float) and math.isnan(v): return default
    except Exception:
        pass
    return v


def _teams(row: Any):
    return str(_get(row,"home_team","홈")), str(_get(row,"away_team","원정"))


def _market_side(row: Any):
    home,away=_teams(row)
    market=str(_get(row,"market","")).lower()
    sel=str(_get(row,"selection","")).strip()
    low=sel.lower()
    if market=="h2h":
        if low==home.lower(): return "home",home
        if low==away.lower(): return "away",away
        if low in {"draw","tie","x"} or "draw" in low:return "draw","무승부"
    if market=="totals":
        if "over" in low:return "over",f"오버 {(_num(_get(row,'point'))):g}" if math.isfinite(_num(_get(row,'point'))) else "오버"
        if "under" in low:return "under",f"언더 {(_num(_get(row,'point'))):g}" if math.isfinite(_num(_get(row,'point'))) else "언더"
    if market=="spreads":
        if low==home.lower():return "home",home
        if low==away.lower():return "away",away
    return "other",sel or "선택"


def _pct(v, digits=1):
    x=_num(v)
    return f"{x*100:.{digits}f}%" if math.isfinite(x) else None


def _join(items):
    return " · ".join(x for x in items if x)


def _recent_form_phrase(row: Any, side: str) -> str | None:
    home,away=_teams(row)
    if side=="home":
        name=home; gf=_num(_get(row,"home_recent_gf")); ga=_num(_get(row,"home_recent_ga"))
    elif side=="away":
        name=away; gf=_num(_get(row,"away_recent_gf")); ga=_num(_get(row,"away_recent_ga"))
    else:return None
    if not (math.isfinite(gf) and math.isfinite(ga)):return None
    return f"{name} 최근 표본 평균 {gf:.2f}득점/{ga:.2f}실점"


def _xg_phrase(row: Any, side: str) -> str | None:
    home,away=_teams(row)
    if side=="home":
        name=home; xgf=_num(_get(row,"home_xg_for")); xga=_num(_get(row,"home_xg_against"))
    elif side=="away":
        name=away; xgf=_num(_get(row,"away_xg_for")); xga=_num(_get(row,"away_xg_against"))
    else:return None
    if not (math.isfinite(xgf) and math.isfinite(xga)):return None
    return f"{name} 최근 xG/xGA {xgf:.2f}/{xga:.2f}"


def _lineup_phrase(row: Any) -> str | None:
    home,away=_teams(row)
    confirmed=bool(_get(row,"lineup_confirmed",False))
    probable=bool(_get(row,"probable_lineup",False))
    if confirmed:
        hf=str(_get(row,"home_formation","") or "").strip(); af=str(_get(row,"away_formation","") or "").strip()
        if hf or af:
            return f"확정 선발 반영" + (f"({home} {hf or '—'} / {away} {af or '—'})")
        return "양 팀 확정 선발 반영"
    if probable:return "예상 XI만 반영되어 공식 선발 발표 전 재평가 필요"
    return None


def _injury_phrase(row: Any) -> str | None:
    home,away=_teams(row)
    hm=_get(row,"home_missing_players",[]) or []; am=_get(row,"away_missing_players",[]) or []
    if isinstance(hm,str): hm=[x.strip() for x in hm.split(',') if x.strip()]
    if isinstance(am,str): am=[x.strip() for x in am.split(',') if x.strip()]
    bits=[]
    if hm:bits.append(f"{home} 결장 반영: {', '.join(hm[:3])}" + (" 외" if len(hm)>3 else ""))
    if am:bits.append(f"{away} 결장 반영: {', '.join(am[:3])}" + (" 외" if len(am)>3 else ""))
    return "; ".join(bits) if bits else None


def _rest_phrase(row: Any) -> str | None:
    home,away=_teams(row); hr=_num(_get(row,"home_rest_days")); ar=_num(_get(row,"away_rest_days"))
    if not (math.isfinite(hr) and math.isfinite(ar)):return None
    if abs(hr-ar)<1:return None
    fav=home if hr>ar else away
    return f"휴식일 {home} {hr:.1f}일 / {away} {ar:.1f}일로 {fav} 쪽이 더 김"


def _market_phrase(row: Any) -> str | None:
    model=_num(_get(row,"model_win_prob")); market=_num(_get(row,"consensus_prob")); be=_num(_get(row,"break_even")); ev=_num(_get(row,"ev_roi"),_num(_get(row,"point_ev_roi")))
    if not math.isfinite(model):return None
    bits=[f"최종 추정확률 {model*100:.1f}%"]
    if math.isfinite(market):bits.append(f"시장 무마진 {market*100:.1f}%")
    if math.isfinite(be):bits.append(f"BE {be*100:.1f}%")
    if math.isfinite(ev):bits.append(f"EV {ev*100:+.1f}%")
    return ", ".join(bits)


def match_specific_reason(row: Any, policy_reason: Any="") -> str:
    """Concrete user-facing reason using only measured fields on this match."""
    side,label=_market_side(row); home,away=_teams(row)
    raw=_num(_get(row,"raw_independent_prob")); market=_num(_get(row,"consensus_prob")); final=_num(_get(row,"model_win_prob"))
    market_name=str(_get(row,"market","")).lower()
    pieces=[]
    if math.isfinite(raw) and math.isfinite(market):
        gap=(raw-market)*100
        pieces.append(f"독립모델 {raw*100:.1f}% vs 시장 {market*100:.1f}% ({gap:+.1f}%p)")
    elif math.isfinite(final) and math.isfinite(market):
        pieces.append(f"최종모델 {final*100:.1f}% vs 시장 {market*100:.1f}%")

    if market_name=="h2h" and side in {"home","away"}:
        fp=_recent_form_phrase(row,side)
        if fp:pieces.append(fp)
        xp=_xg_phrase(row,side)
        if xp:pieces.append(xp)
        opp="away" if side=="home" else "home"
        opp_xg=_xg_phrase(row,opp)
        if opp_xg and not xp:pieces.append(opp_xg)
        he=_num(_get(row,"home_elo")); ae=_num(_get(row,"away_elo"))
        if math.isfinite(he) and math.isfinite(ae) and abs(he-ae)>=35:
            stronger=home if he>ae else away
            pieces.append(f"Elo는 {stronger} 우위({he:.0f}-{ae:.0f})")
    elif market_name=="totals":
        hl=_num(_get(row,"home_lambda")); al=_num(_get(row,"away_lambda")); point=_num(_get(row,"point"))
        if math.isfinite(hl) and math.isfinite(al):
            pieces.append(f"모델 총득점 기대 {hl+al:.2f}골" + (f" vs 기준 {point:g}" if math.isfinite(point) else ""))
        hgf=_num(_get(row,"home_recent_gf")); agf=_num(_get(row,"away_recent_gf")); hga=_num(_get(row,"home_recent_ga")); aga=_num(_get(row,"away_recent_ga"))
        if all(math.isfinite(x) for x in (hgf,agf,hga,aga)):
            pieces.append(f"최근 득점/실점 평균 {home} {hgf:.2f}/{hga:.2f}, {away} {agf:.2f}/{aga:.2f}")
        hs=int(_num(_get(row,"home_blowout_matches_shrunk"),0) or 0); as_=int(_num(_get(row,"away_blowout_matches_shrunk"),0) or 0)
        if hs or as_:
            pieces.append(f"약체/대량득실 이상치 축소 보정 {home} {hs}경기, {away} {as_}경기")
        xgw=_num(_get(row,"xg_blend_weight")); xggap=_num(_get(row,"xg_goal_model_divergence_pct"))
        if math.isfinite(xgw):
            txt=f"실측 xG 단일혼합 {xgw*100:.0f}%"
            if math.isfinite(xggap):txt+=f"(득점모델 대비 xG 총량 {xggap:+.1f}%)"
            pieces.append(txt)

    mp=_market_phrase(row)
    if mp:pieces.append(mp)
    lp=_lineup_phrase(row)
    if lp:pieces.append(lp)
    ip=_injury_phrase(row)
    if ip:pieces.append(ip)
    rp=_rest_phrase(row)
    if rp:pieces.append(rp)

    # Add the policy gate only when it materially changes the decision.
    raw_reason=str(policy_reason or _get(row,"selection_reason","") or "")
    if "원모델-시장 괴리 15%p 초과" in raw_reason:
        pieces.append("시장과의 괴리가 커 자동선정 대신 검토로 제한")
    elif "가정 변화 시 0 이하" in raw_reason:
        pieces.append("스트레스 가정 일부에서 +EV가 사라져 조합은 제외")
    elif "현재 배당에서 기준 기대값이 0 이하" in raw_reason:
        pieces.append("현재 가격에서는 손익분기 확률을 넘지 못함")
    elif "27개 가정에서 모두 기대값 양수" in raw_reason:
        pieces.append("27개 스트레스 가정에서 +EV 유지")

    # Keep it readable: 3-5 facts are enough; audit details remain in the expander.
    uniq=[]
    for x in pieces:
        if x and x not in uniq:uniq.append(x)
    if uniq:
        return _join(uniq[:6])
    if raw_reason:
        if "현재 배당에서 기준 기대값이 0 이하" in raw_reason:
            return "현재 배당 기준 모델 확률이 손익분기 확률을 넘지 못해 +EV 후보에서 제외"
        if "설정한 27개 가정에서 모두 기대값 양수" in raw_reason:
            return "현재 모델과 27개 스트레스 가정에서 모두 +EV가 유지됨"
        if "기준 기대값은 양수지만 가정 변화 시 0 이하" in raw_reason:
            return "기준 모델은 +EV지만 일부 스트레스 가정에서 EV가 0 이하로 내려가 강건성이 부족함"
        if "원모델-시장 괴리 15%p 초과" in raw_reason:
            return "독립 모델과 시장 확률 차이가 15%p를 넘어 추가 확인이 필요한 검토 후보"
        return raw_reason
    return "세부 선정 이유 확인 필요"


def match_specific_failure_route(row: Any) -> str:
    """Plausible failure route grounded only in this match's measured facts."""
    side,label=_market_side(row); home,away=_teams(row); market_name=str(_get(row,"market","")).lower()
    risks=[]
    if market_name=="h2h" and side in {"home","away"}:
        fav=home if side=="home" else away; opp=away if side=="home" else home
        fav_ga=_num(_get(row,"home_recent_ga" if side=="home" else "away_recent_ga"))
        opp_gf=_num(_get(row,"away_recent_gf" if side=="home" else "home_recent_gf"))
        fav_xga=_num(_get(row,"home_xg_against" if side=="home" else "away_xg_against"))
        opp_xgf=_num(_get(row,"away_xg_for" if side=="home" else "home_xg_for"))
        if math.isfinite(fav_xga) and math.isfinite(opp_xgf):
            risks.append(f"{opp}의 최근 xG {opp_xgf:.2f}가 재현되고 {fav}의 xGA {fav_xga:.2f} 수준 수비 허용이 이어지는 경우")
        elif math.isfinite(fav_ga) and math.isfinite(opp_gf):
            risks.append(f"{opp}가 최근 평균 {opp_gf:.2f}득점 수준의 공격을 재현하고 {fav}가 최근 {fav_ga:.2f}실점 수준의 수비 불안을 보이는 경우")
        # lineup/injury downside
        miss=_get(row,"home_missing_players" if side=="home" else "away_missing_players",[]) or []
        if isinstance(miss,str):miss=[x.strip() for x in miss.split(',') if x.strip()]
        if miss:
            risks.append(f"{fav} 결장자({', '.join(miss[:2])}{' 외' if len(miss)>2 else ''}) 영향이 모델 추정보다 큰 경우")
        raw=_num(_get(row,"raw_independent_prob")); market=_num(_get(row,"consensus_prob"))
        if math.isfinite(raw) and math.isfinite(market) and abs(raw-market)>=.10:
            risks.append(f"독립모델과 시장 차이가 {abs(raw-market)*100:.1f}%p라 시장이 반영한 미수집 정보가 실제로 중요할 경우")
    elif market_name=="totals":
        point=_num(_get(row,"point")); hl=_num(_get(row,"home_lambda")); al=_num(_get(row,"away_lambda"))
        hgf=_num(_get(row,"home_recent_gf")); agf=_num(_get(row,"away_recent_gf")); hga=_num(_get(row,"home_recent_ga")); aga=_num(_get(row,"away_recent_ga"))
        total=hl+al if math.isfinite(hl) and math.isfinite(al) else float('nan')
        if side=="over":
            if math.isfinite(total) and math.isfinite(point):risks.append(f"모델 총득점 기대 {total:.2f}가 기준 {point:g}에 근접해 초반 득점이 지연되면 오버 여유가 빠르게 줄어드는 경우")
            if all(math.isfinite(x) for x in (hgf,agf)):risks.append(f"양 팀 최근 득점 평균 합 {hgf+agf:.2f}가 경기 당일 마무리 효율 저하로 재현되지 않는 경우")
        elif side=="under":
            if math.isfinite(total) and math.isfinite(point):risks.append(f"모델 총득점 기대 {total:.2f}가 기준 {point:g}와 가깝기 때문에 선제골 이후 경기 템포가 빨라지는 경우")
            if all(math.isfinite(x) for x in (hga,aga)):risks.append(f"양 팀 최근 실점 평균 합 {hga+aga:.2f}보다 수비 효율이 나빠져 한쪽이 조기 실점하는 경우")
    if not bool(_get(row,"lineup_confirmed",False)):
        risks.append("공식 선발이 예상 XI와 크게 달라져 현재 전력·득점 추정이 바뀌는 경우")
    uniq=[]
    for x in risks:
        if x and x not in uniq:uniq.append(x)
    if uniq:return " / ".join(uniq[:3])
    return humanize_failure_route(_get(row,"counter_case_summary",""))


def humanize_policy_reason(reason: Any, row: Any = None) -> str:
    raw=str(reason or "").strip()
    if row is not None:
        specific=match_specific_reason(row,raw)
        if specific:return specific
    if not raw:return "세부 판정 사유 없음"
    if "현재 배당에서 기준 기대값이 0 이하" in raw:return "현재 배당 기준 모델 확률이 손익분기 확률을 넘지 못해 +EV 후보에서 제외"
    if "설정한 27개 가정에서 모두 기대값 양수" in raw:return "현재 모델과 27개 스트레스 가정에서 모두 +EV가 유지됨"
    if "기준 기대값은 양수지만 가정 변화 시 0 이하" in raw:return "기준 모델은 +EV지만 일부 스트레스 가정에서 EV가 0 이하로 내려가 강건성이 부족함"
    if "원모델-시장 괴리 15%p 초과" in raw:return "독립 모델과 시장 확률 차이가 15%p를 넘어 추가 확인이 필요한 검토 후보"
    if "양 팀 최근 기록 최소 5경기 미충족" in raw:return "최근 경기 표본이 최소 기준보다 작아 확률 안정성이 부족해 판단 보류"
    if "배당/시장 확률 오류" in raw:return "배당 또는 시장 확률 데이터가 불완전해 판단 보류"
    return raw


def counter_case_parts(raw: Any) -> list[str]:
    text=str(raw or "").strip()
    return [x.strip() for x in re.split(r"\s*\|\s*",text) if x.strip()] if text else []


def humanize_data_risk(raw: Any) -> str:
    parts=counter_case_parts(raw)
    if not parts:return "현재 수집 데이터에서 별도의 중대한 데이터 리스크가 감지되지 않음"
    out=[]
    for p in parts:
        m=re.search(r"recent sample(?: only)? (\d+) matches",p,re.I)
        if m:out.append(f"최근 표본 {m.group(1)}경기");continue
        m=re.search(r"model uncertainty ([0-9.]+)pp",p,re.I)
        if m:out.append(f"모델 불확실성 {m.group(1)}%p");continue
        m=re.search(r"deep signal coverage ([0-9]+)%",p,re.I)
        if m:out.append(f"정밀 신호 확보율 {m.group(1)}%");continue
        m=re.search(r"advanced signal completeness ([0-9]+)%",p,re.I)
        if m:out.append(f"고급 신호 완성도 {m.group(1)}%");continue
        if "strongly conflicts with market" in p.lower():out.append("독립모델과 시장의 큰 괴리");continue
        if "gap requires monitoring" in p.lower():out.append("모델-시장 괴리 추가 확인 필요");continue
        if "starting lineup not confirmed" in p.lower():out.append("공식 라인업 미확정");continue
        if "projected xi available" in p.lower():out.append("예상 XI만 확보·공식 라인업 미확정");continue
        if "national-team samples/venues/rotation" in p.lower():out.append("국가대표 표본·중립구장·로테이션 변동성");continue
    uniq=[]
    for x in out:
        if x not in uniq:uniq.append(x)
    return " · ".join(uniq) if uniq else "현재 자동 반증 항목은 내부 감사 정보로만 유지"


def humanize_failure_route(raw: Any) -> str:
    parts=counter_case_parts(raw); low=" | ".join(parts).lower()
    if not parts:return "현재 수집 신호에서 뚜렷한 단일 실패경로는 확인되지 않음"
    if "starting lineup not confirmed" in low or "projected xi available" in low:return "공식 선발이 예상과 크게 달라져 현재 전력 추정과 득점 기대가 약해지는 경우"
    if "strongly conflicts with market" in low or "gap requires monitoring" in low:return "시장이 반영한 정보 중 모델이 놓친 변수가 실제로 중요할 경우 현재 확률 우위가 무너질 수 있음"
    if "model uncertainty" in low or "deep signal coverage" in low or "recent sample" in low:return "정밀 신호와 표본이 충분하지 않아 특정 전개를 단정하기 어렵고, 현재 모델 확률 자체가 실제보다 과대평가된 경우"
    return "현재 자동 수집 정보만으로 특정 경기 전개 실패경로를 단정하기 어려워 추가 확인 필요"


def humanize_selection_summary(row: Any) -> str:
    # FINAL card should explain this match, not merely echo a policy code.
    return match_specific_reason(row,_get(row,"selection_reason",""))
