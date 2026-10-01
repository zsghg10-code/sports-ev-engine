"""Strict no-lookahead paper-trading eligibility helpers."""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
from .prediction_store import load_predictions, load_settled


def paper_fields(commence_time, recorded_at=None):
    rec=pd.to_datetime(recorded_at or datetime.now(timezone.utc),utc=True,errors="coerce")
    kick=pd.to_datetime(commence_time,utc=True,errors="coerce")
    eligible=not pd.isna(rec) and not pd.isna(kick) and rec < kick
    mins=(kick-rec).total_seconds()/60 if eligible else None
    return {"paper_locked":True,"paper_eligible":bool(eligible),"paper_lock_at":rec.isoformat() if not pd.isna(rec) else None,
            "paper_minutes_before":mins,"lookahead_guard":"PASS" if eligible else "POST_KICKOFF_EXCLUDED"}


def paper_summary():
    preds=load_predictions(); settled=load_settled()
    locked=sum(bool(x.get("paper_locked")) for x in preds)
    eligible=sum(x.get("paper_eligible") is not False for x in preds if x.get("paper_locked"))
    settled_eligible=sum(x.get("paper_eligible") is not False for x in settled)
    return {"snapshots":len(preds),"locked":locked,"pregame_eligible":eligible,"settled_eligible":settled_eligible}
