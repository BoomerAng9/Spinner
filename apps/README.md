# Apps

This is the app registry index for the Spinner platform. Each app is a vertical fork
defined by `{ target frame + trigger points + actions/tools + export templates +
compliance guardrails }` and shipped as an `app.json` manifest. See
[`../docs/APP-MODEL.md`](../docs/APP-MODEL.md) for the contract and how an app plugs in.

| App | id | Status | Manifest | Description |
|---|---|---|---|---|
| **Coastal Companion** | `coastal-companion` | **LIVE** | [`coastal-companion/app.json`](coastal-companion/app.json) | Real-time multilingual meeting translation + AI meeting notes. |
| Customer engagement | `customer-engagement` | PLANNED | — | Product/service Q&A that can act (build a cart). Cart/catalog engine actions already LIVE. |
| NIL advice | `nil-advice` | PLANNED | — | Informational guidance framed by school/state NIL rules. Not legal advice. |
| Legal | `legal` | PLANNED | — | Advice-adjacent, cited, paper-trailed. UPL caution. |
| Education / Open Class | `open-class` | PLANNED | — | Leveled, multilingual lesson capture + recap + quiz. |

## Adding an app

1. Create `apps/<app-id>/app.json` per the [app model](../docs/APP-MODEL.md).
2. Add a row to this index with an honest `LIVE`/`PLANNED` status.
3. Wire the app's intents into the trigger catalog,
   [`../platform/triggers.md`](../platform/triggers.md).

The engine ([`../core/spinner_runtime.py`](../core/spinner_runtime.py)) is shared and
unchanged — apps configure it, they do not fork it.
