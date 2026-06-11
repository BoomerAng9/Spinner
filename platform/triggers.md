# Trigger-Point Catalog

A **trigger point** is an intent detected in the live stream that fires an **action** —
real work against platform state. This is what makes "the conversation does things" true.
The general shape is:

```
live input → understand → synthesize into a TARGET FRAME → fire TRIGGER POINTS (actions)
```

Status is honest: **LIVE** means the action is shipped in the engine today; **PLANNED**
means it is designed but the action does not exist yet.

---

## LIVE

These actions exist in [`../core/spinner_runtime.py`](../core/spinner_runtime.py) — the v1
"Shop for me" scope — and are exercised by the cart/catalog loop today. The
*customer-engagement vertical* that surfaces them as a product is still PLANNED (see
[`../docs/ROADMAP.md`](../docs/ROADMAP.md)); the *capability* below is LIVE.

| Detected intent | Action | Engine tool(s) | Status |
|---|---|---|---|
| Product / service question | look up the catalog and build a cart | `search_catalog`, `get_user_history`, `get_cart`, `cart_add`, `summarize_selection` | **LIVE** |
| Speech/text in another language *(Coastal Companion)* | translate into the chosen main language | `POST /api/v1/companion/ingest` (`google/gemini-2.5-flash-lite`) | **LIVE** |
| Notes requested / meeting segment closes *(Coastal Companion)* | synthesize formatted meeting notes | `POST /api/v1/companion/synthesize` (`google/gemini-2.5-flash`) | **LIVE** |

The cart/catalog flow is commissioned by a user-facing agent via `commission_spinner`,
runs `run_agent()` over the gateway surface `spinner_execution`, records every tool call
to the audit ledger (`spinner_tasks` / `spinner_events`, mirrored to Neon), and streams
`spinner.started` → `tool.<name>` → `spinner.finished` events to the activity overlay.

---

## PLANNED

These are designed trigger points whose actions are not yet built.

| Detected intent | Action | Notes | Status |
|---|---|---|---|
| "let's meet Thursday" (scheduling phrase) | create a Cal.com booking | first planned trigger on Coastal Companion | PLANNED |
| A risk phrase | flag it | surfaces a risk marker on the conversation | PLANNED |
| A commitment ("I'll send that by Friday") | create a task | turns spoken commitments into tracked tasks | PLANNED |
| NIL question | return cited, rules-framed informational guidance | needs a school/state NIL rules knowledge base | PLANNED |
| Legal question | return cited, paper-trailed, advice-adjacent output | unauthorized-practice-of-law caution required | PLANNED |
| Lesson capture | recap + quiz at a chosen level and language | Education / "Open Class" vertical | PLANNED |

---

## Notes for app authors

- A trigger names *when*; an action names *what*. In an `app.json`, a trigger references
  an action by id (see [`../docs/APP-MODEL.md`](../docs/APP-MODEL.md)).
- A trigger that maps to a LIVE engine action (catalog/cart) can be marked `live` in an
  app manifest. A trigger needing a not-yet-built action starts `planned`.
- New actions are added to the engine; apps then reference them. Apps never fork the
  engine.
