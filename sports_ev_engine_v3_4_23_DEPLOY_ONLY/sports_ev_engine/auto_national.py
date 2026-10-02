"""Public ESPN scoreboard adapter. No keys, no access-control workarounds."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import pandas as pd
import requests
from functools import lru_cache
from .free_national import free_pool
from .models.soccer_auto import norm_name
PROVIDER_BUILD='3.0.0'
PATCH_BUILD = '3.4.24-national-validation'
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


def fetch_summary(league,event_id):
    if league not in LEAGUES: raise ValueError('Unsupported public league')
    r=requests.get(f'{BASE}/{league}/summary',params={'event':str(event_id)},timeout=(5,12))
    r.raise_for_status(); data=r.json()
    if str((data.get('header') or {}).get('id'))!=str(event_id):
        raise ValueError('공개 통계 경기 ID 불일치')
    return data

def _num_xg(v):
    if isinstance(v,dict):
        v=v.get('value',v.get('displayValue'))
    if isinstance(v,str):
        v=v.strip().replace(',','')
    try:
        x=float(v)
    except (TypeError,ValueError):
        return None
    return x if 0<=x<=15 else None

def _xg_from_stats(stats):
    for st in stats or []:
        name=' '.join(str(st.get(k) or '') for k in ('name','displayName','label','abbreviation')).lower()
        key=''.join(ch for ch in name if ch.isalnum())
        if ('expectedgoal' in key) or key in {'xg','xgoal','xgoals'}:
            x=_num_xg(st.get('value',st.get('displayValue')))
            if x is not None:return x
    return None

def _summary_xg(data,event):
    """Conservatively extract ESPN xG only when it is explicitly team-labelled."""
    wanted={'home':norm_name(event['home']),'away':norm_name(event['away'])}
    out={}
    candidates=[]
    box=data.get('boxscore') or {}
    candidates.extend(box.get('teams') or [])
    header=data.get('header') or {}
    try:candidates.extend((header.get('competitions') or [])[0].get('competitors') or [])
    except (IndexError,AttributeError):pass
    for row in candidates:
        team=row.get('team') or {}
        tname=norm_name(team.get('displayName') or team.get('name') or row.get('displayName') or '')
        side='home' if tname==wanted['home'] else 'away' if tname==wanted['away'] else None
        if not side:continue
        x=_xg_from_stats(row.get('statistics') or row.get('stats') or [])
        if x is not None:out[side]=x
    return out if set(out)=={'home','away'} else {}

def fetch_match_xg(event,fetch=fetch_summary):
    data=fetch(event['league'],event['id'])
    header=data.get('header') or {}
    c,h,a=event_teams(header)
    kick=pd.Timestamp(event['kickoff'])
    if pd.Timestamp(c['date'])!=kick or norm_name(h['team']['displayName'])!=norm_name(event['home']) or norm_name(a['team']['displayName'])!=norm_name(event['away']):
        raise ValueError('공개 xG 대진/시각 불일치')
    x=_summary_xg(data,event)
    if not x:return {}
    return {'home_xg':x['home'],'away_xg':x['away'],'source':f'https://www.espn.com/soccer/match/_/gameId/{event["id"]}'}


FOTMOB_BASES=("https://www.fotmob.com/api/data","https://www.fotmob.com/api")
FOTMOB_HEADERS={"User-Agent":"Mozilla/5.0 (compatible; SportsEVEngine/3.4.5; measured-xG fallback)","Accept":"application/json,text/plain,*/*"}


def _team_equiv(a,b):
    """Conservative country/team name match across public providers."""
    na=norm_name(a); nb=norm_name(b)
    if na==nb:return True
    def toks(x):
        return {t for t in x.replace(' and ',' ').split() if t not in {'fc','cf','national','team','the'}}
    ta,tb=toks(na),toks(nb)
    return bool(ta and tb and ta==tb)


def _fotmob_json(path,params):
    last=None
    for base in FOTMOB_BASES:
        try:
            r=requests.get(f"{base}/{path}",params=params,headers=FOTMOB_HEADERS,timeout=(5,12))
            if r.status_code in (403,404):
                last=ValueError(f"FotMob {r.status_code}")
                continue
            r.raise_for_status()
            ctype=(r.headers.get('content-type') or '').lower()
            if 'json' not in ctype and not r.text.lstrip().startswith(('{','[')):
                raise ValueError('FotMob non-JSON response')
            return r.json()
        except (requests.RequestException,ValueError) as exc:
            last=exc
    if last:raise last
    raise ValueError('FotMob unavailable')


@lru_cache(maxsize=96)
def _fotmob_day(day_yyyymmdd):
    return _fotmob_json('matches',{'date':day_yyyymmdd})


@lru_cache(maxsize=256)
def _fotmob_detail(match_id):
    return _fotmob_json('matchDetails',{'matchId':str(match_id)})


def _fotmob_matches(payload):
    rows=[]
    for league in (payload or {}).get('leagues') or []:
        for m in league.get('matches') or []:
            if isinstance(m,dict):rows.append(m)
    return rows


def _fotmob_match_kickoff(m):
    raw=((m.get('status') or {}).get('utcTime') or m.get('utcTime') or m.get('matchTimeUTCDate'))
    if not raw:return pd.NaT
    return pd.to_datetime(raw,utc=True,errors='coerce')


def _find_fotmob_match(event,day_fetch=_fotmob_day):
    """Find the same completed event without trusting names alone."""
    kick=pd.to_datetime(event.get('kickoff'),utc=True,errors='coerce')
    if pd.isna(kick):return None
    # UTC day first; only inspect adjacent dates when necessary. Day payloads are cached.
    days=[kick.date(),(kick-timedelta(days=1)).date(),(kick+timedelta(days=1)).date()]
    best=None
    for d in days:
        try: payload=day_fetch(d.strftime('%Y%m%d'))
        except Exception: continue
        for m in _fotmob_matches(payload):
            home=((m.get('home') or {}).get('name') or (m.get('homeTeam') or {}).get('name') or '')
            away=((m.get('away') or {}).get('name') or (m.get('awayTeam') or {}).get('name') or '')
            if not (_team_equiv(home,event.get('home','')) and _team_equiv(away,event.get('away',''))):continue
            mk=_fotmob_match_kickoff(m)
            delta=abs((mk-kick).total_seconds()) if not pd.isna(mk) else 9e9
            if delta>8*3600:continue
            status=m.get('status') or {}
            finished=status.get('finished') is True or m.get('finished') is True
            if event.get('completed') and not finished:continue
            if best is None or delta<best[0]:best=(delta,m)
        if best and best[0]<=2*3600:break
    return best[1] if best else None


def _fotmob_stats_xg(detail):
    """Extract the canonical team xG pair from FotMob's All-period stats block."""
    periods=(((detail or {}).get('content') or {}).get('stats') or {}).get('Periods') or {}
    allp=periods.get('All') or periods.get('ALL') or periods.get('all') or {}
    roots=[]
    if isinstance(allp,dict):
        roots=allp.get('stats') or []
    elif isinstance(allp,list):roots=allp
    found=None
    def walk(obj):
        nonlocal found
        if found is not None:return
        if isinstance(obj,dict):
            title=' '.join(str(obj.get(k) or '') for k in ('title','key','name','label')).lower()
            key=''.join(ch for ch in title if ch.isalnum())
            vals=obj.get('stats')
            is_xg=(key in {'xg','expectedgoals','expectedgoalsxg'} or ('expectedgoals' in key and 'ontarget' not in key and 'xgot' not in key))
            if is_xg and isinstance(vals,(list,tuple)) and len(vals)>=2:
                h=_num_xg(vals[0]);a=_num_xg(vals[1])
                if h is not None and a is not None:
                    found={'home':h,'away':a};return
            # Some unofficial payload mirrors use home/away scalar fields.
            if is_xg:
                h=_num_xg(obj.get('home'));a=_num_xg(obj.get('away'))
                if h is not None and a is not None:
                    found={'home':h,'away':a};return
            for v in obj.values():walk(v)
        elif isinstance(obj,list):
            for v in obj:walk(v)
    walk(roots)
    return found or {}


