# The App Model

An **app** is a vertical fork of the Spinner platform. The platform engine
([`../core/spinner_runtime.py`](../core/spinner_runtime.py)) is shared; an app supplies
the configuration that points the engine at a specific job.

An app is defined by a small, honest contract:

```
{ target frame + trigger points + actions/tools + export templates + compliance guardrails }
```

- **target frame** — what input gets rendered *into*. The app's organizing idea
  (e.g. "the chosen main language" for translation).
- **trigger points** — intents detected in the live stream that should cause something to
  happen.
- **actions / tools** — what fires when a trigger matches. Real work against platform
  state.
- **export templates** — the deliverable shapes the app can produce.
- **compliance guardrails** — the rules the vertical must honor (e.g. "informational, not
  legal advice").

---

## The `app.json` manifest

Every app ships an `app.json`. The shape:

```jsonc
{
  "id": "string",                    // stable slug, unique across the platform
  "name": "string",                  // human-facing app name
  "description": "string",           // one or two sentences

  "surfaces": [                      // where users reach this app
    "https://..."                    // web URLs, app-store deep links, etc.
  ],

  "endpoints": {                     // backend routes this app calls (method + path + notes)
    "ingest":    { "method": "POST", "path": "/api/v1/...", "model": "...", "notes": "..." }
  },

  "target_frame": "string",          // what input is rendered into

  "triggers": [                      // detected intent → action mapping
    {
      "id": "string",
      "intent": "string",            // what to detect in the live stream
      "action": "string",            // what fires (references an entry in `actions`)
      "status": "live" | "planned"
    }
  ],

  "actions": [                       // the tools/actions this app can take
    {
      "id": "string",
      "description": "string",
      "status": "live" | "planned"
    }
  ],

  "exports": [                       // deliverable templates this app can emit
    "string"
  ],

  "guardrails": [                    // compliance rules the vertical must honor
    "string"
  ],

  "status": "live" | "planned",      // honest overall status of the app
  "pricing": { "amount": "string", "interval": "string", "processor": "string" }  // optional
}
```

### Field notes

- **`status` is honest.** `live` means it is shipped and reachable today. `planned` means
  designed but not yet shipped. The same applies per-trigger and per-action, so an app can
  be `live` overall while individual triggers are still `planned` (e.g. Coastal Companion
  is live, but its scheduling→Cal.com trigger is planned).
- **`endpoints`** records the *real* backend routes. For Coastal Companion these are on
  the `coastal-runner` backend and name the model each route uses.
- **`target_frame`** is the single most important field — it is the app's identity. Two
  apps with the same engine differ first in their target frame.
- **`actions` vs `triggers`.** Triggers say *when*; actions say *what*. A trigger
  references an action by id.

---

## How an app plugs into Spinner

1. **Author `app.json`** under `apps/<app-id>/app.json` using the contract above. Set the
   target frame, list the surfaces and real endpoints, and mark every trigger/action with
   an honest `live`/`planned` status.
2. **Register the app** in [`../apps/README.md`](../apps/README.md) — one row in the
   registry index, with its status.
3. **Wire its intents** into the platform trigger catalog,
   [`../platform/triggers.md`](../platform/triggers.md). Triggers that map to actions the
   engine already supports (catalog/cart) can be `live`; triggers needing new actions
   (Cal.com booking, risk flag, task creation) start `planned`.
4. **The engine is unchanged.** Apps do not fork
   [`../core/spinner_runtime.py`](../core/spinner_runtime.py); they configure it. When a
   user-facing agent commissions Spinner for this app's work, the same `run_agent`
   tool-loop executes the app's actions, records them to the audit ledger, and streams
   them to the activity overlay.

The worked example is the first app:
[`../apps/coastal-companion/app.json`](../apps/coastal-companion/app.json).
