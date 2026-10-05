from datetime import datetime, timezone
import pytest
from sports_ev_engine.pro_sports import ESPNProContext, _nhl_matrix, _line_prob_from_matrix, PRO_SPORTS_BUILD

class Resp:
    def __init__(self,data,code=200): self.data=data; self.code=code
    def raise_for_status(self):
        if self.code>=400: raise RuntimeError(f"HTTP {self.code}")
    def json(self): return self.data
class FakeSession:
    def __init__(self): self.calls=[]
    def get(self,url,params=None,timeout=None):
        token=(params or {}).get("dates",""); self.calls.append(token)
        if "-" in token: return Resp({},400)
        dates={"202604":"2026-04-20T00:00:00Z","202605":"2026-05-05T00:00:00Z","202609":"2026-09-20T00:00:00Z","202610":"2026-10-04T00:00:00Z"}
        if token not in dates: return Resp({"events":[]})
        return Resp({"events":[{"id":token,"date":dates[token],"status":{"type":{"completed":True}},"competitions":[{"competitors":[{"team":{"displayName":"Home Team"},"score":"3"},{"team":{"displayName":"Away Team"},"score":"2"}]}]}]})

def test_version(): assert "3.6.4" in PRO_SPORTS_BUILD

def test_month_synthesis_never_uses_raw_ranges():
    sess=FakeSession(); ctx=ESPNProContext("icehockey_nhl",session=sess)
    board=ctx.scoreboard(datetime(2026,4,1,tzinfo=timezone.utc),datetime(2026,10,6,tzinfo=timezone.utc))
    assert len(board["events"])==4
    assert all("-" not in x for x in sess.calls)
    assert "202604" in sess.calls and "202610" in sess.calls

def test_recent_prior_season_fallback_is_nonzero():
    sess=FakeSession(); ctx=ESPNProContext("icehockey_nhl",session=sess)
    r,err=ctx.recent("Home Team","2026-10-06T01:00:00Z",n=10)
    assert not err and r.games>=4 and r.effective_games>1.5
    assert r.current_season_games>=2 and r.prior_season_games>=2

def test_regulation_three_way_sums_to_one():
    m=_nhl_matrix(3.0,3.0)
    ph,_=_line_prob_from_matrix(m,market="h2h",selection="Home",point=None,home="Home",away="Away",h2h_scope="REGULATION_3WAY")
    pa,_=_line_prob_from_matrix(m,market="h2h",selection="Away",point=None,home="Home",away="Away",h2h_scope="REGULATION_3WAY")
    pd,_=_line_prob_from_matrix(m,market="h2h",selection="Draw",point=None,home="Home",away="Away",h2h_scope="REGULATION_3WAY")
    assert ph+pa+pd==pytest.approx(1.0,abs=1e-9)
