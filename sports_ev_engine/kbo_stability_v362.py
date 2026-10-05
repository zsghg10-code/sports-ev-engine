"""v3.6.2 KBO stability / regression-control patch.

This patch deliberately avoids chasing one day's results. It rolls KBO's
probability-changing hotfixes back toward the earlier audited core and adds
risk gates around places where the current model can look over-confident.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

PATCH_VERSION = "3.6.2-kbo-stability"
KBO_CALIBRATION_MIN_N = 120
MARKET_MOVE_SENSITIVE_PP = -2.0
MARKET_MOVE_REVIEW_PP = -3.5
MELTDOWN_REVIEW_COMBINED = 0.34

_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


def _num(v: Any, default=float("nan")) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _truth(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    try:
        if pd.isna(v):
            return False
    except Exception:
        pass
    return bool(v) if v is not None else False


def _append_reason(existing: Any, reason: str) -> str:
    parts = [x.strip() for x in str(existing or "").split(" · ") if x.strip()]
    if reason and reason not in parts:
        parts.append(reason)
    return " · ".join(parts)


def _status_cap(current: Any, cap: str) -> str:
    order = {"ROBUST": 5, "SENSITIVE": 4, "FRAGILE": 3, "PASS": 2, "REVIEW": 1, "DATA_HOLD": 0}
    cur = str(current or "DATA_HOLD").upper()
    cp = str(cap or "DATA_HOLD").upper()
    if cur not in order:
        return cp
    return cp if order[cur] > order.get(cp, 0) else cur


def combined_meltdown_risk(home_recent: dict | None, away_recent: dict | None) -> float:
    hr = max(0.0, min(0.75, _num((home_recent or {}).get("meltdown_risk"), 0.0)))
    ar = max(0.0, min(0.75, _num((away_recent or {}).get("meltdown_risk"), 0.0)))
    return 1.0 - (1.0 - hr) * (1.0 - ar)


def guard_row(row: dict, *, home_meltdown_risk: float, away_meltdown_risk: float) -> dict:
    out = dict(row)
    status = str(out.get("v3_decision_status") or "DATA_HOLD").upper()
    reasons = []

    counter_high = str(out.get("counter_case_risk") or "").upper() == "HIGH"
    move_vals = [_num(out.get("market_move_pp")), _num(out.get("market_from_open_pp"))]
    move_vals = [x for x in move_vals if math.isfinite(x)]
    adverse_move = min(move_vals) if move_vals else float("nan")

    market = str(out.get("market") or "").lower()
    selection = str(out.get("selection") or "").lower()
    is_under = market == "totals" and "under" in selection

    combined = 1.0 - (1.0 - max(0.0, min(0.75, home_meltdown_risk))) * (
        1.0 - max(0.0, min(0.75, away_meltdown_risk))
    )

    if counter_high and status == "ROBUST":
        status = _status_cap(status, "SENSITIVE")
        reasons.append("counter-case HIGH: ROBUST 라벨 금지")

    if math.isfinite(adverse_move) and adverse_move <= MARKET_MOVE_REVIEW_PP:
        status = "REVIEW"
        reasons.append(f"시장 역이동 {adverse_move:+.1f}%p: REVIEW")
    elif math.isfinite(adverse_move) and adverse_move <= MARKET_MOVE_SENSITIVE_PP:
        status = _status_cap(status, "SENSITIVE")
        reasons.append(f"시장 역이동 {adverse_move:+.1f}%p: 최대 SENSITIVE")

    if is_under:
        if counter_high and (
            (math.isfinite(adverse_move) and adverse_move <= MARKET_MOVE_SENSITIVE_PP)
            or combined >= MELTDOWN_REVIEW_COMBINED
        ):
            status = "REVIEW"
            reasons.append(f"UNDER 붕괴경로: counter HIGH + starter meltdown {combined*100:.0f}%/시장 역이동")
        elif combined >= MELTDOWN_REVIEW_COMBINED:
            status = _status_cap(status, "SENSITIVE")
            reasons.append(f"UNDER starter meltdown 합성위험 {combined*100:.0f}%: 최대 SENSITIVE")

    candidate = _truth(out.get("v3_candidate"))
    parlay = _truth(out.get("v3_parlay_eligible"))
    if status in {"REVIEW", "PASS", "FRAGILE", "DATA_HOLD"}:
        candidate = False
        parlay = False
    elif status == "SENSITIVE":
        parlay = False

    out["v3_decision_status"] = status
    out["v3_candidate"] = bool(candidate)
    out["v3_parlay_eligible"] = bool(parlay)
    out["parlay_eligible"] = bool(parlay and _truth(out.get("lineup_confirmed")))
    out["kbo_home_starter_meltdown_risk"] = home_meltdown_risk
    out["kbo_away_starter_meltdown_risk"] = away_meltdown_risk
    out["kbo_combined_starter_meltdown_risk"] = combined
    out["kbo_adverse_market_move_pp"] = adverse_move
    out["kbo_stability_guard"] = PATCH_VERSION
    if reasons:
        joined = " · ".join(reasons)
        out["baseball_policy_reason"] = _append_reason(out.get("baseball_policy_reason"), joined)
        out["final_downgrade_reason"] = _append_reason(out.get("final_downgrade_reason"), joined)
        if status == "REVIEW":
            out["grade"] = "REVIEW"
    return out


def _install_recent_core_rollback() -> None:
    try:
        from . import kbo_safety_patch as safety
        from .providers import baseball_advanced as ba
        original = getattr(safety, "_ORIGINALS", {}).get("recent_runs_factor")
        if original is not None:
            ba.AdvancedBaseballSignals._recent_runs_factor = staticmethod(original)
            ba.KBO_RECENT_CORE = "pre-v3.4.25"
    except Exception:
        pass


def _install_adaptive_stability() -> None:
    from . import adaptive_model as am

    if "ensemble_components" not in _ORIGINALS:
        _ORIGINALS["ensemble_components"] = am.ensemble_components
        original_components = _ORIGINALS["ensemble_components"]

        def stable_components(r, family):
            q, comps = original_components(r, family)
            if str(family) != "baseball_kbo":
                return q, comps
            kept = [c for c in (comps or []) if str(c[0]) != "recent_form"]
            if not kept:
                return q, comps
            sw = sum(float(w) for _, _, w in kept) or 1.0
            q2 = sum(float(p) * float(w) for _, p, w in kept) / sw
            return q2, kept

        am.ensemble_components = stable_components

    if "calibration_curve" not in _ORIGINALS:
        _ORIGINALS["calibration_curve"] = am.calibration_curve
        original_curve = _ORIGINALS["calibration_curve"]

        def stable_curve(settled_rows, sport_family, market, *, min_n=None):
            if str(sport_family) == "baseball_kbo":
                effective = max(KBO_CALIBRATION_MIN_N, int(min_n or 0))
                return original_curve(settled_rows, sport_family, market, min_n=effective)
            return original_curve(settled_rows, sport_family, market, min_n=min_n)

        am.calibration_curve = stable_curve

    am.KBO_STABILITY_BUILD = PATCH_VERSION


def _install_starter_meltdown_audit() -> None:
    from .providers import baseball_advanced as ba
    if "kbo_pitcher_recent" in _ORIGINALS:
        return
    _ORIGINALS["kbo_pitcher_recent"] = ba.KBOAdvanced.pitcher_recent
    original = _ORIGINALS["kbo_pitcher_recent"]

    def pitcher_recent_with_tail(self, pid, n=5):
        out = dict(original(self, pid, n) or {})
        if not pid:
            return out
        try:
            rows, err = self._pitcher_log(pid)
            if err or not rows:
                return out
            rows = sorted(rows, key=lambda r: ba._mmdd_key(r.get("일자")))[-int(n):]
            starts = []
            meltdown = 0
            early = 0
            for r in rows:
                ip = ba._ip(r.get("IP")) or 0.0
                runs = float(ba._num(r.get("R")) or 0.0)
                er = float(ba._num(r.get("ER")) or 0.0)
                hits = float(ba._num(r.get("H")) or 0.0)
                bb = float(ba._num(r.get("BB")) or 0.0)
                bf = float(ba._num(r.get("TBF")) or 0.0)
                npitch = ba._num(r.get("NP"))
                bad = runs >= 5 or er >= 5 or (ip < 4.0 and (runs >= 3 or er >= 3 or hits + bb >= 8))
                if bad:
                    meltdown += 1
                if ip < 5.0:
                    early += 1
                starts.append({"ip": ip, "r": runs, "er": er, "h": hits, "bb": bb, "bf": bf, "np": npitch, "meltdown": bad})

            g = len(starts)
            if g:
                rate = (meltdown + 0.5) / (g + 2.0)
                early_rate = (early + 0.5) / (g + 2.0)
                bb_pct = _num(out.get("bb_pct"), 0.0)
                kbb_pct = _num(out.get("kbb_pct"), 0.12)
                avg_ip = sum(x["ip"] for x in starts) / g
                risk = rate
                if bb_pct >= 0.12:
                    risk += 0.04
                if kbb_pct <= 0.05:
                    risk += 0.04
                if avg_ip < 5.0:
                    risk += 0.04
                risk = max(0.03, min(0.60, risk))
                out["starts"] = starts
                out["meltdown_count"] = meltdown
                out["meltdown_rate_raw"] = meltdown / g
                out["meltdown_risk"] = risk
                out["early_exit_rate"] = early_rate
                out["avg_recent_ip"] = avg_ip
        except Exception:
            pass
        return out

    ba.KBOAdvanced.pitcher_recent = pitcher_recent_with_tail
    ba.KBO_MELTDOWN_AUDIT_BUILD = PATCH_VERSION


def _install_decision_guard() -> None:
    from . import official_baseball_model as obm
    if "official_analyze_v362" in _ORIGINALS:
        return
    _ORIGINALS["official_analyze_v362"] = obm.analyze_official_event
    original = _ORIGINALS["official_analyze_v362"]

    def guarded_analyze(event_market, stats, league, context=None):
        frame, meta = original(event_market, stats, league, context)
        if frame is None or frame.empty or str(league).upper() != "KBO":
            return frame, meta

        ctx = context or {}
        adv = ctx.get("advanced") or {}
        hr = adv.get("home_starter_recent") or {}
        ar = adv.get("away_starter_recent") or {}
        home_risk = max(0.0, min(0.75, _num(hr.get("meltdown_risk"), 0.0)))
        away_risk = max(0.0, min(0.75, _num(ar.get("meltdown_risk"), 0.0)))

        for i, row in frame.iterrows():
            guarded = guard_row(row.to_dict(), home_meltdown_risk=home_risk, away_meltdown_risk=away_risk)
            for key, value in guarded.items():
                frame.at[i, key] = value

            if str(row.get("market") or "") == "spreads":
                try:
                    point = float(row.get("point"))
                except (TypeError, ValueError):
                    point = 0.0
                side = obm._side(row)
                opp_risk = away_risk if side == "home" else home_risk if side == "away" else 0.0
                frame.at[i, "kbo_runline_watch"] = bool(point < 0 and opp_risk >= 0.25)
                frame.at[i, "kbo_runline_watch_reason"] = (
                    f"opponent starter meltdown risk {opp_risk*100:.0f}%" if point < 0 and opp_risk >= 0.25 else ""
                )

        if isinstance(meta, dict):
            meta = dict(meta)
            meta["kbo_stability_build"] = PATCH_VERSION
            meta["home_starter_meltdown_risk"] = home_risk
            meta["away_starter_meltdown_risk"] = away_risk
        return frame, meta

    obm.analyze_official_event = guarded_analyze


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_recent_core_rollback()
    _install_adaptive_stability()
    _install_starter_meltdown_audit()
    _install_decision_guard()
    try:
        from . import prediction_store as ps
        ps.MODEL_VERSION = "3.6.2-kbo-stability"
    except Exception:
        pass
    _INSTALLED = True
