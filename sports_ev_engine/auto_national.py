"""Public ESPN scoreboard adapter. No keys, no access-control workarounds."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pandas as pd
import requests
from .free_national import free_pool
from .models.soccer_auto import norm_name
PROVIDER_BUILD='3.0.0'
BASE='https://site.api.espn.com/apis/site/v2/sports/soccer'
LEAGUES=('uefa.nations','fifa.worldq.uefa','uefa.euro','uefa.euroq','fifa.friendly','fifa.world','concacaf.nations.league','fifa.worldq.concacaf','concacaf.gold','fifa.worldq.conmebol','conmebol.america','fifa.worldq.afc','afc.cup','fifa.worldq.caf','caf.nations','fifa.worldq.ofc')

class DataHold(ValueError):pass

def fetch_board(league,year):
    if league not in LEAGUES:raise ValueError('Unsupported public league')
    for attempt in range(2):
        try:
            r=requests.get(f'{BASE}/{league}/scoreboard',params={'dates':str(year),'limit':1000},timeout=(8,15))
            break
        except (requests.Timeout,requests.ConnectionError):
            if attempt:raise
    r.raise_for_status();d=r.json()
    if not isinstance(d.get('events'),list):raise ValueError('공개 소스 응답 형식 변경')
    if len(d['events'])>=1000:raise ValueError('공개 소스 응답 상한: 기록 완전성 확인 불가')
    return d

def league_set(keys):
    keys=' '.join(keys).lower()
    leagues={'fifa.friendly','fifa.world'}
    if any(x in keys for x in ('uefa','euro')):leagues.update(('uefa.nations','uefa.euro','uefa.euroq','fifa.worldq.uefa'))
    if any(x in keys for x in ('concacaf','gold')):leagues.update(('concacaf.nations.league','fifa.worldq.concacaf','concacaf.gold'))
    if any(x in keys for x in ('conmebol','copa')):leagues.update(('conmebol.america','fifa.worldq.conmebol'))
    if any(x in keys for x in ('afc','asian')):leagues.update(('afc.cup','fifa.worldq.afc'))
    if any(x in keys for x in ('caf','africa')) and 'concacaf' not in keys:leagues.update(('caf.nations','fifa.worldq.caf'))
    if 'ofc' in keys:leagues.add('fifa.worldq.ofc')
    if len(leagues)==2:leagues=set(LEAGUES)
    return sorted(leagues)

def event_teams(event):
    c=event['competitions'][0]
    teams={x['homeAway']:x for x in c['competitors']}
    return c,teams['home'],teams['away']

def parse_board(data,league,now=None):
    now=pd.Timestamp(now or datetime.now(timezone.utc)); records=[]; events=[]; excluded=0
    for e in data['events']:
        try:
            c,h,a=event_teams(e);kick=pd.Timestamp(c['date'])
            if kick.tzinfo is None:raise ValueError('missing timezone')
            home=h['team']['displayName'];away=a['team']['displayName']
            status=c['status'];kind=status['type']
            events.append({'id':e['id'],'league':league,'home':home,'away':away,'kickoff':kick.isoformat(),'completed':kind.get('completed') is True,'neutral':c.get('neutralSite')})
            if not kind.get('completed') or kick>=now:continue
            # Full time only. Never reinterpret AET/PEN/abandoned as 90-minute scores.
            if kind.get('name')!='STATUS_FULL_TIME' or status.get('period')!=2:
                excluded+=1;continue
            hg=int(h['score']);ag=int(a['score'])
            if not(0<=hg<=40 and 0<=ag<=40):raise ValueError('invalid goals')
            records.append(dict(date=kick.tz_convert('Asia/Seoul').date(),home=home,away=away,hg=hg,ag=ag,neutral=c.get('neutralSite'),hxg=None,axg=None,source=f'https://www.espn.com/soccer/match/_/gameId/{e["id"]}',kickoff=kick.isoformat()))
        except (KeyError,ValueError,TypeError,IndexError):excluded+=1
    return records,events,excluded

def collect(keys,now=None,fetch=fetch_board,progress=None):
    now=pd.Timestamp(now or datetime.now(timezone.utc)); jobs=[(l,y) for l in league_set(keys) for y in (now.year-1,now.year)]
    records=[];events=[];diagnostics=[]
    def run(job):
        league,year=job
        try:
            r,e,n=parse_board(fetch(league,year),league,now)
            return r,e,{'소스':f'ESPN {league} {year}','상태':'수집 완료','90분 기록':len(r),'제외 기록':n}
        except Exception as exc:return [],[],{'소스':f'ESPN {league} {year}','상태':'수집 실패','이유':str(exc)}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i,(r,e,d) in enumerate(ex.map(run,jobs),1):
            records.extend(r);events.extend(e);diagnostics.append(d)
            if progress:progress(i,len(jobs),d)
    unique={};conflicts=set()
    for r in records:
        k=(r['date'],norm_name(r['home']),norm_name(r['away']))
        if k in conflicts:continue
        if k in unique and (unique[k]['hg'],unique[k]['ag'])!=(r['hg'],r['ag']):
            unique.pop(k);conflicts.add(k);continue
        unique[k]=r
    return list(unique.values()),list({(e['league'],e['id']):e for e in events}.values()),diagnostics

def automatic_pool(records,events,home,away,kickoff,recent_n=6):
    target=pd.Timestamp(kickoff);day=target.tz_convert('Asia/Seoul').date()
    # At least five usable games, and the latest known completed game must be usable.
    for team in (home,away):
        match=lambda r:norm_name(team) in {norm_name(r['home']),norm_name(r['away'])}
        rows=[r for r in records if match(r) and 0<(day-r['date']).days<=730]
        if len(rows)<max(5,recent_n):raise DataHold(f'{team}: 90분 기록 {len(rows)}경기 / 필요 {max(5,recent_n)}경기 — 분석 보류')
        if sum('espn.com' in r['source'] for r in rows)<3:raise DataHold(f'{team}: 최신 공개 대회 기록 수집 부족 — 분석 보류')
        latest=max(r['date'] for r in rows)
        if (day-latest).days>120:raise DataHold(f'{team}: 마지막 기록 {(day-latest).days}일 전 — 분석 보류')
        known=[pd.Timestamp(e['kickoff']).tz_convert('Asia/Seoul').date() for e in events if match(e) and e['completed'] and pd.Timestamp(e['kickoff'])<target]
        if known and max(known)>latest:raise DataHold(f'{team}: 최근 종료 경기의 90분 점수 미확인 — 분석 보류')
    pool=free_pool(records,home,away,kickoff,recent_n)
    pool['data_source']='무료 자동 수집 (경기별 출처 참조)'
    pool['venue_unknown']=True
    return pool

def fetch_lineup(event,now=None):
    now=pd.Timestamp(now or datetime.now(timezone.utc));kick=pd.Timestamp(event['kickoff'])
    if not 0<(kick-now).total_seconds()<=7200:return {}
    r=requests.get(f'{BASE}/{event["league"]}/summary',params={'event':event['id']},timeout=(5,12));r.raise_for_status();data=r.json()
    header=data.get('header',{})
    if str(header.get('id'))!=str(event['id']):raise ValueError('라인업 경기 ID 불일치')
    c,h,a=event_teams(header)
    if pd.Timestamp(c['date'])!=kick or norm_name(h['team']['displayName'])!=norm_name(event['home']) or norm_name(a['team']['displayName'])!=norm_name(event['away']):raise ValueError('라인업 대진/시각 불일치')
    out={}
    for roster in data.get('rosters',[]):
        side=roster.get('homeAway')
        if side not in ('home','away'):continue
        if norm_name(roster['team']['displayName'])!=norm_name(event[side]):continue
        starters=[p['athlete'] for p in roster.get('roster',[]) if p.get('starter') is True]
        if len(starters)==11 and len({p['id'] for p in starters})==11:
            out[side]={'confirmed':True,'attack':0,'defense':0,'neutral':event.get('neutral') is True,'source':f'https://www.espn.com/soccer/lineups/_/gameId/{event["id"]}','checked_at':now.isoformat(),'players':';'.join(p['displayName'] for p in starters)}
    return out
