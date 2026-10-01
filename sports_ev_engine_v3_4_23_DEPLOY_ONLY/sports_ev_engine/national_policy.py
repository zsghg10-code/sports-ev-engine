"""Transparent scenario screening, not a calibrated confidence interval."""
import math
from itertools import product, combinations
from sports_ev_engine.models.soccer_auto import score_matrix, price_from_matrix

POLICY_ID = 'national-scenarios-v293'

def scenario_matrices(hl, al):
    # Illustrative modelling assumptions, not estimated parameter errors.
    return [score_matrix(hl*h, al*a) for h,a in product((.9,1.,1.1), repeat=2)]

def assess(row, matrices, side, line):
    odds=float(row['best_odds']); market=float(row['consensus_prob'])
    if not (math.isfinite(odds) and odds>1 and math.isfinite(market) and 0<market<1):
        return dict(selection_status='DATA_HOLD', selection_reason='배당/시장 확률 오류', scenario_candidate=False, scenario_parlay_eligible=False, scenario_ev_min=float('nan'), scenario_ev_max=float('nan'), scenario_win_min=float('nan'), scenario_win_max=float('nan'), scenario_count=0)
    values=[]; wins=[]
    for matrix in matrices:
        w,p,_=price_from_matrix(matrix,row['market'],side,line)
        for weight in (.15,.25,.35):
            fw=weight*w+(1-weight)*market*(1-p)
            wins.append(fw)
            values.append(odds*fw+p-1) # includes full/half stake refunds
    low=min(values); high=max(values)
    status='PASS'; reason='현재 배당에서 기준 기대값이 0 이하'
    if min(row['home_form_matches'],row['away_form_matches'])<5:
        status='DATA_HOLD';reason='양 팀 최근 기록 최소 5경기 미충족'
    elif row['sanity'] in ('OUTLIER_SHRUNK','HIGH_DISAGREEMENT'):
        status='REVIEW';reason='원모델-시장 괴리 15%p 초과: 기존 검토 기준 유지'
    elif row['point_ev_roi']>0:
        if low>0:
            status='SCENARIO_PASS';reason='설정한 27개 가정에서 모두 기대값 양수; 수익성 미검증'
        else:
            status='SENSITIVE';reason='기준 기대값은 양수지만 가정 변화 시 0 이하; 조합 제외'
    lineup=bool(row.get('lineup_confirmed',False))
    if not lineup:reason+=' / 라인업 미확인: 조합 제외'
    return dict(selection_status=status,selection_reason=reason,
        scenario_candidate=status=='SCENARIO_PASS',
        scenario_parlay_eligible=status=='SCENARIO_PASS' and lineup,
        scenario_ev_min=low,scenario_ev_max=high,
        scenario_win_min=min(wins),scenario_win_max=max(wins),scenario_count=len(values))

def reference_pairs(frame):
    """Experimental pairs of distinct events; independence is an assumption."""
    rows=frame[frame.scenario_parlay_eligible].sort_values('scenario_ev_min',ascending=False).head(20).to_dict('records')
    out=[]
    for a,b in combinations(rows,2):
        if a['event_id']==b['event_id']:continue
        out.append({'조합':a['display_pick']+' + '+b['display_pick'],
            '배당 곱':round(a['best_odds']*b['best_odds'],3),
            '두 선택 모두 승리 추정(%)':round(100*a['model_win_prob']*b['model_win_prob'],2),
            '가정 최저 EV(%)':round(100*((1+a['scenario_ev_min'])*(1+b['scenario_ev_min'])-1),2),
            '조건':'독립 가정 · 적특/반적특 시 수령액 변동 · 실전 미검증'})
    return sorted(out,key=lambda x:x['가정 최저 EV(%)'],reverse=True)[:5]
