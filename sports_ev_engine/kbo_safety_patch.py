"""v3.4.25 KBO pregame safety hotfix.

This module is intentionally small and reversible.  It wraps the existing
v3.4.24 pipeline instead of replacing the calibrated models.  The patch fixes
four concrete failure paths found in live use:

1. Cross-check late KBO starter changes against the Naver pregame feed even
   when the KBO GameCenter batting order is already FINAL.
2. Never keep a FINAL/actionable pick while official and public starter names
   conflict.  The newer public starter may be used for diagnostics, but the
   betting state is HOLD until the sources converge.
3. Winsorize/shrink one-game scoring explosions before they feed KBO recent
   form, and use a wider totals stress grid with stricter parlay thresholds.
4. Make the daily-combo layer respect v3_parlay_eligible and impose a much
   tighter freshness window for baseball close to first pitch.

The underlying probability/calibration architecture is otherwise unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any

import pandas as pd

PATCH_VERSION = "3.4.25-kbo-safety"
KBO_TOTALS_STRESS = (0.88, 0.94, 1.00, 1.06, 1.12)

_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


def _truth(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    try:
        if pd.isna(v):
            return False
    except Exception:
        pass
    return bool(v) if v is not None else False


def _num(v: Any, default=float("nan")) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _norm_name(v: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣ぁ-んァ-ヶ一-龯]+", "", str(v or "")).lower()


def starter_signature(home_starter: Any, away_starter: Any) -> str:
    raw = f"{_norm_name(home_starter)}|{_norm_name(away_starter)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def reconcile_kbo_starters(
    official_home: Any,
    official_away: Any,
    naver_home: Any,
    naver_away: Any,
    *,
    official_confirmed: bool,
    naver_authoritative: bool,
) -> dict:
    """Safely reconcile KBO GameCenter vs Naver pregame starter identities.

    A complete, exact-match Naver batting order is treated as an independent
    cross-check.  When it disagrees with the official game-list starter, the
    Naver name is allowed to drive *diagnostic* pitcher lookup, but the event is
    deliberately not considered starter-confirmed until the two sources agree.
    """
    oh, oa = (str(official_home or "").strip() or None), (str(official_away or "").strip() or None)
    nh, na = (str(naver_home or "").strip() or None), (str(naver_away or "").strip() or None)

    conflicts = []
    if naver_authoritative:
        if oh and nh and _norm_name(oh) != _norm_name(nh):
            conflicts.append(f"home {oh} -> {nh}")
        if oa and na and _norm_name(oa) != _norm_name(na):
            conflicts.append(f"away {oa} -> {na}")

    if conflicts:
        home = nh or oh
        away = na or oa
        return {
            "home_starter": home,
            "away_starter": away,
            "starter_confirmed": False,
            "starter_verified": False,
            "starter_source_conflict": True,
            "starter_override_applied": True,
            "starter_conflict_detail": "; ".join(conflicts),
            "starter_source": "KBO GameCenter vs Naver Sports conflict",
            "starter_signature": starter_signature(home, away),
        }

    home = oh or (nh if naver_authoritative else None)
    away = oa or (na if naver_authoritative else None)
    confirmed = bool(home and away and (official_confirmed or naver_authoritative))
    cross_checked = bool(
        naver_authoritative and nh and na and
        (not oh or _norm_name(oh) == _norm_name(nh)) and
        (not oa or _norm_name(oa) == _norm_name(na))
    )
    return {
        "home_starter": home,
        "away_starter": away,
        "starter_confirmed": confirmed,
        "starter_verified": bool(confirmed and (cross_checked or official_confirmed)),
        "starter_source_conflict": False,
        "starter_override_applied": bool((not oh and nh) or (not oa and na)),
        "starter_conflict_detail": "",
        "starter_source": "KBO GameCenter + Naver Sports cross-check" if cross_checked else "KBO GameCenter",
        "starter_signature": starter_signature(home, away),
    }


def regressed_recent_rate(recent: dict | None, field: str, season_rate: Any,
                          *, clip_runs: float = 3.5, shrink: float = 0.80) -> float | None:
    """Robust recent run rate that cannot be dominated by one 10+ run game.

    Each game is winsorized around the season baseline and the resulting mean
    is mildly shrunk back toward that baseline.  Repeated strong/weak games are
    still respected; only extreme single-game tails lose leverage.
    """
    if not recent or not recent.get("available"):
        return None
    try:
        base = float(season_rate)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(base) or base <= 0:
        return None

    raw = recent.get("raw") or []
    vals = []
    for item in raw:
        try:
            v = float((item or {}).get(field))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v >= 0:
            vals.append(v)
    if not vals:
        key = "runs_for_per_game" if field == "rf" else "runs_against_per_game"
        try:
            v = float(recent.get(key))
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    lo = max(0.0, base - float(clip_runs))
    hi = base + float(clip_runs)
    clipped = [max(lo, min(hi, v)) for v in vals]
    clipped_mean = sum(clipped) / len(clipped)
    result = base + float(shrink) * (clipped_mean - base)
    changed = any(abs(a - b) > 1e-9 for a, b in zip(vals, clipped))

    key = "runs_for_regressed_per_game" if field == "rf" else "runs_against_regressed_per_game"
    recent[key] = result
    recent["blowout_regression_applied"] = bool(recent.get("blowout_regression_applied")) or changed
    recent["recent_rate_shrinkage_applied"] = True
    return result


def _append_reason(existing: Any, reason: str) -> str:
    parts = [x.strip() for x in str(existing or "").split(" · ") if x.strip()]
    if reason and reason not in parts:
        parts.append(reason)
    return " · ".join(parts)


def _install_live_starter_guard():
    from .providers import live_baseball as lb

    if "kbo_context" in _ORIGINALS:
        return
    _ORIGINALS["kbo_context"] = lb.KBOOfficialLive.context
    original = _ORIGINALS["kbo_context"]

    def guarded_context(self, home, away, commence_iso):
        ctx = dict(original(self, home, away, commence_iso) or {})
        if str(ctx.get("league") or "").upper() != "KBO" or not ctx.get("game_id"):
            return ctx

        official_home = ctx.get("home_starter")
        official_away = ctx.get("away_starter")
        nav = {}
        try:
            nav = self._naver_lineup(home, away, commence_iso) or {}
        except Exception as exc:
            ctx["starter_crosscheck_error"] = f"{type(exc).__name__}: {exc}"

        # _naver_lineup only returns a matched game.  Require both complete 1-9
        # orders before allowing it to overrule a conflicting GameCenter name.
        naver_authoritative = bool(
            nav.get("confirmed") and nav.get("source_game_id") and
            len(nav.get("home") or []) == 9 and len(nav.get("away") or []) == 9
        )
        rec = reconcile_kbo_starters(
            official_home, official_away,
            nav.get("home_starter"), nav.get("away_starter"),
            official_confirmed=bool(ctx.get("starter_confirmed")),
            naver_authoritative=naver_authoritative,
        )

        ctx.update(rec)
        ctx["official_home_starter"] = official_home
        ctx["official_away_starter"] = official_away
        ctx["naver_home_starter"] = nav.get("home_starter")
        ctx["naver_away_starter"] = nav.get("away_starter")
        ctx["starter_checked_at"] = datetime.now(timezone.utc).isoformat()

        if rec["starter_source_conflict"]:
            # Newer public starter is useful for diagnostics, but never allow a
            # wager state until the official source converges.  Drop official
            # player IDs so advanced lookup cannot silently use the old pitcher.
            ctx["stage"] = "STARTER CONFLICT"
            ctx["home_starter_id"] = None
            ctx["away_starter_id"] = None
            try:
                pmap = self._pitcher_table()
                ctx["home_starter_stats"] = pmap.get(lb._norm(rec["home_starter"]), {}) if rec["home_starter"] else {}
                ctx["away_starter_stats"] = pmap.get(lb._norm(rec["away_starter"]), {}) if rec["away_starter"] else {}
            except Exception:
                ctx["home_starter_stats"] = {}
                ctx["away_starter_stats"] = {}
            ctx["note"] = _append_reason(ctx.get("note"), "starter source conflict: previous pick invalid until re-confirmed")
        else:
            # Naver may fill an official blank.  Refresh pitcher stats/name IDs
            # whenever the selected starter differs from the original context.
            changed = (
                _norm_name(rec["home_starter"]) != _norm_name(official_home) or
                _norm_name(rec["away_starter"]) != _norm_name(official_away)
            )
            if changed:
                ctx["home_starter_id"] = None
                ctx["away_starter_id"] = None
                try:
                    pmap = self._pitcher_table()
                    ctx["home_starter_stats"] = pmap.get(lb._norm(rec["home_starter"]), {}) if rec["home_starter"] else {}
                    ctx["away_starter_stats"] = pmap.get(lb._norm(rec["away_starter"]), {}) if rec["away_starter"] else {}
                except Exception:
                    pass
            if rec["starter_confirmed"]:
                if bool(ctx.get("lineup_confirmed")):
                    ctx["stage"] = "FINAL"
                elif str(ctx.get("stage") or "") == "PRE-LINEUP":
                    ctx["stage"] = "STARTER CONFIRMED"
        return ctx

    lb.KBOOfficialLive.context = guarded_context
    lb.LIVE_BASEBALL_BUILD = "3.4.25"


def _install_advanced_regression():
    from .providers import baseball_advanced as ba

    if "recent_runs_factor" not in _ORIGINALS:
        _ORIGINALS["recent_runs_factor"] = ba.AdvancedBaseballSignals._recent_runs_factor
        original_factor = _ORIGINALS["recent_runs_factor"]

        def robust_recent_runs_factor(recent, season_for, opponent_recent=None, opponent_season_ra=None):
            # KBO recent raw rows expose rf/ra. NPB does not use this shape, so
            # leave all non-KBO-like payloads on the original implementation.
            has_kbo_raw = bool(recent and any("rf" in (x or {}) for x in (recent.get("raw") or [])))
            if not has_kbo_raw:
                return original_factor(recent, season_for, opponent_recent, opponent_season_ra)

            rf = regressed_recent_rate(recent, "rf", season_for)
            if rf is None or not season_for or float(season_for) <= 0:
                return None
            ratios = [max(.35, min(2.50, float(rf) / float(season_for)))]
            if opponent_recent and opponent_season_ra:
                ora = regressed_recent_rate(opponent_recent, "ra", opponent_season_ra)
                if ora is not None and float(opponent_season_ra) > 0:
                    ratios.append(max(.35, min(2.50, float(ora) / float(opponent_season_ra))))
            ratio = math.exp(sum(math.log(x) for x in ratios) / len(ratios))
            # Slightly smaller ceiling/strength than v3.4.24 for KBO recent-run
            # form; confirmed lineup OPS, starter form and bullpen remain separate.
            return ba._factor_from_ratio(ratio, .04, .24)

        ba.AdvancedBaseballSignals._recent_runs_factor = staticmethod(robust_recent_runs_factor)

    if "kbo_enrich" not in _ORIGINALS:
        _ORIGINALS["kbo_enrich"] = ba.KBOAdvanced.enrich
        original_enrich = _ORIGINALS["kbo_enrich"]

        def guarded_enrich(self, home, away, commence_iso, ctx, recent_n=10):
            result = original_enrich(self, home, away, commence_iso, ctx, recent_n)
            if not (ctx.get("starter_source_conflict") or ctx.get("starter_override_applied")):
                return result

            # The game-list pitcher ID can still point to the old announced
            # starter.  Rebind by the reconciled name before computing recent
            # form and starter-vs-opponent history.
            hpid = self.resolve_player_id(ctx.get("home_starter"), home)
            apid = self.resolve_player_id(ctx.get("away_starter"), away)
            if hpid:
                result["home_starter_recent"] = self.pitcher_recent(hpid, 5)
                result["home_starter_vs_opponent"] = self.pitcher_vs_opponent(hpid, away, 10)
            else:
                result["home_starter_recent"] = {"available": False, "reason": "starter identity changed; new player id unresolved"}
                result["home_starter_vs_opponent"] = {"available": False, "reason": "starter identity changed; new player id unresolved", "opponent": away}
            if apid:
                result["away_starter_recent"] = self.pitcher_recent(apid, 5)
                result["away_starter_vs_opponent"] = self.pitcher_vs_opponent(apid, home, 10)
            else:
                result["away_starter_recent"] = {"available": False, "reason": "starter identity changed; new player id unresolved"}
                result["away_starter_vs_opponent"] = {"available": False, "reason": "starter identity changed; new player id unresolved", "opponent": home}
            result["starter_identity_rebound"] = True
            result.setdefault("notes", []).append("당일 선발 변경/소스충돌: 기존 game-list pitcherId 폐기 후 현재 선발명으로 재조회")
            return result

        ba.KBOAdvanced.enrich = guarded_enrich

    ba.BASEBALL_ADVANCED_BUILD = "3.4.25"


def _install_official_model_policy():
    from . import official_baseball_model as obm
    from .reasoning_engine import baseball_scenarios, final_probability_assessment

    if "official_analyze" in _ORIGINALS:
        return
    _ORIGINALS["official_analyze"] = obm.analyze_official_event
    original = _ORIGINALS["official_analyze"]

    def patched_analyze(event_market, stats, league, context=None):
        frame, meta = original(event_market, stats, league, context)
        if frame is None or frame.empty or str(league).upper() != "KBO":
            return frame, meta

        ctx = context or {}
        conflict = bool(ctx.get("starter_source_conflict"))
        hm = _num((meta or {}).get("home_expected_runs"))
        am = _num((meta or {}).get("away_expected_runs"))
        dispersion = float(obm.BASEBALL_SCORE_DISTRIBUTION["dispersion"])

        for i, row in frame.iterrows():
            # Context audit fields are useful immediately in the KBO tab even
            # though older prediction-store schemas do not persist every field.
            for key in (
                "starter_source_conflict", "starter_conflict_detail", "starter_source",
                "starter_verified", "starter_signature", "starter_override_applied",
                "official_home_starter", "official_away_starter", "naver_home_starter",
                "naver_away_starter", "starter_checked_at",
            ):
                frame.at[i, key] = ctx.get(key)

            adv = ctx.get("advanced") or {}
            hr = adv.get("home_recent") or {}
            ar = adv.get("away_recent") or {}
            blowout_regression = bool(hr.get("blowout_regression_applied") or ar.get("blowout_regression_applied"))
            frame.at[i, "recent_blowout_regression_used"] = blowout_regression

            if conflict:
                frame.at[i, "v3_decision_status"] = "REVIEW"
                frame.at[i, "v3_candidate"] = False
                frame.at[i, "v3_parlay_eligible"] = False
                frame.at[i, "parlay_eligible"] = False
                frame.at[i, "grade"] = "REVIEW"
                frame.at[i, "baseball_policy_gate"] = "HOLD"
                frame.at[i, "baseball_policy_reason"] = "선발 소스 충돌 — 기존 픽/EV 무효, 공식 재확인 후 재분석"
                frame.at[i, "final_downgrade_reason"] = frame.at[i, "baseball_policy_reason"]
                continue

            market = str(row.get("market") or "")
            edge = _num(row.get("edge_pp"), -999.0)
            existing_candidate = _truth(row.get("v3_candidate"))
            existing_parlay = _truth(row.get("v3_parlay_eligible"))
            reasons = []

            if market == "totals" and math.isfinite(hm) and math.isfinite(am):
                # Re-run final-probability robustness on a wider KBO run grid.
                # This does not change the displayed probability; it only asks
                # whether the +EV claim survives a more realistic scoring tail.
                side = obm._side(row)
                point = float(row["point"])
                matrix = obm._matrix(hm, am, dispersion=dispersion)
                raw_w, raw_p, _ = obm._market_probs(market, side, point, matrix)
                raw_q = raw_w / max(1e-9, 1 - raw_p)
                final_push = max(0.0, _num(row.get("push_prob"), 0.0))
                final_q = _num(row.get("model_win_prob"), 0.0) / max(1e-9, 1 - final_push)
                raw_scenarios = baseball_scenarios(
                    hm, am,
                    lambda mm: obm._market_probs(market, side, point, mm),
                    lambda h, a: obm._matrix(h, a, dispersion=dispersion),
                    multipliers=KBO_TOTALS_STRESS,
                )
                final_scenarios = []
                for sw, sp, label in raw_scenarios:
                    sq = sw / max(1e-9, 1 - sp)
                    fq = max(1e-6, min(1 - 1e-6, final_q + (sq - raw_q)))
                    final_scenarios.append((fq * (1 - sp), sp, f"KBO-wide {label}"))
                robust = final_probability_assessment(
                    odds=float(row["best_odds"]),
                    final_win_prob=float(row["model_win_prob"]),
                    final_push_prob=final_push,
                    uncertainty_pp=float(row.get("uncertainty_pp") or 0) + 0.75,
                    scenario_probabilities=final_scenarios,
                    data_ready=True,
                    lineup_required=True,
                    lineup_confirmed=bool(ctx.get("lineup_confirmed")) and str(ctx.get("stage")) == "FINAL",
                )
                for key, value in robust.items():
                    frame.at[i, key] = value
                frame.at[i, "v3_decision_status"] = robust.get("robust_status")
                frame.at[i, "kbo_totals_stress_profile"] = "0.88/0.94/1.00/1.06/1.12 +0.75pp uncertainty"

                odds = float(row["best_odds"])
                win = float(row["model_win_prob"])
                strict_unc = float(row.get("uncertainty_pp") or 0) + 0.75
                conservative_win = max(0.0, win - strict_unc / 100.0)
                strict_cev = odds * conservative_win + final_push - 1.0
                frame.at[i, "kbo_totals_conservative_ev_roi"] = strict_cev

                status = str(robust.get("robust_status") or "")
                ratio = _num(robust.get("robust_positive_ratio"), 0.0)
                p10 = _num(robust.get("robust_ev_p10"), -1.0)
                single_ok = existing_candidate and status in {"ROBUST", "SENSITIVE"} and edge >= 2.5 and strict_cev > 0
                parlay_ok = (
                    existing_parlay and status == "ROBUST" and edge >= 4.0 and
                    strict_cev >= 0.02 and ratio >= 0.90 and p10 >= 0.015
                )
                if edge < 2.5:
                    reasons.append(f"KBO totals 단일 Edge {edge:.1f}%p < 2.5%p")
                if edge < 4.0:
                    reasons.append(f"KBO totals 다폴 Edge {edge:.1f}%p < 4.0%p")
                if strict_cev < 0.02:
                    reasons.append(f"KBO totals 보수 EV {strict_cev*100:+.1f}% < +2.0%")
                if ratio < 0.90 or p10 < 0.015:
                    reasons.append("확장 스트레스 강건성 미달")
                frame.at[i, "v3_candidate"] = bool(single_ok)
                frame.at[i, "v3_parlay_eligible"] = bool(parlay_ok)
                frame.at[i, "parlay_eligible"] = bool(parlay_ok and str(ctx.get("stage")) == "FINAL")
            else:
                # Moneyline/run-line can remain valid singles, but a thin edge
                # is not allowed to become a common accumulator axis.
                parlay_ok = existing_parlay and edge >= 3.0
                if existing_parlay and edge < 3.0:
                    reasons.append(f"KBO 승패/핸디 다폴 Edge {edge:.1f}%p < 3.0%p")
                frame.at[i, "v3_parlay_eligible"] = bool(parlay_ok)
                frame.at[i, "parlay_eligible"] = bool(parlay_ok and str(ctx.get("stage")) == "FINAL")

            candidate_now = _truth(frame.at[i, "v3_candidate"])
            parlay_now = _truth(frame.at[i, "v3_parlay_eligible"])
            frame.at[i, "baseball_policy_gate"] = "PASS" if parlay_now else "SINGLE_ONLY" if candidate_now else "HOLD"
            frame.at[i, "baseball_policy_reason"] = " · ".join(dict.fromkeys(reasons)) if reasons else "v3.4.25 KBO safety gates passed"
            frame.at[i, "kbo_totals_policy_pass"] = bool(candidate_now) if market == "totals" else None

        return frame, meta

    obm.analyze_official_event = patched_analyze


def _install_daily_combo_guard():
    from . import daily_combo as dc

    if "daily_prepare" not in _ORIGINALS:
        _ORIGINALS["daily_prepare"] = dc.prepare_daily_candidates
        original_prepare = _ORIGINALS["daily_prepare"]

        def guarded_prepare(frame: pd.DataFrame) -> pd.DataFrame:
            out = original_prepare(frame)
            if out is None or out.empty:
                return out
            now = pd.Timestamp.now(tz="UTC")
            for i, row in out.iterrows():
                reasons = str(row.get("daily_gate_reason") or "")
                combo = _truth(row.get("daily_combo_eligible"))
                single = _truth(row.get("daily_single_eligible"))
                fam = str(row.get("sport_family") or row.get("sport_key") or "").lower()
                baseball = "baseball" in fam
                kbo = "kbo" in fam
                market = str(row.get("market") or "")
                edge = _num(row.get("edge_pp"), -999.0)

                # Core reasoning intentionally makes SENSITIVE single-only.
                # v3.4.24 daily_combo failed to honor this field.
                if "v3_parlay_eligible" in row.index and not _truth(row.get("v3_parlay_eligible")):
                    combo = False
                    reasons = _append_reason(reasons, "v3 조합 게이트 미통과(SENSITIVE/강건성)")

                if str(row.get("stage") or "").upper() == "STARTER CONFLICT" or _truth(row.get("starter_source_conflict")):
                    single = False
                    combo = False
                    reasons = _append_reason(reasons, "선발 소스 충돌 — 재확인 전 베팅 제외")

                if kbo and market == "totals":
                    if edge < 2.5:
                        single = False
                        combo = False
                        reasons = _append_reason(reasons, f"KBO totals Edge {edge:.1f}%p < 단일 2.5%p")
                    elif edge < 4.0:
                        combo = False
                        reasons = _append_reason(reasons, f"KBO totals Edge {edge:.1f}%p < 다폴 4.0%p")
                elif kbo and edge < 3.0:
                    combo = False
                    reasons = _append_reason(reasons, f"KBO 다폴 Edge {edge:.1f}%p < 3.0%p")

                # Every new baseball analysis cross-checks starters in context(),
                # so recorded_at is also a starter/lineup check time.  Use a much
                # shorter freshness window close to first pitch than the generic
                # 90/180-minute daily-tab rule.
                if baseball and "starter_confirmed" in row.index:
                    if not _truth(row.get("starter_confirmed")):
                        single = False
                        combo = False
                        reasons = _append_reason(reasons, "실제 선발 미확정")
                    kick = pd.to_datetime(row.get("commence_time"), utc=True, errors="coerce")
                    rec = pd.to_datetime(row.get("recorded_at"), utc=True, errors="coerce")
                    if not pd.isna(kick) and kick > now:
                        mins = (kick - now).total_seconds() / 60.0
                        age = (now - rec).total_seconds() / 60.0 if not pd.isna(rec) else float("inf")
                        limit = 10.0 if mins <= 45 else 30.0 if mins <= 120 else None
                        if limit is not None and age > limit:
                            single = False
                            combo = False
                            reasons = _append_reason(reasons, f"경기 {mins:.0f}분 전 선발/라인업 확인 {age:.0f}분 경과 — 재분석 필요")

                out.at[i, "daily_single_eligible"] = bool(single)
                out.at[i, "daily_combo_eligible"] = bool(combo)
                out.at[i, "daily_gate_reason"] = reasons
                if combo:
                    state = "조합 가능"
                elif single:
                    state = "단일 후보"
                else:
                    state = "검토 후보"
                if not str(row.get("national_status_label") or ""):
                    out.at[i, "daily_candidate_state"] = state
            return out.sort_values(
                ["daily_combo_eligible", "daily_quality_score", "daily_adjusted_prob", "daily_adjusted_ev"],
                ascending=[False, False, False, False],
            ).reset_index(drop=True)

        dc.prepare_daily_candidates = guarded_prepare

    if "best_combos" not in _ORIGINALS:
        _ORIGINALS["best_combos"] = dc.best_combos
        original_best = _ORIGINALS["best_combos"]

        def diversified_best(candidates, sizes=(1, 2, 3), top_n=5):
            # Ask the original optimizer for a wider shortlist, then prevent one
            # leg from appearing in virtually every recommended ticket when
            # alternatives exist.  This changes presentation/exposure, not EV.
            expanded = max(int(top_n), min(40, int(top_n) * 5))
            raw = original_best(candidates, sizes=sizes, top_n=expanded)
            result = {}
            for n in sizes:
                rows = list(raw.get(int(n), []))
                if int(n) <= 1 or top_n <= 1:
                    result[int(n)] = rows[:top_n]
                    continue
                cap = max(1, math.ceil(top_n * 0.50))
                counts: dict[str, int] = {}
                picked = []
                deferred = []
                for combo in rows:
                    ids = [f"{leg.get('event_key')}|{leg.get('market')}|{leg.get('selection')}|{leg.get('point')}" for leg in combo.get("legs", [])]
                    if any(counts.get(x, 0) >= cap for x in ids):
                        deferred.append(combo)
                        continue
                    picked.append(combo)
                    for x in ids:
                        counts[x] = counts.get(x, 0) + 1
                    if len(picked) >= top_n:
                        break
                # Do not hide valid combinations if the pool is genuinely tiny.
                if len(picked) < top_n:
                    seen = {x.get("combo_label") for x in picked}
                    for combo in deferred:
                        if combo.get("combo_label") in seen:
                            continue
                        picked.append(combo)
                        if len(picked) >= top_n:
                            break
                result[int(n)] = picked[:top_n]
            return result

        dc.best_combos = diversified_best


def _install_monitor_labels():
    # The existing monitor hashes starter names + lineup and therefore already
    # triggers on the patched context.  No behavioral replacement is necessary;
    # expose a build marker for diagnostics.
    try:
        from . import baseball_monitoring as bm
        bm.BASEBALL_MONITOR_SAFETY_BUILD = PATCH_VERSION
    except Exception:
        pass


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_live_starter_guard()
    _install_advanced_regression()
    _install_official_model_policy()
    _install_daily_combo_guard()
    _install_monitor_labels()

    # record_frame reads this global at call time, so new snapshots carry the
    # hotfix version without replacing the append-only storage implementation.
    try:
        from . import prediction_store as ps
        ps.MODEL_VERSION = PATCH_VERSION
    except Exception:
        pass
    _INSTALLED = True
