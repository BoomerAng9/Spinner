# Spinner Service

The standalone, deployable **Spinner backend** — the platform's realtime app
surface (translate / decode / explain / summarize / research / ideate, voice, and
the live speech-to-speech **realtime duplex**). Extracted from the live Coastal
Brewing deployment so other projects **consume Spinner the way they consume Stage
Zero and the Tool Warehouse**: an independent, token-gated service called over the
mesh — not code copied into each vertical.

> The first consumer is **Coastal Brewing**, where this is surfaced as **"Coastal
> Companion"**. "Coastal Companion" is the *branded label* of the Spinner
> extension inside Coastal — it is powered by this service, not a separate app.

## What's here

| File | Role |
|---|---|
| `spinner_service.py` | The app surface (FastAPI router, prefix `/api/v1/companion`). Checked in **verbatim** from the proven live deployment for provenance. |
| `spinner_meter.py` | Self-contained SQLite freemium usage meter. |
| `inworld_router.py` | Inworld smart-routing client (paid text reasoning). |
| `identity.py` | **The decoupling seam.** Verifies the shared-secret `coastal_uid` cookie; pluggable paid-tier source (`local` SQLite or `callback` to the consuming app). Fails closed. |
| `api_server.py`, `audit_ledger.py` | **Shims** — minimal stand-ins for the Coastal modules `spinner_service.py` imports, delegating to `identity.py`. They load only in this container (never shadow Coastal's real modules). |
| `app.py` | FastAPI entrypoint: service-token gate + `/healthz` + mounts the router. |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | Deploy as a sibling container on `aims_aims-network`. |

## Surface

```
POST /api/v1/companion/ingest               translate one turn (audio/text) → main language
POST /api/v1/companion/decode               decode industry/jargon
POST /api/v1/companion/lens                 explain | research | summarize | ideate
POST /api/v1/companion/synthesize           transcript → formatted notes
POST /api/v1/companion/glossary/generate    build a custom fork glossary
GET  /api/v1/companion/glossary/{industry}
GET  /api/v1/companion/plan                 tier + usage meter + pricing
GET  /api/v1/companion/voices               Inworld voice catalog (custom-first)
POST /api/v1/companion/speak                Inworld TTS (PAID)
WS   /api/v1/companion/realtime/stream      live speech-to-speech duplex (PAID)
GET  /healthz
```

## Auth (two modes)

`SPINNER_REQUIRE_SERVICE_TOKEN`:

- **BRIDGE** (default): the HTTP API requires `X-Service-Token == SPINNER_SERVICE_TOKEN`.
  The consuming app's server-side bridge holds the token and forwards user calls
  (the Warehouse-BFF pattern). The realtime **WS is exempt** — it authenticates
  the user via the signed `coastal_uid` cookie plus the relay's own paid gate.
  `/healthz` is always open.
- **DIRECT**: set to `false` when the browser hits Spinner directly (e.g. served
  from `spinner.aimanagedsolutions.cloud`). HTTP isn't token-gated; per-endpoint
  paid gates + cookie identity protect billable routes. Set `SPINNER_CORS_ORIGINS`.

Identity is a `coastal_uid` cookie signed `<uid>.<hmac16>` with a secret **shared
with the consuming app** (`SPINNER_AUTH_SECRET == Coastal's COASTAL_AUTH_SECRET`),
so cookies are interchangeable. Paid-tier is resolved by `SPINNER_TIER_SOURCE`
(`local` store, or `callback` to the app that owns billing).

## Run

```bash
cp .env.example .env      # fill in keys + SPINNER_AUTH_SECRET + SPINNER_SERVICE_TOKEN
docker compose up -d --build
curl -s http://127.0.0.1:8095/healthz | jq
```

## Provenance / cut-over

`spinner_service.py` is the live Coastal `companion.py` verbatim, so the standalone
service is behaviour-identical to what's proven in production. The migration is
**parallel, then cut over**: stand this up alongside the working inline copy, prove
it, then repoint Coastal's "Coastal Companion" surface to consume it and retire the
inline path. The realtime duplex round-trip is already proven end-to-end (EN→ES,
live STT + translation, the FOAI-Charlotte voice) through the live nginx path.
