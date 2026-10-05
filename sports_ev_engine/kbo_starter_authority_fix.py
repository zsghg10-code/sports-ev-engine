"""KBO confirmed-lineup starter authority fix.

Installed after v3.4.25 kbo_safety_patch.

Problem fixed:
- KBO GameCenter's daily game-list starter fields can lag behind the already
  published Naver Sports 1-9 lineup and its displayed starting pitchers.
- v3.4.25 treated that mismatch as STARTER CONFLICT forever, which forced all
  KBO picks to REVIEW even when today's lineup and both starters were already
  published.

Policy:
- Only when the existing safety layer has already marked Naver as authoritative
  AND both Naver starter names are present, a mismatch is treated as a stale
  GameCenter row.
- The Naver starters become confirmed/verified, old pitcher IDs are discarded
  by the existing wrapper, advanced starter recent/vs-opponent lookup is rebound
  by the new names, and a complete lineup can become FINAL.
- Partial Naver data does not override the existing safety behavior.
"""
from __future__ import annotations

from typing import Any

from . import kbo_safety_patch as _safety

PATCH_VERSION = "3.6.1-kbo-starter-authority"
_INSTALLED = False
_ORIGINAL = None


def _clean(v: Any) -> str | None:
    s = str(v or "").strip()
    return s or None


def _authoritative_reconcile(
    official_home: Any,
    official_away: Any,
    naver_home: Any,
    naver_away: Any,
    *,
    official_confirmed: bool,
    naver_authoritative: bool,
) -> dict:
    oh, oa = _clean(official_home), _clean(official_away)
    nh, na = _clean(naver_home), _clean(naver_away)

    # The v3.4.25 guarded_context sets naver_authoritative only after a matched
    # KBO date/team game has a source_game_id and complete 1-9 batting orders.
    # Require both starter names as an additional condition before overriding.
    if naver_authoritative and nh and na:
        conflicts = []
        if oh and _safety._norm_name(oh) != _safety._norm_name(nh):
            conflicts.append(f"home {oh} -> {nh}")
        if oa and _safety._norm_name(oa) != _safety._norm_name(na):
            conflicts.append(f"away {oa} -> {na}")

        if conflicts:
            return {
                "home_starter": nh,
                "away_starter": na,
                "starter_confirmed": True,
                "starter_verified": True,
                "starter_source_conflict": False,
                "starter_override_applied": True,
                "starter_official_stale": True,
                "starter_conflict_detail": "; ".join(conflicts),
                "starter_source": (
                    "Naver Sports confirmed lineup "
                    "(stale KBO GameCenter starter overridden)"
                ),
                "starter_signature": _safety.starter_signature(nh, na),
            }

    # Preserve every original v3.4.25 safety decision for partial/unmatched data.
    result = _ORIGINAL(
        official_home,
        official_away,
        naver_home,
        naver_away,
        official_confirmed=official_confirmed,
        naver_authoritative=naver_authoritative,
    )
    result.setdefault("starter_official_stale", False)
    return result


def install() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return

    _ORIGINAL = _safety.reconcile_kbo_starters
    _safety.reconcile_kbo_starters = _authoritative_reconcile
    _safety.PATCH_VERSION = PATCH_VERSION

    # Expose an audit marker without changing the old app compatibility checks
    # for LIVE_BASEBALL_BUILD / BASEBALL_ADVANCED_BUILD.
    try:
        from .providers import live_baseball as lb
        lb.KBO_STARTER_AUTHORITY_BUILD = PATCH_VERSION
    except Exception:
        pass
    try:
        from .providers import baseball_advanced as ba
        ba.KBO_STARTER_AUTHORITY_BUILD = PATCH_VERSION
    except Exception:
        pass

    _INSTALLED = True
