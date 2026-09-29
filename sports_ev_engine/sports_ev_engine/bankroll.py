"""Bankroll/drawdown simulator for candidate probabilities.

This is a risk simulator, not a profit guarantee.  It deliberately caps Kelly
and daily exposure and can run with conservative probabilities from the combo
engine.
"""
from __future__ import annotations
import math,random


def kelly_fraction(prob,odds,fraction=.25,cap=.02):
    p=max(0.0,min(1.0,float(prob))); o=float(odds); b=o-1
    if b<=0:return 0.0
    full=max(0.0,(b*p-(1-p))/b)
    return min(float(cap),full*float(fraction))


def simulate(candidates, bankroll=1_000_000, paths=2000, cycles=100, kelly_mult=.25, per_bet_cap=.02, daily_cap=.08, ruin_fraction=.5, seed=340):
    rows=[]
    for r in candidates or []:
        try:p=float(r.get("daily_adjusted_prob",r.get("adjusted_prob",r.get("model_win_prob"))));o=float(r.get("best_odds"))
        except (TypeError,ValueError):continue
        if not (0<p<1 and o>1):continue
        f=kelly_fraction(p,o,kelly_mult,per_bet_cap)
        if f>0: rows.append((p,o,f))
    if not rows:return {"paths":0,"reason":"usable +EV candidates 없음"}
    total=sum(f for _,_,f in rows)
    scale=min(1.0,float(daily_cap)/total) if total>0 else 0
    rows=[(p,o,f*scale) for p,o,f in rows]
    rng=random.Random(seed); finals=[];dds=[];ruins=0
    for _ in range(int(paths)):
        bank=float(bankroll); peak=bank; maxdd=0
        for _c in range(int(cycles)):
            start=bank
            # Fractions are based on bankroll at cycle start so total exposure respects daily_cap.
            pnl=0.0
            for p,o,f in rows:
                stake=start*f
                pnl += stake*(o-1) if rng.random()<p else -stake
            bank=max(0.0,start+pnl); peak=max(peak,bank)
            if peak>0:maxdd=max(maxdd,(peak-bank)/peak)
            if bank<=bankroll*ruin_fraction:ruins+=1;break
        finals.append(bank);dds.append(maxdd)
    finals.sort();dds.sort();n=len(finals)
    q=lambda arr,x:arr[min(len(arr)-1,max(0,int(x*(len(arr)-1))))]
    return {"paths":n,"legs":len(rows),"effective_daily_exposure":sum(f for _,_,f in rows),"median_final":q(finals,.5),"p10_final":q(finals,.1),"p90_final":q(finals,.9),"median_max_drawdown":q(dds,.5),"p90_max_drawdown":q(dds,.9),"ruin_probability":ruins/max(1,n),"stake_fractions":[f for _,_,f in rows]}
