
import math, re, unicodedata
from sports_ev_engine.core.asian import settle_total_under, settle_total_over, settle_home_handicap

ALIASES = {
    "republic of ireland":"ireland",
    "usa":"united states",
    "south korea":"korea republic",
    "north korea":"korea dpr",
    "czech republic":"czechia",
}

def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii","ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+"," ",s)
    s = re.sub(r"\s+"," ",s).strip()
    return ALIASES.get(s,s)

def choose_team(search_results, target_name):
    if not search_results:
        return None
    target=norm_name(target_name)
    scored=[]
    for item in search_results:
        team=item.get("team",{})
        name=norm_name(team.get("name",""))
        score=0
        if name==target: score+=100
        if target in name or name in target: score+=40
        # national teams preferred for country-like competitions
        if team.get("national"): score+=20
        scored.append((score,team))
    scored.sort(key=lambda x:x[0],reverse=True)
    return scored[0][1] if scored else None

def recent_form(fixtures, team_id, decay=0.86):
    gf=ga=pts=w=0.0
    matches=0
    for i,fx in enumerate(reversed(fixtures)):  # most recent gets largest weight below
        teams=fx.get("teams",{})
        goals=fx.get("goals",{})
        home=teams.get("home",{}).get("id")
        away=teams.get("away",{}).get("id")
        hg=goals.get("home")
        ag=goals.get("away")
        if hg is None or ag is None:
            continue
        weight=decay**i
        if home==team_id:
            tgf,tga=hg,ag
        elif away==team_id:
            tgf,tga=ag,hg
        else:
            continue
        gf += weight*tgf
        ga += weight*tga
        pts += weight*(3 if tgf>tga else 1 if tgf==tga else 0)
        w += weight
        matches += 1
    if w==0:
        return {"gf":1.25,"ga":1.25,"ppg":1.5,"matches":0}
    return {"gf":gf/w,"ga":ga/w,"ppg":pts/w,"matches":matches}

def build_lambdas(home_form, away_form, comp_goal_mean=2.55, home_adv=0.16):
    # Independent recent-form Poisson, shrunk to competition environment.
    base=comp_goal_mean/2
    h_raw=((home_form["gf"] + away_form["ga"])/2) + home_adv
    a_raw=max(0.15,(away_form["gf"] + home_form["ga"])/2)
    # modest points-form adjustment
    h_adj=1 + 0.06*((home_form["ppg"]-away_form["ppg"])/1.5)
    a_adj=1 - 0.06*((home_form["ppg"]-away_form["ppg"])/1.5)
    h=max(.15,h_raw*h_adj)
    a=max(.15,a_raw*a_adj)
    # shrink 30% toward league environment
    total=h+a
    target=comp_goal_mean
    desired=.70*total+.30*target
    factor=desired/max(total,1e-9)
    return h*factor,a*factor

def poisson(k,lam):
    return math.exp(-lam)*(lam**k)/math.factorial(k)

def score_matrix(hl,al,max_goals=10):
    out={}; s=0.0
    for h in range(max_goals+1):
        ph=poisson(h,hl)
        for a in range(max_goals+1):
            p=ph*poisson(a,al)
            out[(h,a)]=p; s+=p
    return {k:v/s for k,v in out.items()}

def price_from_matrix(matrix, market, side=None, line=None):
    w=p=l=0.0
    for (hg,ag),prob in matrix.items():
        if market=="h2h":
            if side=="home":
                o=(1,0,0) if hg>ag else (0,0,1)
            elif side=="away":
                o=(1,0,0) if ag>hg else (0,0,1)
            elif side=="draw":
                o=(1,0,0) if hg==ag else (0,0,1)
            else: raise ValueError(side)
        elif market=="totals":
            o=settle_total_over(hg+ag,line) if side=="over" else settle_total_under(hg+ag,line)
        elif market=="spreads":
            o=settle_home_handicap(hg,ag,line) if side=="home" else settle_home_handicap(ag,hg,line)
        else:
            raise ValueError(market)
        w+=prob*o[0]; p+=prob*o[1]; l+=prob*o[2]
    return w,p,l
