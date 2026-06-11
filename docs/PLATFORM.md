# The Spinner Platform Thesis

Spinner is the flagship of the A.I.M.S. (AI Managed Solutions) / FOAI ecosystem. This
document explains *why* it is a platform and not a feature, and *how* one engine
generalizes across many verticals.

---

## 1. Centralize: ship the platform, not individual apps

The naive path is to build a product (say, Coastal Companion), list it on the Apple App
Store and Google Play, build a separate web surface for it, and then do the whole thing
again for the next product. That fragments the brand, the identity system, the billing,
and the engineering across N independent listings.

The Spinner path: **ship Spinner once** — to the app stores, to the web, and into the
AIMS creator-economy store — and add **apps** inside it.

- **One platform, many apps.** Spinner is both the front door and the engine.
- **The first app is Coastal Companion.** It is LIVE.
- **New apps are added over time**, each a self-contained vertical, without a new
  install or a new store listing.
- **One identity, one billing surface, one execution core**, a growing shelf of apps.

This is the centralization that makes everything downstream cheaper: a new vertical is a
manifest and some wiring, not a new product launch.

---

## 2. The commission / execution model

Spinner is **commissioned, not conversed with.** It has no persona and does not speak to
users. The division of labor:

- **User-facing agents** (Sal, Melli, LUC, ACHEEVY) hold the conversation, decide *what*
  needs doing, and narrate progress.
- **Spinner** is the runtime that *does the work* — an autonomous tool-using loop that
  touches real platform state and takes actions.

An agent commissions Spinner via a `commission_spinner` tool call. The runtime then:

```
commission_spinner(commission_text, prefs, history)
        │
        ▼  POST /api/v1/agent/spinner   (the live commission endpoint)
run_agent()  ──  tool-loop over the gateway surface "spinner_execution"
        │        (model routing is handled by aims_gateway; the runtime
        │         docstring notes Sonnet 4-6 — the file does not hardcode it)
        ▼
each tool call → audit ledger (SQLite spinner_tasks/spinner_events, mirrored to Neon)
                 + a live SSE event the frontend overlay subscribes to
        │
        ▼
returns a final summary → the commissioning agent narrates it
```

Because every tool call is recorded to an **audit ledger** and pushed to a **live event
stream**, the work is both auditable after the fact and visible while it happens. The
frontend renders a split-screen activity overlay from the SSE stream
(`spinner.started` → `tool.<name>` events → `spinner.finished`). The user watches the
platform *do things*, narrated by the agent they were already talking to.

This is grounded in the real engine — see [`../core/spinner_runtime.py`](../core/spinner_runtime.py).
The v1 scope in the live code is **"Shop for me"** with five tools: `search_catalog`,
`get_user_history`, `get_cart`, `cart_add`, `summarize_selection`. Navigation, form-fill,
discount application, and checkout are explicitly deferred to a later scope.

---

## 3. Real-time execution + trigger points

The reason Spinner is a *platform* is the shape underneath the commission model:

```
live multimodal input  →  understand  →  synthesize into a TARGET FRAME  →  fire TRIGGER POINTS
```

- **Live multimodal input** — audio, text, and other streams arriving in real time.
- **Understand** — detect language, intent, entities, commitments, risks.
- **Synthesize into a target frame** — render the input into the form the app cares
  about. The target frame is the app's organizing idea. For translation it is *the chosen
  main language*; for meeting notes it is *the chosen note format*; for an NIL vertical it
  would be *the applicable school/state rules*.
- **Fire trigger points** — when the understood input matches a configured intent, an
  action runs. The action is real: build a cart, create a booking, flag a phrase, open a
  task.

"Translate every language into one main language" is one instance of "render input into a
target frame." It is not a special case; it is the general pattern with the frame set to a
language.

**The conversation does things.** That is the whole platform in one sentence. Examples of
trigger points (with honest status):

| Detected intent | Action | Status |
|---|---|---|
| Product / service question | look up the catalog, build a cart | **LIVE** via `spinner_runtime` |
| "let's meet Thursday" | create a Cal.com booking | PLANNED |
| A risk phrase | flag it | PLANNED |
| A commitment | create a task | PLANNED |

The full catalog lives in [`../platform/triggers.md`](../platform/triggers.md).

---

## 4. How this generalizes across verticals

Every vertical is the same engine with a different `{target frame + triggers + actions +
exports + guardrails}`. The differences between, say, meeting translation and customer
engagement are **configuration**, not a new codebase:

- **Coastal Companion** — target frame = the chosen main language; the live actions are
  translate (ingest) and synthesize-notes; export templates are minutes / summary /
  actions / kpis / bullets / transcript.
- **Customer engagement** — target frame = the product/service catalog; the actions are
  the cart/catalog tools that already exist in the runtime today.
- **NIL advice** — target frame = the applicable school/state NIL rules; actions surface
  cited, informational guidance.
- **Legal** — target frame = the relevant statutes/precedents; actions produce cited,
  paper-trailed, advice-adjacent output under an unauthorized-practice-of-law caution.
- **Education / Open Class** — target frame = a lesson at a chosen level and language;
  actions capture, recap, and quiz.

Because the engine is shared, a capability proven in one vertical (the cart/catalog loop,
proven on Coastal Brewing Co.) is immediately available to skin for another. The
proof-of-concept the platform runs today is **Coastal Brewing Co.**, a human-less
organization — the live test bed for "the conversation does things."

---

## 5. Why this is the flagship

- **Leverage:** one engine, audited and streamed, behind every app.
- **Speed to a new vertical:** a manifest + wiring, not a product launch.
- **Trust:** every action is in the audit ledger and visible in the live overlay.
- **Distribution:** one platform on the app stores + web + the creator-economy store,
  rather than N fragmented listings.

Spinner is the doer. Apps are what the doer is pointed at.
