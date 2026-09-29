"""v3 multi-stage, explainable reasoning and robustness layer.

This module does not call an LLM.  It turns the same kinds of evidence that are
checked in a manual ChatGPT analysis into explicit, auditable signals:
independent model -> context -> counter-case -> market calibration -> scenario
robustness -> decision gate.

Scenario ranges are stress tests, not statistical confidence intervals.
"""
from __future__ import annotations

PATCH_BUILD = '3.4.15-xg-single-pass'

from dataclasses import dataclass, asdict
from itertools import product
import math
from typing import Callable, Iterable

ENGINE_ID = "chatgpt-style-v3.4.2-match-specific-explanations"


def clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


def finite(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


@dataclass
class Signal:
    name: str
    available: bool
    direction: str = "neutral"
    magnitude_pct: float = 0.0
    confidence: float = 0.0
    source: str = ""
    note: str = ""


class SignalLedger:
    def __init__(self):
        self.items: list[Signal] = []

    def add(self, name, available, direction="neutral", magnitude_pct=0.0,
            confidence=0.0, source="", note=""):
        self.items.append(Signal(
            name=str(name), available=bool(available), direction=str(direction),
            magnitude_pct=float(magnitude_pct or 0.0), confidence=clamp(confidence, 0, 1),
            source=str(source or ""), note=str(note or ""),
        ))

    @property
    def coverage(self):
        if not self.items:
            return 0.0
        return sum(1 for x in self.items if x.available) / len(self.items)

    @property
    def missing(self):
        return [x.name for x in self.items if not x.available]

    def compact(self):
        parts=[]
        for x in self.items:
            if not x.available:
                parts.append(f"{x.name}:MISSING")
                continue
            sign="+" if x.magnitude_pct>0 else "" if x.magnitude_pct==0 else ""
            detail=f" {sign}{x.magnitude_pct:.1f}%" if abs(x.magnitude_pct)>=0.05 else ""
            parts.append(f"{x.name}:{x.direction}{detail}")
        return " | ".join(parts)

    def to_dicts(self):
        return [asdict(x) for x in self.items]


def _side_context(ctx: dict, side: str) -> dict:
    v=ctx.get(side,{}) if isinstance(ctx,dict) else {}
    return v if isinstance(v,dict) else {}


def apply_soccer_context(home_lambda: float, away_lambda: float, context: dict | None,
                         home_form: dict | None=None, away_form: dict | None=None,
                         international: bool=False):
    """Apply only measured/labelled context; never fabricate missing features.

    Expected optional context keys are produced by deep_soccer_context.py.  The
    function is deliberately tolerant so older data sources remain compatible.
    """
    context=context or {}
    base_h=float(home_lambda); base_a=float(away_lambda)
    h=base_h; a=base_a
    ledger=SignalLedger()
    extra_unc=0.0
    attempted=bool(context.get("deep_context_attempted"))
    def miss_penalty(v):
        nonlocal extra_unc
        if attempted:extra_unc+=float(v)

    # Recent measured xG/xGA is a *replacement blend*, not an additive second
    # form boost.  Observed goals and xG are two noisy estimates of the same latent
    # scoring rate; stacking both as sequential multipliers double-counts the same
    # recent matches.  v3.4.15 therefore blends them exactly once here.
    hxgf=context.get("home_xg_for"); hxga=context.get("home_xg_against")
    axgf=context.get("away_xg_for"); axga=context.get("away_xg_against")
    xg_ok=all(finite(v) for v in (hxgf,hxga,axgf,axga))
    if xg_ok:
        hx=clamp((float(hxgf)+float(axga))/2, .20, 4.50)
        ax=clamp((float(axgf)+float(hxga))/2, .20, 4.50)
        # Guard against a bad/mismatched provider record while still letting real
        # measured xG move an overheated goal-only model materially.
        target_h=clamp(hx, base_h*.65, base_h*1.35)
        target_a=clamp(ax, base_a*.65, base_a*1.35)
        try:
            xg_n=min(int(context.get("xg_samples_home") or 0), int(context.get("xg_samples_away") or 0))
        except (TypeError,ValueError):
            xg_n=0
        if xg_n >= 5:
            xg_weight=.50 if international else .42
        elif xg_n >= 3:
            xg_weight=.45 if international else .38
        else:
            # Complete provider aggregates without an auditable sample count get a
            # smaller weight rather than being discarded or treated as 3+ games.
            xg_weight=.35 if international else .30
        h=(1-xg_weight)*h+xg_weight*target_h
        a=(1-xg_weight)*a+xg_weight*target_a
        context["xg_application_mode"]="single_pass_blend"
        context["xg_blend_weight"]=xg_weight
        context["xg_target_home"]=target_h
        context["xg_target_away"]=target_a
        ledger.add("recent_xg",True,"home" if hx>ax else "away" if ax>hx else "neutral",
                   100*((h+a)/(base_h+base_a)-1),.88,context.get("xg_source","measured xG"),
                   f"single-pass {xg_weight:.0%}; H xGF/xGA {float(hxgf):.2f}/{float(hxga):.2f}; A {float(axgf):.2f}/{float(axga):.2f}")
    else:
        ledger.add("recent_xg",False,note="usable xG sample <3 per team or provider did not expose xG")
        miss_penalty(.8)

    # Confirmed lineup relative strength. Collector reports fractional attack/defence changes.
    lineup_ok=bool(context.get("lineup_confirmed"))
    hla=float(context.get("home_lineup_attack_pct") or 0.0)
    hld=float(context.get("home_lineup_defense_pct") or 0.0)
    ala=float(context.get("away_lineup_attack_pct") or 0.0)
    ald=float(context.get("away_lineup_defense_pct") or 0.0)
    if lineup_ok:
        h*=1+(hla+ald)/100.0
        a*=1+(ala+hld)/100.0
        ledger.add("confirmed_lineup",True,
                   "home" if (hla-ala)>(hld-ald) else "away" if (ala-hla)>(ald-hld) else "neutral",
                   (hla+ald)-(ala+hld),.90,context.get("lineup_source","API-Football lineups"),
                   f"H atk/def {hla:+.1f}/{hld:+.1f}%; A {ala:+.1f}/{ald:+.1f}%")
    elif bool(context.get("probable_lineup_available")):
        pha=float(context.get("home_probable_lineup_attack_pct") or 0.0); phd=float(context.get("home_probable_lineup_defense_pct") or 0.0)
        paa=float(context.get("away_probable_lineup_attack_pct") or 0.0); pad=float(context.get("away_probable_lineup_defense_pct") or 0.0)
        # projected XI gets only 40% of a confirmed-lineup effect
        h*=1+.40*(pha+pad)/100.0; a*=1+.40*(paa+phd)/100.0
        net=(pha+pad)-(paa+phd)
        ledger.add("probable_lineup",True,"home" if net>0 else "away" if net<0 else "neutral",.40*net,.45,
                   context.get("probable_lineup_source","model projected XI"),"projected, not official; replaced by startXI")
        ledger.add("confirmed_lineup",False,note="projected XI available; official startXI not confirmed")
        miss_penalty(.45 if international else .30)
    else:
        ledger.add("confirmed_lineup",False,note="both starting XIs not confirmed")
        miss_penalty(1.0 if international else .7)

    # Injury/suspension impact is importance-weighted by the collector.
    hia=float(context.get("home_injury_attack_pct") or 0.0)
    hid=float(context.get("home_injury_defense_pct") or 0.0)
    aia=float(context.get("away_injury_attack_pct") or 0.0)
    aid=float(context.get("away_injury_defense_pct") or 0.0)
    injury_available=bool(context.get("injury_available"))
    if injury_available:
        h*=1+(hia+aid)/100.0
        a*=1+(aia+hid)/100.0
        net=(hia+aid)-(aia+hid)
        ledger.add("injuries_player_impact",True,"home" if net>0 else "away" if net<0 else "neutral",
                   net,.75,context.get("injury_source","API-Football injuries"),
                   f"importance-weighted H atk/def {hia:+.1f}/{hid:+.1f}%; A {aia:+.1f}/{aid:+.1f}%")
    else:
        ledger.add("injuries_player_impact",False,note="injury list/player importance unavailable")
        miss_penalty(.7)

    # Rest/travel proxy from prior fixture dates. We only use a small capped effect.
    hr=context.get("home_rest_days"); ar=context.get("away_rest_days")
    rest_ok=finite(hr) and finite(ar)
    if rest_ok:
        gap=clamp(float(hr)-float(ar),-7,7)
        adj=clamp(gap*.45,-2.5,2.5)
        h*=1+adj/100; a*=1-adj/100
        ledger.add("rest_schedule",True,"home" if adj>0 else "away" if adj<0 else "neutral",adj,.65,
                   context.get("schedule_source","competition fixtures"),f"rest H/A {float(hr):.1f}/{float(ar):.1f} days")
    else:
        ledger.add("rest_schedule",False,note="previous fixture timing unavailable")
        miss_penalty(.3)

    # Style/pressure slots are explicitly missing unless a source actually supplied them.
    ppda_h=context.get("home_ppda"); ppda_a=context.get("away_ppda")
    if finite(ppda_h) and finite(ppda_a):
        # PPDA is explanatory here; no directional multiplier without a validated matchup coefficient.
        ledger.add("ppda_pressing",True,"context",0,.55,context.get("ppda_source",""),f"PPDA H/A {ppda_h}/{ppda_a}")
    else:
        ledger.add("ppda_pressing",False,note="PPDA not exposed by current provider; no invented proxy")
        miss_penalty(.25)

    bc_h=context.get("home_big_chances"); bc_a=context.get("away_big_chances")
    if finite(bc_h) and finite(bc_a):
        ledger.add("big_chances",True,"home" if float(bc_h)>float(bc_a) else "away" if float(bc_a)>float(bc_h) else "neutral",
                   0,.60,context.get("big_chance_source",""),f"recent big chances H/A {bc_h}/{bc_a}")
    else:
        ledger.add("big_chances",False,note="big-chance feed unavailable")
        miss_penalty(.25)

    # Hard cap prevents context from overwhelming the independent model.
    h=clamp(h,base_h*.84,base_h*1.16)
    a=clamp(a,base_a*.84,base_a*1.16)
    return h,a,extra_unc,ledger


def build_counter_cases(*, sample_matches:int|None=None, lineup_confirmed:bool|None=None,
                        sanity:str|None=None, uncertainty_pp:float|None=None,
                        signal_coverage:float|None=None, stage:str|None=None,
                        international:bool=False, advanced_completeness:float|None=None):
    cases=[]
    def add(code,severity,text):
        cases.append({"code":code,"severity":severity,"text":text})
    if sample_matches is not None and sample_matches<5:
        add("SMALL_SAMPLE","high",f"recent sample only {sample_matches} matches")
    elif sample_matches is not None and sample_matches<7:
        add("THIN_SAMPLE","medium",f"recent sample {sample_matches} matches")
    if lineup_confirmed is False:
        if str(stage or "").upper()=="PROBABLE":
            add("LINEUP_PROJECTED","medium","projected XI available; official starting lineup not confirmed")
        else:
            add("LINEUP_UNKNOWN","high","starting lineup not confirmed")
    if sanity in {"OUTLIER_SHRUNK","HIGH_DISAGREEMENT"}:
        add("MARKET_CONFLICT","high",f"independent model strongly conflicts with market ({sanity})")
    elif sanity=="CHECK":
        add("MARKET_GAP","medium","independent model/market gap requires monitoring")
    if uncertainty_pp is not None and float(uncertainty_pp)>=7:
        add("HIGH_UNCERTAINTY","high",f"model uncertainty {float(uncertainty_pp):.1f}pp")
    elif uncertainty_pp is not None and float(uncertainty_pp)>=5:
        add("UNCERTAINTY","medium",f"model uncertainty {float(uncertainty_pp):.1f}pp")
    if signal_coverage is not None and signal_coverage<.5:
        add("LOW_SIGNAL_COVERAGE","high",f"deep signal coverage {100*signal_coverage:.0f}%")
    elif signal_coverage is not None and signal_coverage<.75:
        add("PARTIAL_SIGNAL_COVERAGE","medium",f"deep signal coverage {100*signal_coverage:.0f}%")
    if stage and stage!="FINAL":
        add("NOT_FINAL","high",f"pregame stage is {stage}")
    if advanced_completeness is not None and advanced_completeness<.55:
        add("ADVANCED_PARTIAL","medium",f"advanced signal completeness {100*advanced_completeness:.0f}%")
    if international:
        add("INTERNATIONAL_VARIANCE","medium","national-team samples/venues/rotation are less stable")
    rank={"low":1,"medium":2,"high":3}
    maxsev=max((rank[x["severity"]] for x in cases),default=0)
    label={0:"LOW",1:"LOW",2:"MEDIUM",3:"HIGH"}[maxsev]
    return cases,label


def scenario_assessment(*, odds:float, market_prob:float, model_weight:float,
                        scenarios:Iterable[tuple[float,float,str]],
                        base_ev:float, sanity:str="OK", data_ready:bool=True,
                        lineup_required:bool=False, lineup_confirmed:bool=True,
                        weight_scales=(.80,1.0,1.20)):
    """Stress raw model probabilities and market-prior weight.

    scenarios contains (raw_win, raw_push, label).  The result is deliberately
    named a stress/robustness range rather than a confidence interval.
    """
    if not data_ready or not finite(odds) or float(odds)<=1 or not finite(market_prob):
        return dict(robust_status="DATA_HOLD",robust_reason="required data/price missing",
                    robust_positive_ratio=0.0,robust_ev_min=float("nan"),robust_ev_p10=float("nan"),
                    robust_ev_max=float("nan"),robust_prob_min=float("nan"),robust_prob_max=float("nan"),
                    robust_scenario_count=0,robust_parlay_eligible=False)
    values=[]; probs=[]
    for raw_win,raw_push,_ in scenarios:
        resolved=max(1e-9,1-float(raw_push))
        raw_cond=clamp(float(raw_win)/resolved,0,1)
        for scale in weight_scales:
            w=clamp(float(model_weight)*float(scale),.08,.78)
            fw=(w*raw_cond+(1-w)*float(market_prob))*resolved
            ev=float(odds)*fw+float(raw_push)-1
            values.append(ev); probs.append(fw)
    if not values:
        return dict(robust_status="DATA_HOLD",robust_reason="no stress scenarios",
                    robust_positive_ratio=0.0,robust_ev_min=float("nan"),robust_ev_p10=float("nan"),
                    robust_ev_max=float("nan"),robust_prob_min=float("nan"),robust_prob_max=float("nan"),
                    robust_scenario_count=0,robust_parlay_eligible=False)
    s=sorted(values)
    p10=s[max(0,math.ceil(.10*len(s))-1)]
    pos=sum(v>0 for v in values)/len(values)
    if sanity in {"OUTLIER_SHRUNK","HIGH_DISAGREEMENT"}:
        status="REVIEW"; reason="model-market disagreement gate"
    elif base_ev<=0:
        status="PASS"; reason="base EV is non-positive"
    elif pos>=.85 and p10>0:
        status="ROBUST"; reason=f"{pos*100:.0f}% of stress scenarios positive and 10th-percentile EV > 0"
    elif pos>=.55:
        status="SENSITIVE"; reason=f"base EV positive but only {pos*100:.0f}% of stress scenarios stay positive"
    else:
        status="FRAGILE"; reason=f"base EV positive but only {pos*100:.0f}% of stress scenarios stay positive"
    parlay=status=="ROBUST" and (lineup_confirmed or not lineup_required)
    return dict(
        robust_status=status, robust_reason=reason, robust_positive_ratio=pos,
        robust_ev_min=min(values), robust_ev_p10=p10, robust_ev_max=max(values),
        robust_prob_min=min(probs), robust_prob_max=max(probs), robust_scenario_count=len(values),
        robust_parlay_eligible=parlay,
    )


def soccer_scenarios(home_lambda:float, away_lambda:float, price_fn:Callable[[dict],tuple[float,float,float]],
                     matrix_fn:Callable[[float,float],dict], multipliers=(.90,1.0,1.10)):
    out=[]
    for hm,am in product(multipliers,repeat=2):
        matrix=matrix_fn(float(home_lambda)*hm,float(away_lambda)*am)
        w,p,_=price_fn(matrix)
        out.append((w,p,f"lambda {hm:.2f}/{am:.2f}"))
    return out


def baseball_scenarios(home_runs:float, away_runs:float, price_fn:Callable[[dict],tuple[float,float,float]],
                       matrix_fn:Callable[[float,float],dict], multipliers=(.92,1.0,1.08)):
    out=[]
    for hm,am in product(multipliers,repeat=2):
        matrix=matrix_fn(float(home_runs)*hm,float(away_runs)*am)
        w,p,_=price_fn(matrix)
        out.append((w,p,f"runs {hm:.2f}/{am:.2f}"))
    return out


def decision_fields(*, ledger:SignalLedger|None, counter_cases:list, counter_risk:str,
                    robust:dict, legacy_eligible:bool=True):
    coverage=ledger.coverage if ledger else 0.0
    # Parlays are intentionally stricter than single-bet candidate display.
    status=robust.get("robust_status","DATA_HOLD")
    candidate=status in {"ROBUST","SENSITIVE"} and legacy_eligible
    parlay=status=="ROBUST" and bool(robust.get("robust_parlay_eligible")) and legacy_eligible and counter_risk!="HIGH"
    return {
        "reasoning_engine_id":ENGINE_ID,
        "signal_coverage":coverage,
        "signal_summary":ledger.compact() if ledger else "",
        "missing_signals":", ".join(ledger.missing) if ledger else "",
        "counter_case_risk":counter_risk,
        "counter_case_count":len(counter_cases),
        "counter_case_summary":" | ".join(x["text"] for x in counter_cases),
        "v3_decision_status":status,
        "v3_candidate":candidate,
        "v3_parlay_eligible":parlay,
        **robust,
    }
