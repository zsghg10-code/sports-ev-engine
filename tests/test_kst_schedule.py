from datetime import date

import pandas as pd

from sports_ev_engine.kst_schedule import add_kst_columns, filter_kst_date, format_kst


def test_midnight_boundary_uses_kst_date():
    # 2026-09-28 16:30 UTC == 2026-09-29 01:30 KST
    df = pd.DataFrame([
        {"event_id": "a", "commence_time": "2026-09-28T16:30:00Z"},
        {"event_id": "b", "commence_time": "2026-09-28T14:30:00Z"},
    ])
    out = filter_kst_date(df, date(2026, 9, 29))
    assert out["event_id"].tolist() == ["a"]


def test_kst_display_and_columns():
    value = "2026-09-28T16:30:00Z"
    assert format_kst(value) == "09/29 01:30 KST"
    out = add_kst_columns(pd.DataFrame([{"commence_time": value}]))
    assert out.iloc[0]["kickoff_kst"] == "09/29 01:30 KST"
    assert out.iloc[0]["match_date_kst"] == date(2026, 9, 29)
