"""KST schedule/date helpers used by the Streamlit UI.

The Odds API exposes kickoff timestamps in UTC.  All user-facing calendar
filtering in the Korean dashboard must therefore convert to Asia/Seoul before
comparing calendar dates, otherwise matches around midnight are assigned to
the wrong day.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo("Asia/Seoul")


def to_kst(value: Any) -> pd.Timestamp:
    """Return an aware pandas Timestamp converted to Asia/Seoul."""
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return pd.NaT
    if ts.tzinfo is None:
        # Provider timestamps without an offset are treated as UTC, matching
        # The Odds API commence_time contract used throughout the project.
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.tz_convert(KST)


def format_kst(value: Any, *, include_date: bool = True) -> str:
    """Format kickoff for the UI, e.g. ``09/29 01:00 KST``."""
    try:
        ts = to_kst(value)
    except Exception:
        return "시간 미확인"
    if pd.isna(ts):
        return "시간 미확인"
    return ts.strftime("%m/%d %H:%M KST" if include_date else "%H:%M KST")


def filter_kst_date(frame: pd.DataFrame, selected_date: date, column: str = "commence_time") -> pd.DataFrame:
    """Keep rows whose kickoff falls on ``selected_date`` in KST."""
    if frame is None or frame.empty or column not in frame.columns:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    ts = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.tz_convert(KST)
    return frame.loc[ts.dt.date.eq(selected_date)].copy()


def add_kst_columns(frame: pd.DataFrame, column: str = "commence_time") -> pd.DataFrame:
    """Attach reusable KST date/time display columns without mutating input."""
    if frame is None or frame.empty:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    out = frame.copy()
    if column not in out.columns:
        out["kickoff_kst"] = "시간 미확인"
        out["match_date_kst"] = None
        return out
    ts = pd.to_datetime(out[column], utc=True, errors="coerce").dt.tz_convert(KST)
    out["kickoff_kst"] = ts.dt.strftime("%m/%d %H:%M KST").fillna("시간 미확인")
    out["match_date_kst"] = ts.dt.date
    return out
