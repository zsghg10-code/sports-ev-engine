from __future__ import annotations

import math
import pandas as pd


def _num(v, default=float('nan')):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _truth(v):
    if isinstance(v, str):
        return v.strip().lower() in {'1','true','yes','y'}
    return bool(v)


def exclusion_reasons(row) -> str:
    reasons=[]
    status=str(row.get('v3_decision_status') or '')
    sanity=str(row.get('sanity') or '')
    stage=str(row.get('stage') or '')
    counter=str(row.get('counter_case_risk') or '')
    adaptive=str(row.get('adaptive_gate') or '')
    ensemble=str(row.get('ensemble_gate') or '')
    cev=_num(row.get('conservative_ev_roi'))

    if sanity in {'OUTLIER_SHRUNK','HIGH_DISAGREEMENT'}:
        reasons.append('독립모델-시장 괴리 안전게이트')
    if stage and stage != 'FINAL':
        reasons.append(f'{stage}: 라인업/정밀정보 확정 전')
    if counter == 'HIGH':
        reasons.append('반증·데이터 위험 HIGH')
    if math.isfinite(cev) and cev <= 0:
        reasons.append('불확실성 차감 후 보수 EV≤0')
    if status == 'SENSITIVE':
        reasons.append('스트레스 가정에 민감')
    elif status == 'FRAGILE':
        reasons.append('스트레스 강건성 부족')
    elif status == 'REVIEW' and not reasons:
        reasons.append('추가 검토 게이트')
    elif status == 'PASS' and not reasons:
        reasons.append('v3 후보 기준 미달')
    if adaptive == 'PASS_AFTER_CALIBRATION':
        reasons.append('앙상블/Calibration 후 +EV 소멸')
    elif adaptive == 'MODEL_CONFLICT_REVIEW':
        reasons.append('모델 앙상블 충돌')
    if ensemble == 'REVIEW' and '모델 앙상블 충돌' not in reasons:
        reasons.append('모델 앙상블 충돌')

    seen=[]
    for r in reasons:
        if r and r not in seen:
            seen.append(r)
    return ' · '.join(seen) or '-'


def candidate_label(row) -> str:
    if _truth(row.get('v3_parlay_eligible')):
        return '조합 가능'
    if _truth(row.get('v3_candidate')):
        return '단일 +EV 후보'
    if _num(row.get('ev_roi'), -1) > 0:
        if (not _truth(row.get('lineup_confirmed'))
                and str(row.get('stage')) in {'PRE-LINEUP', 'STARTER CONFIRMED'}
                and str(row.get('sanity')) not in {'OUTLIER_SHRUNK', 'HIGH_DISAGREEMENT'}
                and _num(row.get('conservative_ev_roi'), -1) > 0):
            return '라인업 대기 예비 후보'
        return '검토 후보' 
    return '제외'


def build_baseball_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    """Return every *base* +EV baseball row, including REVIEW rows.

    This deliberately differs from the parlay gate.  A row can have positive
    point EV yet be REVIEW/STARTER CONFIRMED/high-disagreement; hiding it made
    the UI look as if no positive-EV analysis existed.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    x=frame.copy()
    ev=pd.to_numeric(x.get('ev_roi'), errors='coerce')
    x=x[ev > 0].copy()
    if x.empty:
        return x
    x['diagnostic_candidate_status']=[candidate_label(r) for _,r in x.iterrows()]
    x['diagnostic_exclusion_reason']=[exclusion_reasons(r) for _,r in x.iterrows()]
    order={'조합 가능':0,'단일 +EV 후보':1,'라인업 대기 예비 후보':2,'검토 후보':3,'제외':4}
    x['_diag_order']=x['diagnostic_candidate_status'].map(order).fillna(9)
    for c in ['robust_positive_ratio','robust_ev_p10','conservative_ev_roi','ev_roi']:
        if c not in x.columns:
            x[c]=float('nan')
    x=x.sort_values(['_diag_order','robust_positive_ratio','robust_ev_p10','conservative_ev_roi','ev_roi'], ascending=[True,False,False,False,False])
    return x.drop(columns=['_diag_order'])


def diagnostic_counts(frame: pd.DataFrame) -> dict:
    d=build_baseball_diagnostics(frame)
    if d.empty:
        return {'base_positive':0,'parlay':0,'single':0,'review':0}
    s=d['diagnostic_candidate_status'].value_counts().to_dict()
    return {
        'base_positive':len(d),
        'parlay':int(s.get('조합 가능',0)),
        'single':int(s.get('단일 +EV 후보',0)),
        'review':int(s.get('검토 후보',0)) + int(s.get('라인업 대기 예비 후보',0)),
    }
