# Spinner

**Spinner is the flagship real-time execution platform for the A.I.M.S. / FOAI ecosystem.**

It is the *doer* — the "Kodee for A.I.M.S." A real-time, agent-commissioned execution
engine that runs an autonomous tool-using loop, touches real platform state, and **takes
actions**. Spinner has no persona and never speaks to users. A user-facing agent (Sal,
Melli, LUC, ACHEEVY) narrates while Spinner works; the frontend renders a live activity
overlay driven by Spinner's event stream.

---

## The thesis: ship the platform, not individual apps

Instead of shipping individual products one at a time to the Apple App Store, Google
Play, and the web, we ship **Spinner** as **THE platform** — on the app stores, on the
web, and in the AIMS creator-economy store. Inside Spinner, users open **apps**.

- **One platform. Many apps.** Spinner is the front door and the engine.
- The **first app is Coastal Companion** (LIVE).
- More apps are added over time, each as a self-contained vertical.

This centralizes everything: one install, one identity, one billing surface, one
execution core — and a growing shelf of apps behind it. We don't fragment the brand and
the engineering across N separate App Store listings; we add a new app to Spinner.

---

## How Spinner works (the engine, in brief)

Spinner is commissioned, not chatted-with. The pattern is:

```
User-facing agent (Sal/Melli/LUC/ACHEEVY)
        │  "I'll grab three things for you"
        ▼  emits  commission_spinner(...)
Spinner runtime  ── autonomous tool-loop ──▶  real platform state (cart, catalog, ...)
        │  every tool call → audit ledger + SSE event
        ▼
returns a summary  ──▶  the agent narrates; the frontend overlay shows the work live
```

The deeper shape that makes Spinner a *platform* and not a single feature:

```
live multimodal input  →  understand  →  synthesize into a TARGET FRAME  →  fire TRIGGER POINTS (actions)
```

"Translate every language into one main language" is just one instance of "render input
into a target frame." A conversation that detects *"let's meet Thursday"* can create a
Cal.com booking; a product question can build a cart (this is **LIVE** today via the
runtime); a risk phrase can be flagged; a commitment can become a task. **The
conversation does things.**

See [`docs/PLATFORM.md`](docs/PLATFORM.md) for the full thesis and
[`core/spinner_runtime.py`](core/spinner_runtime.py) for the real engine, extracted
verbatim from the live Coastal deployment.

---

## The app model (one paragraph)

Each **app** is a vertical fork of the platform defined by a small contract:
`{ target frame + trigger points + actions/tools + export templates + compliance
guardrails }`. The target frame is what input gets rendered into (e.g. "the chosen main
language" for translation). Triggers are intents detected in the live stream; actions are
what fires when a trigger matches; exports are the deliverable templates; guardrails are
the compliance rules the vertical must honor. An app ships as an `app.json` manifest plus
its trigger/action/export wiring and plugs into the same Spinner engine. See
[`docs/APP-MODEL.md`](docs/APP-MODEL.md).

---

## Current status

| App | Status | What it does |
|---|---|---|
| **Coastal Companion** | **LIVE** | Real-time multilingual meeting translation + **live speech-to-speech duplex** (FOAI-Charlotte voice) + AI meeting notes. Free metered translation; $24/mo Spinner+ for voice/realtime (Stripe live). |
| Customer engagement | PLANNED | Product/service Q&A that can act (build a cart). Closest to done — the Spinner cart/catalog actions already exist. |
| NIL advice | PLANNED | Informational guidance framed by school/state NIL rules. Not legal advice. |
| Legal | PLANNED | Advice-adjacent, cited, paper-trailed (unauthorized-practice-of-law caution). |
| Education / "Open Class" | PLANNED | Leveled, multilingual lesson capture + recap + quiz. |

Full table with surfaces and endpoints: [`docs/ROADMAP.md`](docs/ROADMAP.md).

> **Coastal Brewing Co.** is the human-less-organization proof-of-concept that Spinner runs.

---

## How to add an app

An app is a manifest + its triggers, actions, exports, and guardrails. Read
[`docs/APP-MODEL.md`](docs/APP-MODEL.md) for the `app.json` contract, register it in
[`apps/README.md`](apps/README.md), and wire its intents into the trigger catalog at
[`platform/triggers.md`](platform/triggers.md). The first app,
[`apps/coastal-companion/app.json`](apps/coastal-companion/app.json), is the worked
example.

---

## Repository layout

```
README.md                          this file
docs/
  PLATFORM.md                      the platform thesis in depth
  APP-MODEL.md                     the app contract (app.json shape) + how an app plugs in
  ROADMAP.md                       verticals with honest LIVE/PLANNED status
core/
  spinner_runtime.py               the real execution engine (verbatim from live Coastal)
service/                           the DEPLOYABLE Spinner backend service
  spinner_service.py               app surface — translate/decode/lens/voice/realtime (verbatim from live)
  app.py                           FastAPI entrypoint (token-gated) + /healthz
  identity.py                      decoupling seam — shared-secret cookie + pluggable tier source
  Dockerfile / docker-compose.yml  run as a sibling container on aims_aims-network
  README.md                        how the service is consumed (the Stage-Zero pattern)
apps/
  README.md                        app registry index
  coastal-companion/app.json       first app — LIVE
  spinner-web/                     the Spinner web app (index.html) + landing
platform/
  triggers.md                      trigger-point catalog (intent → action, LIVE vs PLANNED)
```

## The service

The engine docs above describe *what* Spinner does; [`service/`](service/) is the
**deployable backend** that does it — the realtime app surface (translate, decode,
explain, summarize, research, ideate, voice, and the live speech-to-speech
**realtime duplex**), extracted from the live Coastal deployment. Other projects
consume it the way they consume **Stage Zero** and the **Tool Warehouse**: an
independent, token-gated service called over the mesh. Coastal Brewing is the first
consumer — surfaced there as **"Coastal Companion"**. See
[`service/README.md`](service/README.md).
