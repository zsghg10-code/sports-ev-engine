"""Chronological validation of raw regulation-time national-team probabilities.
No historical odds: this does NOT validate the live market blend or betting ROI.
"""
import math
import pandas as pd
from .models.elo import build_elo,opponent_adjusted_form
from .models.soccer_auto import norm_name,score_matrix,price_from_matrix

MODEL_ID='national-raw-v3.4.24'

def temperature(prob,t):
    values=[max(1e-12,float(p))**(1/float(t)) for p in prob]
    return [v/sum(values) for v in values]

def metrics(rows,t=1):
    if not rows:return {'n':0}
    brier=loss=0
    for row in rows:
        p=temperature(row['p'],t);y=row['y']
        brier+=sum((v-(i==y))**2 for i,v in enumerate(p))
        loss-=math.log(max(1e-12,p[y]))
    return {'n':len(rows),'brier':brier/len(rows),'log_loss':loss/len(rows)}

def validate(records,as_of,recent_n=6,max_events=1200):
    from .auto_soccer import _build_lambdas
    day=pd.Timestamp(as_of).date()
    valid=[r for r in records if 0<(day-r['date']).days<=1098]
    valid.sort(key=lambda r:(r['date'],norm_name(r['home']),norm_name(r['away'])))
    fixtures=[]
    for r in valid:
        fixtures.append({'fixture':{'timestamp':int(pd.Timestamp(r['date'],tz='UTC').timestamp()),'status':{'short':'FT'}},'teams':{'home':{'name':r['home']},'away':{'name':r['away']}},'goals':{'home':r['hg'],'away':r['ag']},'neutral':r['neutral'] is not False})
    predictions=[];skipped=0
    # All earlier data remain training history; cap only evaluation targets.
    targets=valid[-max_events:]
    for r in targets:
        ts=int(pd.Timestamp(r['date'],tz='UTC').timestamp())
        past=[f for f in fixtures if f['fixture']['timestamp']<ts]
        ratings=build_elo(past,home_adv=20)
        hf=opponent_adjusted_form(past,r['home'],ratings,ts,recent_n)
        af=opponent_adjusted_form(past,r['away'],ratings,ts,recent_n)
        if not hf or not af or min(hf['matches'],af['matches'])<max(5,recent_n):skipped+=1;continue
        # Live automatic path has unknown venue until matched: reproduce zero home advantage.
        hl,al=_build_lambdas(hf,af,ratings.get(norm_name(r['home']),1500),ratings.get(norm_name(r['away']),1500),home_adv=0)
        m=score_matrix(hl,al)
        p=[price_from_matrix(m,'h2h',side,None)[0] for side in ('home','draw','away')]
        predictions.append({'date':str(r['date']),'home':r['home'],'away':r['away'],'p':p,'y':0 if r['hg']>r['ag'] else 1 if r['hg']==r['ag'] else 2,'source':r['source']})
    dates=sorted({p['date'] for p in predictions})
    if len(dates)<10 or len(predictions)<100:
        return {'model_id':MODEL_ID,'status':'표본 부족','n':len(predictions),'skipped':skipped,'live_temperature':1.0,'market_blend_validated':False},predictions
    split=dates[int(len(dates)*.7)]
    train=[p for p in predictions if p['date']<split];test=[p for p in predictions if p['date']>=split]
    chosen=min((.7,.85,1,1.15,1.35,1.6,2.0),key=lambda t:metrics(train,t)['log_loss'])
    before=metrics(test);after=metrics(test,chosen)
    adopted=len(train)>=100 and len(test)>=100 and after['log_loss']<before['log_loss'] and after['brier']<before['brier']
    bins=[]
    for lo,hi in ((0,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.00001)):
        pairs=[(p,i==r['y']) for r in test for i,p in enumerate(temperature(r['p'],chosen if adopted else 1)) if lo<=p<hi]
        if pairs:bins.append({'bin':f'{lo:.1f}-{min(hi,1):.1f}','n':len(pairs),'mean_predicted':sum(p for p,y in pairs)/len(pairs),'observed_frequency':sum(y for p,y in pairs)/len(pairs)})
    return {'model_id':MODEL_ID,'status':'보정 채택' if adopted else '보정 미채택','n':len(predictions),'skipped':skipped,'train_n':len(train),'test_n':len(test),'split_date':split,'start_date':predictions[0]['date'],'end_date':predictions[-1]['date'],'chosen_temperature':chosen,'live_temperature':chosen if adopted else 1.0,'holdout_before':before,'holdout_after':after,'bins':bins,'market_blend_validated':False,'scope':'90분 승무패 원모델만. 현재 수정된 기록의 소급 검증; 당시 수집 상태·배당·라인업·EV/ROI 미검증'},predictions


def load_validation_report(path='data/validation_report.json'):
    import json
    from pathlib import Path
    try:
        report=json.loads(Path(path).read_text(encoding='utf-8'))
        return report if isinstance(report,dict) else {}
    except (OSError,ValueError): return {}

def validation_summary(report):
    """Report observed evidence without conflating raw-model testing with ROI."""
    report=report or {}
    holdout=report.get('holdout_before') or {}
    def number(x):
        try: return math.isfinite(float(x))
        except (TypeError,ValueError): return False
    oos=bool(report.get('split_date')) and int(report.get('test_n') or 0)>=100
    probability=oos and all(number(holdout.get(k)) for k in ('brier','log_loss')) and bool(report.get('bins'))
    profitability=(probability and report.get('market_blend_validated') is True
                   and report.get('roi_validated') is True and number(report.get('roi'))
                   and int(report.get('roi_n') or 0)>=100)
    return {'probability_validated':probability,'profitability_validated':profitability,
            'completed_count':int(probability)+int(profitability),
            'status':'수익성 검증 완료' if profitability else '확률 성능 검증 · 장기 수익성 미검증' if probability else '장기 수익성 미검증',
            'brier':holdout.get('brier'),'log_loss':holdout.get('log_loss'),
            'roi':report.get('roi'),'calibration':report.get('bins',[]),'out_of_sample_n':report.get('test_n',0),
            'scope':report.get('scope','검증 보고서 없음')}
