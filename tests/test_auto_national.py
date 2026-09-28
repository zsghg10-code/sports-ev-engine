import unittest
from datetime import date,timedelta
from unittest.mock import patch,Mock
from sports_ev_engine.auto_national import *


def event(i=1,kind='STATUS_FULL_TIME',period=2,date='2026-09-20T12:00Z'):
    return {'id':str(i),'competitions':[{'date':date,'status':{'period':period,'type':{'completed':True,'name':kind}},'competitors':[{'homeAway':'home','score':'2','team':{'displayName':'Turkey'}},{'homeAway':'away','score':'1','team':{'displayName':'Italy'}}]}]}

class AutoTests(unittest.TestCase):
    def test_score_basis_and_future(self):
        data={'events':[event(),event(2,'STATUS_FINAL_AET',4),event(3,date='2026-10-01T12:00Z')]}
        r,e,n=parse_board(data,'uefa.nations',now='2026-09-28T10:00Z')
        self.assertEqual(len(r),1);self.assertEqual(n,1);self.assertEqual(len(e),3)
    def test_hold_missing_and_stale(self):
        with self.assertRaises(DataHold):automatic_pool([],[],'Turkey','Italy','2026-09-29T01:00+09:00')
        data={'events':[event(i,date=f'2026-09-{i+1:02}T12:00Z') for i in range(6)]}
        r,e,_=parse_board(data,'uefa.nations',now='2026-09-28T10:00Z')
        self.assertTrue(automatic_pool(r,e,'Turkey','Italy','2026-09-29T01:00+09:00')['fixtures'])
        e.append({'home':'Turkey','away':'Italy','completed':True,'kickoff':'2026-09-27T12:00Z'})
        with self.assertRaises(DataHold):automatic_pool(r,e,'Turkey','Italy','2026-09-29T01:00+09:00')
    def test_source_failure_reported(self):
        def fail(*args):raise TimeoutError('test timeout')
        r,e,d=collect(['soccer_uefa_nations_league'],now='2026-09-28T10:00Z',fetch=fail)
        self.assertFalse(r);self.assertTrue(all(x['상태']=='수집 실패' for x in d))
    def test_lineup_identity_and_eleven(self):
        e={'id':'1','league':'uefa.nations','home':'Turkey','away':'Italy','kickoff':'2026-09-28T12:00Z','neutral':None}
        header=event(date=e['kickoff'])
        roster=[{'homeAway':side,'team':{'displayName':name},'roster':[{'starter':True,'athlete':{'id':str(i),'displayName':str(i)}} for i in range(11)]} for side,name in [('home','Turkey'),('away','Italy')]]
        response=Mock();response.json.return_value={'header':header,'rosters':roster}
        with patch('sports_ev_engine.auto_national.requests.get',return_value=response):
            self.assertEqual(len(fetch_lineup(e,now='2026-09-28T11:00Z')),2)
            roster[0]['roster'].pop()
            self.assertNotIn('home',fetch_lineup(e,now='2026-09-28T11:00Z'))
            header['id']='WRONG'
            with self.assertRaises(ValueError):fetch_lineup(e,now='2026-09-28T11:00Z')
        self.assertEqual(fetch_lineup(e,now='2026-09-28T01:00Z'),{})

if __name__=='__main__':unittest.main()
