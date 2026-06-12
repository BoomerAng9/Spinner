"""SHIM — minimal stand-in for Coastal's `api_server`, for the STANDALONE Spinner
service only.

`spinner_service.py` is the proven Coastal router checked in verbatim; in the live
Coastal deployment it does `from api_server import _resolve_uid_cookie`. As a
standalone service Spinner has no Coastal `api_server`, so this module provides
ONLY the identity helpers it imports, delegating to `identity.py`. Cookies signed
by Coastal verify here because the secret is shared (`SPINNER_AUTH_SECRET`).

This file only loads in the standalone Spinner container (which has no real
`api_server.py` on its path) — it never shadows Coastal's module.
"""
import identity

AUTH_SECRET = identity._SECRET


def _resolve_uid_cookie(raw):
    return identity.resolve_uid(raw)


def _sign_uid_for_cookie(uid):
    return identity.sign_uid(uid)
