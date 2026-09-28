"""Free national-team history and source-labelled manual evidence."""
from io import BytesIO
import math
import pandas as pd
import requests
from sports_ev_engine.models.soccer_auto import norm_name

PROVIDER_BUILD = '2.9.4'
FREE_URL = 'https://raw.githubusercontent.com/martj42/international_results/master/results.csv'
HISTORY_COLUMNS = 'date,home_team,away_team,home_score,away_score,tournament,neutral,score_basis,home_xg,away_xg,source'.split(',')
CONTEXT_COLUMNS = 'kickoff,home_team,away_team,team,lineup_confirmed,lineup_players,missing_players,attack_change_pct,defense_change_pct,neutral,source,checked_at'.split(',')

def blank_csv(columns):
    return (','.join(columns)+'\n').encode('utf-8-sig')

def read_csv(data):
    if len(data)>12_000_000:raise ValueError('CSV는 12MB 이하로 올려주세요.')
    return pd.read_csv(BytesIO(data),keep_default_na=False)

def flag(value):
    s=str(value).strip().lower()
    if s in {'true','1','yes','확정'}:return True
    if s in {'false','0','no','미확정'}:return False
    raise ValueError(f'true/false 값이 필요합니다: {value}')

def number(value,lo,hi):
    v=float(value)
    if not math.isfinite(v) or not lo<=v<=hi:raise ValueError(f'숫자 범위 오류: {value} ({lo}~{hi})')
    return v

def prepare_history(frame, public=False):
    required={'date','home_team','away_team','home_score','away_score','tournament','neutral'}
    if not required.issubset(frame):raise ValueError('경기 기록 CSV 필수 열: '+', '.join(sorted(required)))
    if not public and not {'score_basis','source'}.issubset(frame):raise ValueError('CSV에 score_basis=90, source가 필요합니다.')
    records=[]; skipped=0
    for i,r in frame.iterrows():
        try:
            # The public dataset contains extra-time scores; exclude ambiguous cup games.
            safe=r['tournament']=='Friendly'
            if public and not safe:skipped+=1;continue
            if not public and str(r['score_basis']).strip()!='90':raise ValueError('score_basis는 90이어야 합니다(연장/승부차기 제외).')
            source='Mart Jürisoo international_results (CC0)' if public else str(r['source']).strip()
            if not source:raise ValueError('source가 비어 있습니다.')
            home=str(r['home_team']).strip();away=str(r['away_team']).strip()
            if not home or not away or norm_name(home)==norm_name(away):raise ValueError('팀 이름을 확인하세요.')
            stamp=pd.Timestamp(r['date'])
            if pd.isna(stamp):raise ValueError('유효한 경기 날짜가 필요합니다.')
            d=stamp.date()
            hs=number(r['home_score'],0,40);aws=number(r['away_score'],0,40)
            if int(hs)!=hs or int(aws)!=aws:raise ValueError('득점은 정수여야 합니다.')
            hx=r.get('home_xg','');ax=r.get('away_xg','')
            if (hx=='')!=(ax==''):raise ValueError('양 팀 xG를 함께 입력하세요.')
            records.append({'date':d,'home':home,'away':away,'hg':int(hs),'ag':int(aws),
                'neutral':flag(r['neutral']),'source':source,
                'hxg':None if hx=='' else number(hx,0,15),'axg':None if ax=='' else number(ax,0,15)})
        except Exception as e:raise ValueError(f'경기 기록 {i+2}행: {e}') from e
    # Exact duplicates collapse; conflicting same-game values require correction.
    out={}; conflicts=set()
    for r in records:
        k=(r['date'],norm_name(r['home']),norm_name(r['away']))
        if k in conflicts:continue
        if k in out and any(out[k][v]!=r[v] for v in ('hg','ag','neutral','hxg','axg')):
            if not public:raise ValueError(f'중복 경기 기록 충돌: {k}')
            del out[k];conflicts.add(k);skipped+=2;continue
        out[k]=r
    return list(out.values()),skipped

def download_history():
    response=requests.get(FREE_URL,timeout=(8,20))
    response.raise_for_status()
    return prepare_history(read_csv(response.content),public=True)

def combine_history(base, additions):
    # Explicit user evidence may supply regulation-time results or fresher xG.
    out={(r['date'],norm_name(r['home']),norm_name(r['away'])):r for r in base}
    for r in additions:out[(r['date'],norm_name(r['home']),norm_name(r['away']))]=r
    return list(out.values())

