"""Fail-soft national-team pre-match context orchestration.

API-Football deep context and public measured-xG fallbacks are deliberately
independent: an API-Football error must never prevent ESPN/FotMob/SofaScore xG checks.
"""
from __future__ import annotations

PATCH_BUILD = '3.4.15-xg-single-pass'

import pandas as pd

from .deep_soccer_context import collect_deep_context, merge_xg_fallback
from .auto_national import collect_recent_xg


def collect_national_context(
    context_api,
    pool,
    home,
    away,
    kickoff,
    *,
    season=None,
    horizon_hours=24,
    public_events=None,
    summary_fetch=None,
    enable_deep=True,
    deep_collector=None,
    xg_collector=None,
):
    """Return fail-soft deep context with independent public xG fallback.

    - API-Football is attempted first when available.
    - Whether API-Football succeeds, fails, rate-limits, or is absent, measured
      public xG is still attempted when ``enable_deep`` and ``public_events``
      are available.
    - Missing xG remains missing; partial samples are retained only for audit.
    """
    deep_collector = deep_collector or collect_deep_context
    xg_collector = xg_collector or collect_recent_xg
    if season is None:
        season = pd.Timestamp(kickoff).year

    if not enable_deep:
        return {
            "deep_context_attempted": False,
            "deep_context_reason": "deep context disabled",
            "lineup_confirmed": False,
            "lineup_status": "NOT_CHECKED",
        }

    if context_api is not None:
        try:
            ctx = deep_collector(
                context_api, pool, home, away, kickoff,
                season=season, horizon_hours=horizon_hours,
            ) or {}
        except Exception as exc:
            ctx = {
                "deep_context_attempted": True,
                "deep_context_reason": f"API-Football collector failed: {type(exc).__name__}: {exc}",
                "deep_context_api_error": f"{type(exc).__name__}: {exc}",
                "lineup_confirmed": False,
                "lineup_status": "ERROR",
            }
    else:
        ctx = {
            "deep_context_attempted": True,
            "deep_context_reason": "API-Football unavailable; public xG fallback still attempted",
            "lineup_confirmed": False,
            "lineup_status": "NOT_CHECKED",
        }

    xg_keys = ("home_xg_for", "home_xg_against", "away_xg_for", "away_xg_against")
    if not all(ctx.get(k) is not None for k in xg_keys) and public_events:
        try:
            kwargs = {"n": 3, "allow_fotmob": True}
            if summary_fetch is not None:
                kwargs["fetch"] = summary_fetch
            public_xg = xg_collector(public_events, home, away, kickoff, **kwargs)
            ctx = merge_xg_fallback(ctx, public_xg)
            ctx["xg_collection_status"] = (
                "OK" if all(ctx.get(k) is not None for k in xg_keys)
                else "ERROR" if (public_xg or {}).get("xg_collector_error")
                else "MISSING"
            )
            # Make the audit trail explicit even if no usable xG was found.
            prior = str(ctx.get("xg_sources_tried") or "").strip()
            chain = "API-Football → ESPN → FotMob → SofaScore"
            ctx["xg_sources_tried"] = chain if not prior else (
                chain if "ESPN" in prior or "FotMob" in prior else f"API-Football → {prior}"
            )
        except Exception as exc:
            ctx["xg_fallback_attempted"] = True
            ctx["xg_fallback_error"] = f"{type(exc).__name__}: {exc}"
            ctx["xg_collection_status"] = "ERROR"
            ctx["xg_samples_home"] = 0
            ctx["xg_samples_away"] = 0
            # A collector call that fails before entering the provider loop has
            # not actually checked ESPN/FotMob/SofaScore.
            ctx["xg_sources_tried"] = "API-Football → public xG collector ERROR"

    elif all(ctx.get(k) is not None for k in xg_keys):
        ctx["xg_collection_status"] = "OK"
    else:
        ctx["xg_collection_status"] = "NO_EVENTS"

    # In deep mode, NOT_CHECKED is never a valid terminal xG state.  Seeing it
    # previously meant app.py and this module came from different ZIP versions.
    if enable_deep and ctx.get("xg_collection_status") in (None, "", "NOT_CHECKED"):
        ctx["xg_collection_status"] = "ERROR"
        ctx["xg_pipeline_invariant_error"] = "deep xG pipeline ended without terminal status"
        ctx["xg_fallback_error"] = ctx.get("xg_fallback_error") or ctx["xg_pipeline_invariant_error"]

    return ctx
