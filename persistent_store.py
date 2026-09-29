"""Optional persistent storage for Sports EV Engine.

The app always writes its local JSONL files.  When Supabase credentials are
configured it also mirrors records to one generic Postgres table through the
Supabase REST API.  Reads merge remote + local records by stable record id, so a
short remote outage never blocks analysis and a redeploy can recover the remote
history.

Expected table schema is shipped in ``supabase_schema.sql``.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

import requests

_TABLE = "sports_ev_records"
_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or ""
_TIMEOUT = 12
_LOCK = threading.RLock()
_LAST_ERROR = ""
_LAST_OK = None


def configure(url: str | None = None, key: str | None = None) -> None:
    global _URL, _KEY
    if url is not None:
        _URL = str(url).rstrip("/")
    if key is not None:
        _KEY = str(key)


def enabled() -> bool:
    return bool(_URL and _KEY)


def status() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "url": (_URL.split("//", 1)[-1].split("/", 1)[0] if _URL else ""),
        "last_ok": _LAST_OK,
        "last_error": _LAST_ERROR,
        "mode": "supabase+local" if enabled() else "local-jsonl",
    }


def _headers(prefer: str | None = None) -> dict[str, str]:
    h = {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _record_id(row: dict, id_field: str) -> str | None:
    val = row.get(id_field)
    if val is None or str(val) == "":
        return None
    return str(val)


def mirror(kind: str, rows: list[dict], *, id_field: str) -> int:
    """Mirror records to Supabase.  Fail-soft; local JSONL remains canonical fallback."""
    global _LAST_ERROR, _LAST_OK
    if not enabled() or not rows:
        return 0
    payload = []
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        rid = _record_id(row, id_field)
        if not rid:
            continue
        payload.append({
            "kind": kind,
            "record_id": rid,
            "event_id": str(row.get("event_id") or "") or None,
            "sport_key": str(row.get("sport_key") or "") or None,
            "commence_time": row.get("commence_time"),
            "created_at": row.get("recorded_at") or row.get("settled_at") or row.get("reviewed_at") or row.get("observed_at") or now,
            "payload": row,
        })
    if not payload:
        return 0
    try:
        with _LOCK:
            r = requests.post(
                f"{_URL}/rest/v1/{_TABLE}?on_conflict=kind,record_id",
                headers=_headers("resolution=ignore-duplicates,return=minimal"),
                data=json.dumps(payload, ensure_ascii=False, default=str),
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
        _LAST_ERROR = ""
        _LAST_OK = datetime.now(timezone.utc).isoformat()
        return len(payload)
    except Exception as exc:  # fail-soft by design
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return 0


def load(kind: str, *, limit: int = 20000) -> list[dict]:
    """Load one record kind from Supabase. Returns [] on any remote failure."""
    global _LAST_ERROR, _LAST_OK
    if not enabled():
        return []
    out: list[dict] = []
    page = 1000
    start = 0
    try:
        while start < limit:
            params = {
                "kind": f"eq.{kind}",
                "select": "payload,created_at",
                "order": "created_at.asc",
                "limit": str(min(page, limit - start)),
                "offset": str(start),
            }
            r = requests.get(
                f"{_URL}/rest/v1/{_TABLE}",
                headers=_headers(),
                params=params,
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            batch = r.json()
            if not isinstance(batch, list):
                break
            for item in batch:
                p = item.get("payload")
                if isinstance(p, dict):
                    out.append(p)
            if len(batch) < min(page, limit - start):
                break
            start += len(batch)
        _LAST_ERROR = ""
        _LAST_OK = datetime.now(timezone.utc).isoformat()
        return out
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return []


def merge(local_rows: list[dict], remote_rows: list[dict], *, id_field: str) -> list[dict]:
    """Deduplicate remote+local records; local wins only when ids are identical."""
    merged: dict[str, dict] = {}
    anon: list[dict] = []
    for row in remote_rows + local_rows:
        rid = _record_id(row, id_field)
        if rid:
            merged[rid] = row
        else:
            anon.append(row)
    return list(merged.values()) + anon
