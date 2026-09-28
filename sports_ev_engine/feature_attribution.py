"""Auditable probability attribution.

The decomposition is intentionally labelled approximate.  Ensemble component
contributions are measured versus the market anchor; calibration is explicit;
context-ledger magnitudes are displayed separately because lambda/run effects do
not map linearly to probability points.
"""
from __future__ import annotations
import math,re


def _num(v,default=float("nan")):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except (TypeError,ValueError): return default


def attribution(row:dict):
    market=_num(row.get("consensus_prob")); final=_num(row.get("model_win_prob")); push=max(0.0,_num(row.get("push_prob"),0.0))
    resolved=max(1e-9,1-push)
    final_cond=final/resolved if math.isfinite(final) else float("nan")
    out=[]
    for key,label in (("independent","독립/정밀모델"),("recent_form","최근폼")):
        p=_num(row.get(f"ensemble_{key}_prob")); w=_num(row.get(f"ensemble_{key}_weight"),0.0)
        if math.isfinite(p) and math.isfinite(market):
            out.append({"feature":label,"contribution_pp":(p-market)*w*100,"detail":f"component {p*100:.1f}% × weight {w:.2f}"})
    mw=_num(row.get("ensemble_market_weight"),0.0)
    if math.isfinite(market): out.append({"feature":"시장 anchor","contribution_pp":0.0,"detail":f"no-vig {market*100:.1f}%, weight {mw:.2f}"})
    cal=_num(row.get("calibration_delta_pp"),0.0)
    if abs(cal)>=.01: out.append({"feature":"자동 Calibration","contribution_pp":cal,"detail":f"settled n={int(_num(row.get('calibration_n'),0))}"})
    # residual makes the probability-level accounting transparent rather than pretending exact Shapley attribution.
    known=sum(x["contribution_pp"] for x in out if x["feature"]!="시장 anchor")
    if math.isfinite(final_cond) and math.isfinite(market):
        total=(final_cond-market)*100
        residual=total-known
        out.append({"feature":"컨텍스트/정규화 잔차","contribution_pp":residual,"detail":"라인업·xG·결장·상호배타 정규화 등 비선형 효과"})
    ledger=[]
    for part in str(row.get("signal_summary") or "").split("|"):
        s=part.strip()
        if not s or "MISSING" in s: continue
        m=re.search(r"^([^:]+):([^ ]+)(?:\s+([+-]?\d+(?:\.\d+)?)%)?",s)
        if m: ledger.append({"signal":m.group(1),"direction":m.group(2),"context_magnitude_pct":float(m.group(3)) if m.group(3) else 0.0})
    return {"market_prob":market,"final_cond_prob":final_cond,"probability_contributions":out,"context_ledger":ledger}