def fetch_match_xg_fotmob(event,day_fetch=_fotmob_day,detail_fetch=_fotmob_detail):
    """Measured xG fallback from a matched FotMob finished match; never estimate xG."""
    m=_find_fotmob_match(event,day_fetch=day_fetch)
    if not m:return {}
    mid=m.get('id') or m.get('matchId')
    if not mid:return {}
    data=detail_fetch(str(mid))
    general=(data or {}).get('general') or {}
    home=((general.get('homeTeam') or {}).get('name') or '')
    away=((general.get('awayTeam') or {}).get('name') or '')
    if home and not _team_equiv(home,event.get('home','')):raise ValueError('FotMob xG 홈팀 불일치')
    if away and not _team_equiv(away,event.get('away','')):raise ValueError('FotMob xG 원정팀 불일치')
    gx=pd.to_datetime(general.get('matchTimeUTCDate'),utc=True,errors='coerce')
    ex=pd.to_datetime(event.get('kickoff'),utc=True,errors='coerce')
    if not pd.isna(gx) and not pd.isna(ex) and abs((gx-ex).total_seconds())>8*3600:
        raise ValueError('FotMob xG 경기시각 불일치')
    x=_fotmob_stats_xg(data)
    if not x:return {}
    return {'home_xg':x['home'],'away_xg':x['away'],'source':f'https://www.fotmob.com/matches/{mid}'}




