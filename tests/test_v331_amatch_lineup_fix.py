import pandas as pd
from unittest.mock import Mock, patch

from sports_ev_engine.auto_soccer import analyze_event
from sports_ev_engine.deep_soccer_context import collect_deep_context


def _fixture(fid, ts, home='Turkey', away='Italy', hg=1, ag=0, status='FT'):
    return {
        'fixture': {'id': fid, 'timestamp': ts, 'status': {'short': status}},
        'teams': {'home': {'id': 1, 'name': home}, 'away': {'id': 2, 'name': away}},
        'goals': {'home': hg, 'away': ag},
    }


def test_api_football_confirmed_lineup_promotes_international_event():
    # Enough historical fixtures for both teams.
    cutoff = int(pd.Timestamp('2026-09-29T03:45:00+09:00').timestamp())
    fixtures = []
    for i in range(1, 7):
        fixtures.append(_fixture(100+i, cutoff-i*86400*20, 'Turkey', 'Italy', 1+(i%2), i%2))
    pool = {
        'fixtures': fixtures,
        'international': True,
        'team_names': {'turkiye': 'Turkey', 'italy': 'Italy'},
        'manual_context': {},
        'event_context': {'deep_context_attempted': True, 'lineup_confirmed': True, 'lineup_source': 'API-Football fixtures/lineups'},
    }
    rows = pd.DataFrame([
        {'event_id':'e1','home_team':'Turkey','away_team':'Italy','commence_time':'2026-09-29T03:45:00+09:00',
         'market':'h2h','selection':'Turkey','best_odds':2.5,'consensus_prob':0.40,'best_book':'X'},
        {'event_id':'e1','home_team':'Turkey','away_team':'Italy','commence_time':'2026-09-29T03:45:00+09:00',
         'market':'h2h','selection':'Draw','best_odds':3.2,'consensus_prob':0.30,'best_book':'X'},
        {'event_id':'e1','home_team':'Turkey','away_team':'Italy','commence_time':'2026-09-29T03:45:00+09:00',
         'market':'h2h','selection':'Italy','best_odds':3.3,'consensus_prob':0.30,'best_book':'X'},
    ])
    out, meta = analyze_event(rows, pool, recent_n=4)
    assert not out.empty
    assert out['lineup_confirmed'].all()


def test_deep_context_extracts_names_and_status_from_full_startxi():
    kickoff='2026-09-29T03:45:00+09:00'
    ts=int(pd.Timestamp(kickoff).timestamp())
    target=_fixture(999, ts, status='NS')
    target['goals']={'home':None,'away':None}
    pool={'fixtures':[target], 'international':True}
    api=Mock()
    api.fixtures_by_date.return_value=[target]
    api.fixture_statistics.return_value=[]
    api.lineups.return_value=[
        {'team':{'name':'Turkey'},'formation':'4-2-3-1','startXI':[{'player':{'id':i,'name':f'T{i}'}} for i in range(1,12)]},
        {'team':{'name':'Italy'},'formation':'4-3-3','startXI':[{'player':{'id':100+i,'name':f'I{i}'}} for i in range(1,12)]},
    ]
    api.team_players.return_value=[]
    api.injuries.return_value=[]
    with patch('sports_ev_engine.deep_soccer_context.pd.Timestamp.now',return_value=pd.Timestamp(kickoff).tz_convert('UTC')-pd.Timedelta(hours=1)):
        ctx=collect_deep_context(api,pool,'Turkey','Italy',kickoff,season=2026,horizon_hours=24)
    assert ctx['lineup_confirmed'] is True
    assert ctx['lineup_status']=='CONFIRMED'
    assert len(ctx['home_lineup_players'])==11
    assert len(ctx['away_lineup_players'])==11
    assert ctx['home_formation']=='4-2-3-1'
