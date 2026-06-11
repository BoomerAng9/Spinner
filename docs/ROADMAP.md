# Roadmap

Each app is a vertical fork of the Spinner platform — same engine, different
`{ target frame + triggers + actions + exports + guardrails }`. Status is honest:
**LIVE** = shipped and reachable today; **PLANNED** = designed, not yet shipped.

| App | Status | Description |
|---|---|---|
| **Coastal Companion** | **LIVE** | Real-time multilingual meeting translation + AI meeting notes. |
| **Customer engagement** | PLANNED | Product/service Q&A that can act (build a cart). Closest to done — the Spinner cart/catalog actions already exist in the engine. |
| **NIL advice** | PLANNED | Target frame = school/state NIL rules; needs a rules knowledge base. Informational, not legal advice. |
| **Legal** | PLANNED | Advice-adjacent only, cited, paper-trailed. Unauthorized-practice-of-law caution. |
| **Education / "Open Class"** | PLANNED | Leveled, multilingual lesson capture + recap + quiz. |

---

## Coastal Companion (LIVE)

The first app and the worked example. Manifest:
[`../apps/coastal-companion/app.json`](../apps/coastal-companion/app.json).

- **What it does:** real-time multilingual meeting translation plus AI meeting notes.
- **Target frame:** the chosen main language.
- **Surfaces:**
  - https://brewing.foai.cloud/common-cup
  - https://cbrew.foai.cloud/common-cup
- **Backend endpoints** (on `coastal-runner`):
  - `POST /api/v1/companion/ingest` — audio/text → detect language + translate to the main
    language. Model: `google/gemini-2.5-flash-lite`.
  - `POST /api/v1/companion/synthesize` — transcript + format → notes. Model:
    `google/gemini-2.5-flash`.
- **Export formats:** minutes, summary, actions, kpis, bullets, transcript.
- **Pricing:** $24/mo (Stripe live).
- **Planned within this app:** scheduling detection → Cal.com booking.

---

## Customer engagement (PLANNED — closest to done)

Product/service Q&A that can act. This vertical is closest to shipping because the
**engine actions it needs already exist and are LIVE**: the v1 "Shop for me" scope in
[`../core/spinner_runtime.py`](../core/spinner_runtime.py) provides `search_catalog`,
`get_user_history`, `get_cart`, `cart_add`, and `summarize_selection`. The remaining work
is the vertical's product surface, not the engine.

- **Target frame:** the product/service catalog.
- **LIVE engine capability backing it:** catalog search + cart build via the runtime.

> Note: the cart/catalog *capability* is LIVE in the engine; the customer-engagement
> *product/vertical* is PLANNED. See [`../platform/triggers.md`](../platform/triggers.md).

---

## NIL advice (PLANNED)

- **Target frame:** the applicable school/state NIL rules.
- **Needs:** a rules knowledge base.
- **Posture:** informational guidance, not legal advice.

---

## Legal (PLANNED)

- **Posture:** advice-adjacent only, cited, paper-trailed.
- **Guardrail:** unauthorized-practice-of-law caution is mandatory.

---

## Education / "Open Class" (PLANNED)

- **What it does:** leveled, multilingual lesson capture + recap + quiz.
- **Target frame:** a lesson at a chosen level and language.

---

## The proof-of-concept

**Coastal Brewing Co.** is the human-less-organization proof-of-concept that Spinner
runs — the live test bed for the platform thesis ("the conversation does things").
