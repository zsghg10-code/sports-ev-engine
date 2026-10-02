"""Transparent scenario screening, not a calibrated confidence interval."""
import math
from itertools import product, combinations
from sports_ev_engine.models.soccer_auto import score_matrix, price_from_matrix

POLICY_ID = 'national-scenarios-v3.4.24-hotfix1'

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
            status='SCENARIO_PASS';reason='설정한 27개 가정에서 모두 기대값 양수'
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
    # Final combination eligibility is downstream from the 27-scenario diagnostic.
    # Keep scenario/v3 flags intact and use the final national state as an extra gate.
    eligible=frame.scenario_parlay_eligible.fillna(False).map(truth)
    if 'national_status' in frame:
        eligible &= frame.national_status.eq('FINAL_BET')
    rows=frame[eligible].sort_values('scenario_ev_min',ascending=False).head(20).to_dict('records')
    out=[]
    for a,b in combinations(rows,2):
        if a['event_id']==b['event_id']:continue
        out.append({'조합':a['display_pick']+' + '+b['display_pick'],
            '배당 곱':round(a['best_odds']*b['best_odds'],3),
            '두 선택 모두 승리 추정(%)':round(100*a['model_win_prob']*b['model_win_prob'],2),
            '가정 최저 EV(%)':round(100*((1+a['scenario_ev_min'])*(1+b['scenario_ev_min'])-1),2),
            '조건':'독립 가정 · 적특/반적특 시 수령액 변동 · 실전 미검증'})
    return sorted(out,key=lambda x:x['가정 최저 EV(%)'],reverse=True)[:5]


STATUS_LABELS = {'FINAL_BET':'최종 후보', 'PROVISIONAL':'잠정 후보',
                 'COMBO_EXCLUDE':'조합 제외', 'NO_BET':'베팅 제외'}

def truth(value):
    return str(value).strip().lower() in {'true','1','yes','confirmed','final'}

def finite(value):
    try: return math.isfinite(float(value))
    except (TypeError, ValueError): return False

def match_data_validation(row, now=None):
    import pandas as pd
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='UTC')
    if now.tzinfo is None: now = now.tz_localize('UTC')
    stamp = pd.to_datetime(row.get('data_checked_at'), utc=True, errors='coerce')
    xg_stamp = pd.to_datetime(row.get('xg_checked_at'), utc=True, errors='coerce')
    checks = {
        '라인업': truth(row.get('lineup_confirmed')),
        'xG': all(finite(row.get(k)) and float(row.get(k)) >= 0 for k in
                   ('home_xg_for','home_xg_against','away_xg_for','away_xg_against'))
              and all(finite(row.get(k)) and float(row.get(k)) >= 3 for k in ('xg_samples_home','xg_samples_away'))
              and str(row.get('xg_collection_status','')).upper() == 'OK',
        '결장': truth(row.get('injury_available')),
        '휴식일': all(finite(row.get(k)) and float(row.get(k)) >= 0 for k in ('home_rest_days','away_rest_days')),
        '신선도': all(not pd.isna(t) and 0 <= (now-t).total_seconds() <= 10800 for t in (stamp,xg_stamp)),
    }
    missing = [key for key,ok in checks.items() if not ok]
    return {'match_data_verified':not missing, 'match_data_status':'검증 완료' if not missing else '확인 필요',
            'match_data_missing':' · '.join(missing)}

def finalize_national(row, now=None):
    """Final A-match display state. Diagnostic scenario/v3 flags remain immutable."""
    out = dict(row)
    out.update(match_data_validation(row, now))
    ev = row.get('point_ev_roi', row.get('ev_roi'))
    scenario = row.get('selection_status')
    if scenario is None:
        scenario='SCENARIO_PASS' if row.get('national_status') in {'FINAL_BET','PROVISIONAL'} else 'DATA_HOLD'
    robust = row.get('v3_decision_status')
    counter_risk = str(row.get('counter_case_risk') or '').upper()

    if not finite(ev) or float(ev) <= 0 or scenario in {'PASS','DATA_HOLD'}:
        status = 'NO_BET'
    # Hard exclusion/risk gates must be evaluated BEFORE missing-data provisional status.
    # Otherwise REVIEW/HIGH-risk rows can be incorrectly promoted to PROVISIONAL.
    elif (scenario in {'REVIEW','SENSITIVE'}
          or robust in {'REVIEW','FRAGILE','PASS','DATA_HOLD'}
          or counter_risk == 'HIGH'
          or str(row.get('adaptive_gate','OK')) not in {'','OK'}):
        status = 'COMBO_EXCLUDE'
    elif not out['match_data_verified']:
        status = 'PROVISIONAL'
    elif robust != 'ROBUST':
        status = 'COMBO_EXCLUDE'
    else:
        status = 'FINAL_BET'

    if status in {'FINAL_BET','PROVISIONAL'}:
        out['selection_reason']='가정 변화 통과 · ' + ('경기 데이터 검증 완료' if out['match_data_verified'] else '확인 필요: ' + out['match_data_missing'])

    out['national_status'] = status
    out['national_status_label'] = STATUS_LABELS[status]

    # IMPORTANT: do not overwrite these diagnostic flags:
    # scenario_candidate / scenario_parlay_eligible
    # v3_candidate / v3_parlay_eligible / parlay_eligible
    # They describe upstream diagnostics and are intentionally independent of the
    # user-facing FINAL/PROVISIONAL/COMBO_EXCLUDE/NO_BET state.
    out['national_candidate'] = status in {'FINAL_BET','PROVISIONAL'}
    out['national_final_eligible'] = status == 'FINAL_BET'
    out['national_combo_eligible'] = (
        status == 'FINAL_BET'
        and truth(row.get('scenario_parlay_eligible', False))
        and truth(row.get('v3_parlay_eligible', False))
    )
    out['model_validation_status'] = row.get('model_validation_status','장기 수익성 미검증')
    return out
