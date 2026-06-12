"""Spinner freemium usage meter — self-contained SQLite, isolated from the
main audit_ledger so the freemium layer can never destabilize billing or
session accounting. Monthly per-meter token tally.

DESIGN: free OpenRouter models cost $0, so the cap is an UPSELL trigger, not
a spend guard. The meter is therefore BEST-EFFORT and FAILS OPEN — a meter
hiccup must never block a free translation. The only hard invariant lives in
companion.py routing (free/anonymous can never reach a paid model); this
module just counts.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import threading
import time

_DB = pathlib.Path(os.environ.get(
    "SPINNER_USAGE_DB",
    str(pathlib.Path(__file__).resolve().parent.parent / "data" / "spinner_usage.db")))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(_DB), timeout=10)


def init() -> None:
    try:
        with _lock:
            c = _conn()
            try:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS spinner_usage ("
                    "meter_id TEXT NOT NULL, period TEXT NOT NULL, "
                    "tokens_used INTEGER NOT NULL DEFAULT 0, "
                    "requests INTEGER NOT NULL DEFAULT 0, "
                    "updated INTEGER NOT NULL DEFAULT 0, "
                    "PRIMARY KEY (meter_id, period))")
                c.commit()
            finally:
                c.close()
    except Exception:
        pass


def usage_get(meter_id: str, period: str) -> tuple[int, int]:
    """Return (tokens_used, requests) for this meter+period. (0,0) on any error."""
    try:
        with _lock:
            c = _conn()
            try:
                cur = c.execute(
                    "SELECT tokens_used, requests FROM spinner_usage "
                    "WHERE meter_id=? AND period=?", (meter_id, period))
                row = cur.fetchone()
                return (int(row[0]), int(row[1])) if row else (0, 0)
            finally:
                c.close()
    except Exception:
        return (0, 0)


def usage_add(meter_id: str, period: str, tokens: int) -> None:
    """Increment this meter's token tally + request count. Silent on error."""
    try:
        with _lock:
            c = _conn()
            try:
                c.execute(
                    "INSERT INTO spinner_usage (meter_id, period, tokens_used, requests, updated) "
                    "VALUES (?,?,?,1,?) "
                    "ON CONFLICT(meter_id, period) DO UPDATE SET "
                    "tokens_used = tokens_used + excluded.tokens_used, "
                    "requests = requests + 1, updated = excluded.updated",
                    (meter_id, period, int(tokens or 0), int(time.time())))
                c.commit()
            finally:
                c.close()
    except Exception:
        pass