SOFA_BASE="https://api.sofascore.com/api/v1"
SOFA_HEADERS={"User-Agent":"Mozilla/5.0 (compatible; SportsEVEngine/3.4.16; measured-xG fallback)","Accept":"application/json,text/plain,*/*"}


def _sofa_json(path):
    r=requests.get(f"{SOFA_BASE}/{path.lstrip('/')}",headers=SOFA_HEADERS,timeout=(5,12))
    r.raise_for_status()
    ctype=(r.headers.get('content-type') or '').lower()
    if 'json' not in ctype and not r.text.lstrip().startswith(('{','[')):
        raise ValueError('SofaScore non-JSON response')
    return r.json()


@lru_cache(maxsize=96)
def _sofa_day(day_iso):
    return _sofa_json(f"sport/football/scheduled-events/{day_iso}")


@lru_cache(maxsize=256)
def _sofa_stats(event_id):
    return _sofa_json(f"event/{event_id}/statistics")


def _sofa_event_kickoff(e):
    raw=e.get('startTimestamp')
    try:
        return pd.to_datetime(int(raw),unit='s',utc=True)
    except (TypeError,ValueError,OverflowError):
        return pd.NaT


def _find_sofa_match(event,day_fetch=_sofa_day):
    """Match one completed event on SofaScore by teams + kickoff, never by names alone."""
    kick=pd.to_datetime(event.get('kickoff'),utc=True,errors='coerce')
    if pd.isna(kick):return None
    days=[kick.date(),(kick-timedelta(days=1)).date(),(kick+timedelta(days=1)).date()]
    best=None
    for d in days:
        try: payload=day_fetch(d.isoformat())
        except Exception: continue
        for e in (payload or {}).get('events') or []:
            if not isinstance(e,dict):continue
            home=((e.get('homeTeam') or {}).get('name') or '')
            away=((e.get('awayTeam') or {}).get('name') or '')
            if not (_team_equiv(home,event.get('home','')) and _team_equiv(away,event.get('away',''))):continue
            ek=_sofa_event_kickoff(e)
            delta=abs((ek-kick).total_seconds()) if not pd.isna(ek) else 9e9
            if delta>8*3600:continue
            st=e.get('status') or {}
            finished=(str(st.get('type') or '').lower()=='finished' or st.get('code')==100)
            if event.get('completed') and not finished:continue
            if best is None or delta<best[0]:best=(delta,e)
        if best and best[0]<=2*3600:break
    return best[1] if best else None


def _sofa_stats_xg(payload):
    """Extract measured full-match xG only when SofaScore explicitly labels it."""
    periods=(payload or {}).get('statistics') or []
    allp=None
    for block in periods:
        if str(block.get('period') or '').upper()=='ALL':
            allp=block;break
    if allp is None and periods:
        allp=periods[0]
    for group in (allp or {}).get('groups') or []:
        for item in group.get('statisticsItems') or []:
            label=' '.join(str(item.get(k) or '') for k in ('name','key')).lower()
            key=''.join(ch for ch in label if ch.isalnum())
            if not (key in {'xg','expectedgoals','expectedgoalsxg'} or ('expectedgoals' in key and 'ontarget' not in key and 'xgot' not in key)):
                continue
            h=_num_xg(item.get('homeValue',item.get('home')))
            a=_num_xg(item.get('awayValue',item.get('away')))
            if h is not None and a is not None:
                return {'home':h,'away':a}
    return {}


def fetch_match_xg_sofascore(event,day_fetch=_sofa_day,stats_fetch=_sofa_stats):
    """Measured xG fallback from a matched finished SofaScore event."""
    e=_find_sofa_match(event,day_fetch=day_fetch)
    if not e:return {}
    eid=e.get('id')
    if not eid:return {}
    payload=stats_fetch(str(eid))
    x=_sofa_stats_xg(payload)
    if not x:return {}
    return {'home_xg':x['home'],'away_xg':x['away'],'source':f'https://www.sofascore.com/event/{eid}'}

def fetch_match_xg_multi(event,espn_fetch=fetch_summary,fotmob_day_fetch=_fotmob_day,fotmob_detail_fetch=_fotmob_detail,sofa_day_fetch=_sofa_day,sofa_stats_fetch=_sofa_stats):
    """Measured-xG cascade: ESPN -> FotMob -> SofaScore. Missing stays missing."""
    errors=[]
    try:
        rec=fetch_match_xg(event,fetch=espn_fetch)
        if rec:
            rec['provider']='ESPN';return rec
        errors.append('ESPN:NO_XG')
    except Exception as exc:errors.append(f'ESPN:{type(exc).__name__}: {str(exc)[:160]}')
    try:
        rec=fetch_match_xg_fotmob(event,day_fetch=fotmob_day_fetch,detail_fetch=fotmob_detail_fetch)
        if rec:
            rec['provider']='FotMob';return rec
        errors.append('FotMob:NO_XG_OR_MATCH')
    except Exception as exc:errors.append(f'FotMob:{type(exc).__name__}: {str(exc)[:160]}')
    try:
        rec=fetch_match_xg_sofascore(event,day_fetch=sofa_day_fetch,stats_fetch=sofa_stats_fetch)
        if rec:
            rec['provider']='SofaScore';return rec
        errors.append('SofaScore:NO_XG_OR_MATCH')
    except Exception as exc:errors.append(f'SofaScore:{type(exc).__name__}: {str(exc)[:160]}')
    return {'errors':errors} if errors else {}

