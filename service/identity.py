"""Identity + tier provider for the standalone Spinner service.

This is the decoupling seam. In the live Coastal deployment the Spinner code
read identity from `api_server._resolve_uid_cookie` and paid-tier from
`audit_ledger.companion_is_paid`. As a standalone service Spinner can't import
Coastal's modules, so those are provided by thin shims (`api_server.py`,
`audit_ledger.py`) that delegate here.

Identity: the consuming app (Coastal) signs a `coastal_uid` cookie as
`<uid>.<hmac16>` using a shared secret. Spinner verifies it with the SAME secret
(`SPINNER_AUTH_SECRET` == Coastal's `COASTAL_AUTH_SECRET`). Same algorithm as
`api_server._sign_uid_for_cookie`, so cookies are interchangeable.

Tier (is this uid paid?): pluggable via `SPINNER_TIER_SOURCE`:
  - "local"    (default) — Spinner owns a tiny SQLite paid store (a Spinner
                billing webhook can mark a uid paid). Self-contained + testable.
  - "callback" — Spinner asks the consuming app over HTTP
                (`SPINNER_TIER_CALLBACK_URL`, with the service token). Use this
                when the consuming app (Coastal) owns billing.
Fails CLOSED (treat as free) on any error — never bill on uncertainty.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

log = logging.getLogger("spinner.identity")

_SECRET = os.environ.get("SPINNER_AUTH_SECRET", "").strip()
_TIER_SOURCE = os.environ.get("SPINNER_TIER_SOURCE", "local").strip().lower()
_TIER_CALLBACK = os.environ.get("SPINNER_TIER_CALLBACK_URL", "").strip()
_SERVICE_TOKEN = os.environ.get("SPINNER_SERVICE_TOKEN", "").strip()
_DB = os.environ.get(
    "SPINNER_IDENTITY_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "spinner_identity.db"),
)
_lock = threading.Lock()


# ── identity (signed cookie) ──────────────────────────────────────────
def sign_uid(uid: str) -> str:
    """Return the signed cookie value `<uid>.<hmac16>` (or bare uid if no secret)."""
    if not _SECRET:
        return uid
    sig = hmac.new(_SECRET.encode("utf-8"), uid.encode("ascii"), hashlib.sha256).hexdigest()[:16]
    return f"{uid}.{sig}"


def resolve_uid(raw: Optional[str]) -> Optional[str]:
    """Verify a raw cookie value → uid, or None on forgery/missing."""
    if not raw:
        return None
    if "." in raw:
        uid, sig = raw.rsplit(".", 1)
        if not _SECRET:
            return uid or None
        expected = hmac.new(_SECRET.encode("utf-8"), uid.encode("ascii"), hashlib.sha256).hexdigest()[:16]
        return uid if hmac.compare_digest(sig, expected) else None
    return raw or None  # legacy unsigned — accept (matches Coastal dual-read)


# ── tier (paid?) ──────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    """Create the local paid store (used when SPINNER_TIER_SOURCE=local)."""
    try:
        with _lock:
            c = _conn()
            try:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS paid_users ("
                    "uid TEXT PRIMARY KEY, status TEXT, updated INTEGER)")
                c.commit()
            finally:
                c.close()
    except Exception as exc:  # never block boot on the store
        log.warning("identity.init failed: %s", exc)


def set_paid(uid: str, status: str = "active") -> None:
    if not uid:
        return
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT OR REPLACE INTO paid_users (uid, status, updated) VALUES (?,?,?)",
                      (uid, status, int(time.time())))
            c.commit()
        finally:
            c.close()


def is_paid(uid: Optional[str]) -> bool:
    """True iff the uid is paid. Fails CLOSED (free) on any error."""
    if not uid:
        return False
    if _TIER_SOURCE == "callback" and _TIER_CALLBACK:
        try:
            import requests
            r = requests.get(_TIER_CALLBACK, params={"uid": uid},
                             headers={"X-Service-Token": _SERVICE_TOKEN}, timeout=6)
            if r.status_code == 200:
                d = r.json() or {}
                return bool(d.get("paid") or d.get("is_paid") or d.get("tier") == "paid")
        except Exception as exc:
            log.warning("tier callback failed for %s: %s", uid, exc)
        return False  # fail closed
    # local store
    try:
        with _lock:
            c = _conn()
            try:
                row = c.execute("SELECT status FROM paid_users WHERE uid=?", (uid,)).fetchone()
                return row is not None and row[0] in ("active", "trialing")
            finally:
                c.close()
    except Exception as exc:
        log.warning("is_paid local lookup failed for %s: %s", uid, exc)
        return False
