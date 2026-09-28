"""Display/combination eligibility; never relabel a disagreement as a good pick."""
import pandas as pd

REVIEW_STATES={'OUTLIER_SHRUNK','HIGH_DISAGREEMENT'}

def candidate_mask(frame):
    if frame.empty:return pd.Series(False,index=frame.index,dtype=bool)
    if 'v3_candidate' in frame.columns:
        return frame['v3_candidate'].fillna(False).astype(bool)
    return frame['grade'].isin(['A','B','C']) & (frame['conservative_ev_roi']>0) & ~frame['sanity'].isin(REVIEW_STATES)

def review_mask(frame):
    if frame.empty:return pd.Series(False,index=frame.index,dtype=bool)
    base=frame['grade'].eq('REVIEW') | frame['sanity'].isin(REVIEW_STATES)
    if 'v3_decision_status' in frame.columns:
        base = base | frame['v3_decision_status'].isin(['REVIEW','DATA_HOLD','FRAGILE'])
    return base

def review_reason(gap,sample,international):
    reasons=[]
    if abs(gap)>25:reasons.append(f'원모델-시장 차이 {gap:+.1f}%p: 25%p 초과, 이상치 검토')
    elif abs(gap)>15:reasons.append(f'원모델-시장 차이 {gap:+.1f}%p: 15%p 초과, 후보/다폴 제외')
    elif abs(gap)>10:reasons.append(f'원모델-시장 차이 {gap:+.1f}%p: 주의')
    else:reasons.append(f'원모델-시장 차이 {gap:+.1f}%p: 기준 이내')
    if sample<5:reasons.append(f'최근 표본 부족({sample}경기)')
    if international:reasons.append('국가대표 전용 모델 / 최고 B')
    return '; '.join(reasons)
