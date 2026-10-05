"""One unattended v3.6 settlement/CLV/review/learning cycle."""
from __future__ import annotations
import json
import os

from .prediction_store import configure_persistence, persistence_status
from .automation_tick import run_auto_cycle

configure_persistence(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"),
)
odds = os.getenv("THE_ODDS_API_KEY")
if not odds:
    raise SystemExit("THE_ODDS_API_KEY is required")

pst = persistence_status()
if not pst.get("enabled"):
    raise SystemExit(
        "Unattended mode requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY "
        "so history survives between scheduler runs."
    )

result = run_auto_cycle(
    odds,
    reserve_credits=int(os.getenv("AUTO_LEARN_RESERVE_CREDITS", "50")),
    collect_clv=os.getenv("AUTO_CLV_ENABLED", "1").strip().lower() not in {"0","false","no"},
)
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
