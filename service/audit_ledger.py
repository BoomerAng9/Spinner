"""SHIM — minimal stand-in for Coastal's `audit_ledger`, for the STANDALONE
Spinner service only.

The Spinner app surface (translate/decode/explain/summarize/research/ideate,
voice, the realtime duplex) only needs **paid-tier lookup** + schema init, which
delegate to `identity.py`. The Coastal-internal stores this module also exposes —
BYOK keys, Common Cup sessions, Taskade workspaces — are NOT part of the standalone
Spinner service; they degrade to no-op / None here so the endpoints that reference
them import cleanly and simply return empty (they aren't served standalone).

Loads only in the standalone Spinner container; never shadows Coastal's module.
"""
import threading

import identity

_lock = threading.Lock()


def init_schema():
    identity.init()


def companion_is_paid(uid):
    return identity.is_paid(uid)


# ── Coastal-internal stores — degrade in the standalone service ──────────
def companion_workspace_get(uid):
    return None


def companion_byok_fetch(uid, vendor):
    return None


def companion_byok_store(*args, **kwargs):
    return None


def companion_byok_delete(*args, **kwargs):
    return None


def companion_session_start(*args, **kwargs):
    return None


def companion_session_fetch(session_id):
    return None


def companion_session_end(*args, **kwargs):
    return None


def companion_paid_user_upsert(*, coastal_uid, status="active", **kwargs):
    identity.set_paid(coastal_uid, status)


def _connect():
    return identity._conn()
