"""Per-match analysis view, independent of candidate eligibility."""
import pandas as pd

def match_summary(frame):
    out=[]
    for eid,g in frame.groupby('event_id',sort=False):
        first=g.iloc[0]
        for market in ('h2h','totals'):
            rows=g[g.market.eq(market)]
            if rows.empty:
                out.append({'경기':f"{first.home_team} - {first.away_team}",'분석':'승무패' if market=='h2h' else '언더오버','판정':'해당 마켓 배당 미수집/미선택'})
                continue
            if market=='totals':
                # Main line: most book coverage, then closest to balanced market.
                lines=[]
                for point,q in rows.groupby('point'):
                    if len(q)<2:continue
                    lines.append((int(q.books.min()),-abs(float(q.consensus_prob.mean())-.5),-float(q.consensus_prob.sub(.5).abs().max()),float(point)))
                if lines:
                    line=max(lines)[-1];rows=rows[rows.point.eq(line)]
            direction=rows.sort_values('model_win_prob',ascending=False).iloc[0]
            value=rows.sort_values('point_ev_roi',ascending=False).iloc[0]
            item={'경기':f"{first.home_team} - {first.away_team}",'분석':'승무패' if market=='h2h' else '언더오버',
                '확률이 가장 높은 선택':direction.display_pick.split(' | ',1)[-1],
                '그 선택 추정 확률(%)':round(direction.model_win_prob*100,2),
                '배당 대비 EV가 가장 높은 선택':value.display_pick.split(' | ',1)[-1],
                '배당':value.best_odds,'추정 확률(%)':round(value.model_win_prob*100,2),
                'BE(%)':round(value.break_even*100,2),'Edge(%p)':round(value.edge_pp,2),
                'EV(%)':round(value.point_ev_roi*100,2),
                '가정 최저 EV(%)':round(value.scenario_ev_min*100,2),
                '가정 최고 EV(%)':round(value.scenario_ev_max*100,2),
                '판정':'모델상 양의 EV' if value.point_ev_roi>0 else '현재 배당에서 양의 EV 없음',
                '라인업':'확인' if value.lineup_confirmed else '미확인',
                '조합':'참고 조합 가능' if value.scenario_parlay_eligible else '제외',
                '이유':value.selection_reason}
            if 'v3_decision_status' in rows.columns:
                item.update({'v3 판정':value.v3_decision_status,
                             'Stress 양수 비율(%)':round(float(value.robust_positive_ratio)*100,1),
                             'v3 최저 EV(%)':round(float(value.robust_ev_min)*100,2),
                             '반증 위험':value.counter_case_risk,
                             '신호 커버리지(%)':round(float(value.signal_coverage)*100,1),
                             'v3 조합':'가능' if bool(value.v3_parlay_eligible) else '제외'})
            out.append(item)
    return pd.DataFrame(out)
