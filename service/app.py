"""Standalone Spinner service — the platform's realtime app surface.

Extracted from the live Coastal Brewing deployment so other projects consume
Spinner the way they consume Stage Zero and the Tool Warehouse: an independent,
token-gated service, called over the mesh. The first consumer is Coastal
Brewing, where the app is surfaced as "Coastal Companion".

Surface (router prefix `/api/v1/companion`, kept verbatim for clean cut-over):
  POST /ingest /decode /lens /synthesize /glossary/generate   — translate/decode/etc.
  GET  /glossary/{industry} /plan /voices
  POST /speak                                                   — Inworld TTS (paid)
  WS   /realtime/stream                                         — live speech-to-speech duplex (paid)

Auth, two modes (env `SPINNER_REQUIRE_SERVICE_TOKEN`):
  - BRIDGE mode (default, true): the HTTP API requires `X-Service-Token` ==
    SPINNER_SERVICE_TOKEN. The consuming app's server-side bridge holds the token
    and forwards user calls (Warehouse-BFF pattern). The realtime WS is EXEMPT —
    it authenticates the user via the signed `coastal_uid` cookie + the relay's
    own paid gate. `/healthz` is always open.
  - DIRECT mode (false): the browser hits Spinner directly (e.g. served from
    spinner.aimanagedsolutions.cloud). HTTP isn't token-gated; per-endpoint paid
    gates + cookie identity protect billable routes.
Identity + paid-tier come from `identity.py` (shared-secret cookie + pluggable
tier source) via the `api_server`/`audit_ledger` shims.
"""
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import identity
import inworld_router
import realtime_engines
import spinner_service
import billing

app = FastAPI(title="Spinner Service", version="1.0")

_origins = [o.strip() for o in os.environ.get("SPINNER_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_SERVICE_TOKEN = os.environ.get("SPINNER_SERVICE_TOKEN", "").strip()
_REQUIRE_TOKEN = os.environ.get("SPINNER_REQUIRE_SERVICE_TOKEN", "true").strip().lower() in ("1", "true", "yes")


@app.middleware("http")
async def _service_token_gate(request: Request, call_next):
    path = request.url.path
    if (_REQUIRE_TOKEN and _SERVICE_TOKEN and path.startswith("/api/")
            and "/realtime/stream" not in path        # WS authenticates via cookie
            and "/billing/webhook" not in path):       # Stripe webhook is secured by signature
        tok = (request.headers.get("x-service-token")
               or request.query_params.get("service_token"))
        if tok != _SERVICE_TOKEN:
            return JSONResponse({"detail": "service token required"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "spinner",
        "realtime_configured": bool(os.environ.get("INWORLD_REALTIME_API_KEY", "").strip()),
        "engine_inworld": bool(os.environ.get("INWORLD_REALTIME_API_KEY", "").strip()),
        "engine_gemini": realtime_engines.configured_gemini(),
        "tts_configured": bool(os.environ.get("INWORLD_API_KEY", "").strip()),
        "router_configured": inworld_router.configured(),
        "openrouter_configured": bool(os.environ.get("OPENROUTER_API_KEY", "").strip()),
        "auth_secret_set": bool(identity._SECRET),
        "tier_source": identity._TIER_SOURCE,
        "require_service_token": _REQUIRE_TOKEN,
    }


identity.init()
app.include_router(spinner_service.router)
app.include_router(billing.router)
