"""Human-readable UI explanations while preserving audit codes underneath."""
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


def humanize_policy_reason(reason: Any, row: Any = None) -> str:
    """Translate legacy policy codes into concise user-facing Korean.

    The original ``selection_reason`` remains stored and available in the audit
    expander.  This helper only changes presentation.
    """
    raw=str(reason or "").strip()
    if not raw:
        return "세부 판정 사유 없음"
    text=raw
    low=raw.lower()
    if "현재 배당에서 기준 기대값이 0 이하" in raw:
        text="현재 배당 기준 모델 확률이 손익분기 확률을 넘지 못해 +EV 후보에서 제외"
    elif "설정한 27개 가정에서 모두 기대값 양수" in raw:
        text="현재 모델과 27개 스트레스 가정에서 모두 +EV가 유지됨. 장기 수익성은 사후 표본으로 별도 검증 중"
    elif "기준 기대값은 양수지만 가정 변화 시 0 이하" in raw:
        text="기준 모델은 +EV지만 일부 스트레스 가정에서 EV가 0 이하로 내려가 강건성이 부족함"
    elif "원모델-시장 괴리 15%p 초과" in raw:
        text="독립 모델과 시장 확률 차이가 15%p를 넘어, 시장이 반영한 미수집 정보 가능성을 확인할 때까지 검토 후보"
    elif "양 팀 최근 기록 최소 5경기 미충족" in raw:
        text="최근 경기 표본이 최소 기준보다 작아 확률 안정성이 부족해 판단 보류"
    elif "배당/시장 확률 오류" in raw:
        text="배당 또는 시장 확률 데이터가 불완전해 판단 보류"

    if "라인업 미확인" in raw and "라인업" not in text:
        text += " · 공식 라인업 미확정이라 조합 단계에서는 보수적으로 취급"
    return text


def counter_case_parts(raw: Any) -> list[str]:
    text=str(raw or "").strip()
    if not text:
        return []
    return [x.strip() for x in re.split(r"\s*\|\s*",text) if x.strip()]


def humanize_data_risk(raw: Any) -> str:
    parts=counter_case_parts(raw)
    if not parts:
        return "현재 수집 데이터에서 별도의 중대한 데이터 리스크가 감지되지 않음"
    out=[]
    for p in parts:
        m=re.search(r"recent sample(?: only)? (\d+) matches",p,re.I)
        if m:
            out.append(f"최근 표본 {m.group(1)}경기")
            continue
        m=re.search(r"model uncertainty ([0-9.]+)pp",p,re.I)
        if m:
            out.append(f"모델 불확실성 {m.group(1)}%p")
            continue
        m=re.search(r"deep signal coverage ([0-9]+)%",p,re.I)
        if m:
            out.append(f"정밀 신호 확보율 {m.group(1)}%")
            continue
        m=re.search(r"advanced signal completeness ([0-9]+)%",p,re.I)
        if m:
            out.append(f"고급 신호 완성도 {m.group(1)}%")
            continue
        if "strongly conflicts with market" in p.lower():
            out.append("독립모델과 시장의 큰 괴리")
            continue
        if "gap requires monitoring" in p.lower():
            out.append("모델-시장 괴리 추가 확인 필요")
            continue
        if "starting lineup not confirmed" in p.lower():
            out.append("공식 라인업 미확정")
            continue
        if "projected xi available" in p.lower():
            out.append("예상 XI만 확보·공식 라인업 미확정")
            continue
        if "pregame stage is" in p.lower():
            stage=p.split("is",1)[-1].strip()
            out.append(f"사전 데이터 단계 {stage}")
            continue
        if "national-team samples/venues/rotation" in p.lower():
            out.append("국가대표 표본·중립구장·로테이션 변동성")
            continue
    # keep order but remove duplicates
    uniq=[]
    for x in out:
        if x not in uniq:
            uniq.append(x)
    return " · ".join(uniq) if uniq else "현재 자동 반증 항목은 내부 감사 정보로만 유지"


def humanize_failure_route(raw: Any) -> str:
    """Describe a plausible failure path without inventing tactical facts."""
    parts=counter_case_parts(raw)
    low=" | ".join(parts).lower()
    if not parts:
        return "현재 수집 신호에서 뚜렷한 단일 실패경로는 확인되지 않음"
    if "starting lineup not confirmed" in low or "projected xi available" in low:
        return "공식 선발이 예상과 크게 달라져 현재 전력 추정과 득점 기대가 약해지는 경우"
    if "strongly conflicts with market" in low or "gap requires monitoring" in low:
        return "시장이 반영한 결장·로테이션·매치업 정보 중 모델이 놓친 변수가 실제로 중요할 경우 현재 확률 우위가 무너질 수 있음"
    if "model uncertainty" in low or "deep signal coverage" in low or "recent sample" in low:
        return "정밀 신호와 표본이 충분하지 않아 특정 전술적 실패경로를 단정하기 어렵고, 가장 큰 위험은 현재 모델 확률 자체가 실제보다 과대평가된 경우"
    return "현재 자동 수집 정보만으로 특정 경기 전개 실패경로를 단정하기 어려워 추가 확인 필요"


def humanize_selection_summary(row: Any) -> str:
    try:
        status=str(row.get("v3_decision_status") or row.get("robust_status") or "").upper()
        ratio=_num(row.get("robust_positive_ratio"))
        n=int(_num(row.get("robust_scenario_count"),0) or 0)
        p10=_num(row.get("robust_ev_p10"))
        ev=_num(row.get("ev_roi"),_num(row.get("point_ev_roi")))
    except Exception:
        return "세부 선정 이유 확인 필요"
    bits=[]
    if math.isfinite(ev): bits.append(f"기준 EV {ev*100:+.1f}%")
    if status=="ROBUST" and math.isfinite(ratio) and n:
        bits.append(f"스트레스 {int(round(ratio*n))}/{n}개에서 +EV 유지")
        if math.isfinite(p10): bits.append(f"P10 EV {p10*100:+.1f}%")
    elif status=="SENSITIVE" and math.isfinite(ratio) and n:
        bits.append(f"스트레스 양수 비율 {ratio*100:.0f}%로 가정 변화에 민감")
    elif status:
        bits.append(f"v3 판정 {status}")
    return " · ".join(bits) if bits else "세부 선정 이유 확인 필요"