def free_pool(records,home,away,cutoff_iso,recent_n=6):
    cutoff=pd.Timestamp(cutoff_iso);day=cutoff.tz_convert('Asia/Seoul').date()
    # Date-only results on match day are excluded to avoid time leakage.
    valid=[r for r in records if 0<(day-r['date']).days<=1098]
    fixtures=[]
    for r in valid:
        ts=int(pd.Timestamp(r['date'],tz='UTC').timestamp())
        fixtures.append({'fixture':{'timestamp':ts,'status':{'short':'FT'}},'teams':{'home':{'name':r['home']},'away':{'name':r['away']}},'goals':{'home':r['hg'],'away':r['ag']},'neutral':r['neutral'] is not False})
    summaries={}; xg={}
    for side,team in [('home',home),('away',away)]:
        rows=sorted([r for r in valid if norm_name(team) in {norm_name(r['home']),norm_name(r['away'])}],key=lambda r:r['date'],reverse=True)
        if len(rows)<3:raise ValueError(f'{team}: 90분 기록 최소 3경기 필요. 무료 자료 또는 CSV를 보완하세요.')
        age=(day-rows[0]['date']).days
        if age>180:raise ValueError(f'{team}: 마지막 기록이 {age}일 전입니다. 최신 CSV를 보완하세요.')
        recent=rows[:recent_n]; measured=[r for r in recent if r['hxg'] is not None]
        if len(measured)>=3:
            xg[side]=(sum(r['hxg'] if norm_name(r['home'])==norm_name(team) else r['axg'] for r in measured)/len(measured),sum(r['axg'] if norm_name(r['home'])==norm_name(team) else r['hxg'] for r in measured)/len(measured))
        summaries[side]={'last_date':str(rows[0]['date']),'age_days':age,'matches':len(recent),'xg_matches':len(measured),'sources':'; '.join(sorted({r['source'] for r in recent}))}
    return {'fixtures':fixtures,'international':True,'team_names':{},'evidence':summaries,'xg_form':xg,
        'data_source':'무료 공개 기록 + 사용자 CSV','extra_uncertainty':1.0+max(v['age_days'] for v in summaries.values())/90,'manual_context':{}}

def event_context(frame,home,away,kickoff_iso,now=None):
    if frame is None or frame.empty:return {}
    if not set(CONTEXT_COLUMNS).issubset(frame):raise ValueError('라인업/결장 CSV 열을 템플릿과 맞춰주세요.')
    kickoff=pd.Timestamp(kickoff_iso);now=pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    out={}
    for _,r in frame.iterrows():
        if norm_name(r['home_team'])!=norm_name(home) or norm_name(r['away_team'])!=norm_name(away):continue
        when=pd.Timestamp(r['kickoff'])
        if when.tzinfo is None:raise ValueError('kickoff에는 +09:00 등 시간대를 넣으세요.')
        if when!=kickoff:continue
        team=norm_name(r['team']);side='home' if team==norm_name(home) else 'away' if team==norm_name(away) else None
        if side is None:raise ValueError('라인업 팀이 경기 팀과 다릅니다.')
        checked=pd.Timestamp(r['checked_at'])
        if checked.tzinfo is None or checked>min(now,kickoff) or (now-checked).total_seconds()>86400:raise ValueError('checked_at은 최근 24시간의 확인 시각이어야 합니다(시간대 포함).')
        if not str(r['source']).strip():raise ValueError('라인업/결장 출처가 필요합니다.')
        confirmed=flag(r['lineup_confirmed'])
        players=[p.strip() for p in str(r['lineup_players']).split(';') if p.strip()]
        if confirmed and len(set(players))!=11:raise ValueError('확정 라인업은 서로 다른 선수 11명을 세미콜론으로 구분하세요.')
        attack=number(r['attack_change_pct'] or 0,-20,20);defense=number(r['defense_change_pct'] or 0,-20,20)
        if (attack or defense) and not str(r['missing_players']).strip():raise ValueError('결장 보정에는 대상 선수 이름이 필요합니다.')
        if side in out:raise ValueError('동일 팀 라인업/결장 행이 중복됐습니다.')
        out[side]={'confirmed':confirmed,'attack':attack,'defense':defense,'neutral':flag(r['neutral']),'source':r['source'],'missing':r['missing_players'],'checked_at':str(checked),'players':';'.join(players)}
    if len(out)==2 and out['home']['neutral']!=out['away']['neutral']:raise ValueError('양 팀 중립구장 값이 다릅니다.')
    return out

def adjust_lambdas(hl,al,pool):
    xg=pool.get('xg_form',{});ctx=pool.get('manual_context',{});notes=[]
    if 'home' in xg and 'away' in xg:
        hx=(xg['home'][0]+xg['away'][1])/2;ax=(xg['away'][0]+xg['home'][1])/2
        hl=.75*hl+.25*max(.8*hl,min(1.2*hl,hx))
        al=.75*al+.25*max(.8*al,min(1.2*al,ax));notes.append('xG 반영(양 팀 각 3경기 이상)')
    else:notes.append('xG 미반영(표본 부족/미수집)')
    h=ctx.get('home',{});a=ctx.get('away',{})
    hl*=1+(h.get('attack',0)+a.get('defense',0))/100
    al*=1+(a.get('attack',0)+h.get('defense',0))/100
    confirmed=bool(h.get('confirmed') and a.get('confirmed'))
    notes.append('양 팀 선발 명단 확인' if confirmed else '라인업 미확인: 다폴 제외')
    for side,c in ctx.items():
        notes.append(f"{side} 출처: {c.get('source','')} / 확인: {c.get('checked_at','')}")
    if any(c.get('attack') or c.get('defense') for c in ctx.values()):notes.append('사용자 결장 보정 반영(미검증 계수)')
    return max(.15,hl),max(.15,al),confirmed,'; '.join(notes)
