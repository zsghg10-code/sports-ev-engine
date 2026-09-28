"""Lineup maturity resolver for soccer.

Confirmed XIs always outrank public fallbacks.  Model-projected XIs are never
labelled as confirmed and only reduce pre-lineup uncertainty modestly.
"""
from __future__ import annotations


def _both_confirmed(ctx: dict | None) -> bool:
    ctx=ctx or {}
    return bool(ctx.get("home",{}).get("confirmed") and ctx.get("away",{}).get("confirmed"))


def resolve_lineup_stage(deep: dict | None=None, public_ctx: dict | None=None) -> dict:
    deep=deep or {}; public_ctx=public_ctx or {}
    if bool(deep.get("lineup_confirmed")):
        return {"stage":"CONFIRMED","confirmed":True,"probable":False,
                "label":"확정 (API-Football)","source":deep.get("lineup_source") or "API-Football fixtures/lineups",
                "home_players":deep.get("home_lineup_players") or [],"away_players":deep.get("away_lineup_players") or [],
                "home_formation":deep.get("home_formation"),"away_formation":deep.get("away_formation"),"fallback_used":False}
    if _both_confirmed(public_ctx):
        return {"stage":"CONFIRMED","confirmed":True,"probable":False,
                "label":"확정 (공개소스 fallback)","source":"ESPN public lineup fallback",
                "home_players":str(public_ctx.get("home",{}).get("players") or "").split(";") if public_ctx else [],
                "away_players":str(public_ctx.get("away",{}).get("players") or "").split(";") if public_ctx else [],
                "home_formation":None,"away_formation":None,"fallback_used":True}
    if bool(deep.get("probable_lineup_available")):
        return {"stage":"PROBABLE","confirmed":False,"probable":True,
                "label":"예상 (모델 projected XI)","source":deep.get("probable_lineup_source") or "player importance + availability",
                "home_players":deep.get("home_probable_players") or [],"away_players":deep.get("away_probable_players") or [],
                "home_formation":None,"away_formation":None,"fallback_used":True}
    if deep.get("lineup_status")=="PARTIAL":
        return {"stage":"PARTIAL","confirmed":False,"probable":False,"label":"부분 게시","source":deep.get("lineup_source") or "API-Football","fallback_used":False}
    return {"stage":"PRE-LINEUP","confirmed":False,"probable":False,"label":"미확인","source":"—","fallback_used":False}