def collect_recent_xg(events,home,away,kickoff,n=3,fetch=fetch_summary,allow_fotmob=None,allow_sofascore=True):
    """Collect recent measured xG from ESPN/FotMob/SofaScore; never synthesize xG."""
    target=pd.Timestamp(kickoff)
    if target.tzinfo is None:target=target.tz_localize('UTC')
    else:target=target.tz_convert('UTC')
    if allow_fotmob is None: allow_fotmob=(fetch is fetch_summary)
    cache={}; providers=set(); errors=[]
    attempts=['ESPN']
    if allow_fotmob: attempts.append('FotMob')
    if allow_sofascore: attempts.append('SofaScore')
    def profile(team):
        cand=[]
        for e in events or []:
            try:
                ek=pd.Timestamp(e['kickoff']);ek=ek.tz_convert('UTC') if ek.tzinfo else ek.tz_localize('UTC')
            except Exception:continue
            if not e.get('completed') or ek>=target:continue
            if not (_team_equiv(team,e.get('home','')) or _team_equiv(team,e.get('away',''))):continue
            cand.append((ek,e))
        cand.sort(key=lambda z:z[0],reverse=True)
        xf=[];xa=[]
        checked=0
        for _,e in cand[:max(12,n+6)]:
            checked+=1
            k=(e.get('league'),str(e.get('id')))
            try:
                rec=cache.get(k)
                if rec is None:
                    if allow_fotmob:
                        rec=fetch_match_xg_multi(e,espn_fetch=fetch) if allow_sofascore else fetch_match_xg_multi(e,espn_fetch=fetch,sofa_day_fetch=lambda *_:{},sofa_stats_fetch=lambda *_:{})
                    else:
                        rec=fetch_match_xg(e,fetch=fetch)
                    cache[k]=rec
            except Exception as exc:
                errors.append(f'collector:{type(exc).__name__}: {exc}')
                rec={};cache[k]=rec
            if rec and rec.get('errors'):
                errors.extend(str(x) for x in rec.get('errors') or [])
            if not rec or rec.get('home_xg') is None or rec.get('away_xg') is None:continue
            if rec.get('provider'):
                providers.add(str(rec.get('provider')))
            elif 'espn.com' in str(rec.get('source') or '').lower():
                providers.add('ESPN')
            elif 'fotmob.com' in str(rec.get('source') or '').lower():
                providers.add('FotMob')
            if _team_equiv(e.get('home',''),team):
                xf.append(float(rec['home_xg']));xa.append(float(rec['away_xg']))
            else:
                xf.append(float(rec['away_xg']));xa.append(float(rec['home_xg']))
            if len(xf)>=n:break
        return {'for':sum(xf)/len(xf) if len(xf)>=n else None,'against':sum(xa)/len(xa) if len(xa)>=n else None,'games':len(xf),'candidates':len(cand),'checked':checked}
    hp=profile(home);ap=profile(away)
    return {
        'home_xg_for':hp['for'],'home_xg_against':hp['against'],
        'away_xg_for':ap['for'],'away_xg_against':ap['against'],
        'xg_samples_home':hp['games'],'xg_samples_away':ap['games'],
        'xg_candidates_home':hp['candidates'],'xg_candidates_away':ap['candidates'],
        'xg_checked_home':hp['checked'],'xg_checked_away':ap['checked'],
        'xg_source':('ESPN public match-summary xG fallback' if providers=={'ESPN'} else 'FotMob public match-details xG fallback' if providers=={'FotMob'} else 'SofaScore public match-statistics xG fallback' if providers=={'SofaScore'} else ' + '.join(sorted(providers))+' public measured xG fallback' if providers else 'public measured xG fallback'),
        'xg_sources_tried':' → '.join(attempts),
        'xg_errors':' · '.join(list(dict.fromkeys(errors))[:12]),
        'xg_collector_error':any(str(error).startswith('collector:') for error in errors),
        'xg_partial': bool((hp['games'] or ap['games']) and not (hp['for'] is not None and ap['for'] is not None)),
        'xg_checked_at':datetime.now(timezone.utc).isoformat(),
    }
