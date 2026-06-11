# ─────────────────────────────────────────────────────────────────────────
# Extracted VERBATIM from the live Coastal Brewing deployment
# (aims-vps:/docker/coastal-brewing/scripts/aims_agents/spinner_runtime.py).
# This is the platform's execution core — the real agent-commissioned,
# tool-using loop that touches platform state and takes actions. It is
# checked in as-is for reference and provenance; it depends on live sibling
# modules (aims_gateway, catalog, cart_store, user_profile) that ship with
# the deployment and are NOT vendored here, so it is not importable
# standalone. The module docstring and code below are unmodified.
# ─────────────────────────────────────────────────────────────────────────
"""Spinner — agent-commissioned site-action runtime for A.I.M.S.

The "Kodee for A.I.M.S." layer. Other agents (Sal, Melli, LUC, ACHEEVY)
commission Spinner via a `commission_spinner` tool call. Spinner runs an
autonomous tool-using loop that touches real platform state — currently
the Coastal cart, catalog, and user history.

Spinner does NOT speak to users. It has no persona. It is a tool.
The user-facing agent (Sal etc.) narrates while Spinner works; the
frontend renders a split-screen activity overlay tied to the live
event stream.

Flow:
    Sal:    "I'll grab three things for you" → emits commission_spinner(...)
                            ↓
    Frontend POST /api/v1/agent/spinner {commission_text, prefs, history}
                            ↓
    spinner_runtime.run_agent() — Sonnet 4-6 tool-loop:
       search_catalog, get_user_history, get_cart, cart_add, summarize_selection
                            ↓
    Each tool call is recorded to coastal.spinner_tasks (audit ledger) +
    pushed to the SSE stream the overlay subscribes to.
                            ↓
    Spinner finishes → returns summary → Sal narrates.

v1 scope = "Shop for me." Defer navigate / form_fill / apply_discount /
checkout to v2.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

log = logging.getLogger("aims.spinner")

# Local audit DB for Spinner runs. Mirrors the pattern used by
# crucible_judge + lilhawk_dispatch — SQLite is fine here because every
# Spinner run is also already mirrored to Neon via cart_store writes.
SPINNER_DB_PATH = Path(
    os.environ.get(
        "SPINNER_AUDIT_DB",
        str(Path(__file__).resolve().parent.parent.parent / "audit_ledger" / "spinner_tasks.db"),
    )
)


# ─── Activation ─────────────────────────────────────────────────────────


def is_configured() -> bool:
    from aims_gateway import is_configured as _gw
    return _gw()


def missing_keys() -> List[str]:
    out: List[str] = []
    from aims_gateway import is_configured as _gw
    if not _gw():
        out.append("INWORLD_API_KEY")
    return out


# ─── Audit ledger ───────────────────────────────────────────────────────


def _ensure_db() -> None:
    SPINNER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SPINNER_DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS spinner_tasks (
                task_id        TEXT PRIMARY KEY,
                coastal_uid    TEXT,
                commissioned_by TEXT,
                commission_text TEXT,
                status         TEXT,
                final_summary  TEXT,
                tool_call_count INTEGER,
                duration_ms    REAL,
                created_at     TEXT,
                updated_at     TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS spinner_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                seq     INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_spinner_events_task ON spinner_events(task_id, seq)")
        c.commit()


# ─── Live event stream ──────────────────────────────────────────────────
# In-memory ring buffer per task, used by the SSE endpoint. Cleared after
# the task ends + the SSE consumer drains. Keeps last 200 events per task.

_LIVE_EVENTS: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=200))
_TASK_FINAL: Dict[str, bool] = {}


def get_live_events(task_id: str) -> List[dict]:
    return list(_LIVE_EVENTS.get(task_id, deque()))


def is_task_final(task_id: str) -> bool:
    return _TASK_FINAL.get(task_id, False)


def _record_event(task_id: str, event_type: str, payload: dict) -> None:
    seq = len(_LIVE_EVENTS[task_id])
    ev = {
        "task_id": task_id,
        "seq": seq,
        "type": event_type,
        "payload": payload,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _LIVE_EVENTS[task_id].append(ev)
    try:
        with sqlite3.connect(SPINNER_DB_PATH) as c:
            c.execute(
                "INSERT INTO spinner_events (task_id, seq, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, seq, event_type, json.dumps(payload), ev["ts"]),
            )
    except Exception as exc:
        log.warning("spinner: failed to persist event: %s", exc)


# ─── Tool implementations ──────────────────────────────────────────────


def _search_catalog(query: str, limit: int = 10) -> Dict[str, Any]:
    """Search the live Coastal catalog via catalog.py (the source of
    truth — list_products() strips internal cost fields). Falls back to
    catalog.runtime.json if catalog.py isn't on the path (legacy)."""
    products: List[Dict[str, Any]] = []
    try:
        import catalog as _cat
        products = _cat.list_products()
    except Exception as exc:
        # Legacy fallback — only relevant in environments without catalog.py
        catalog_path = Path(__file__).resolve().parent.parent / "catalog.runtime.json"
        if not catalog_path.exists():
            return {"error": f"catalog unreachable: {exc}", "results": []}
        try:
            data = json.loads(catalog_path.read_text(encoding="utf-8"))
            products = data if isinstance(data, list) else data.get("products", [])
        except Exception as exc2:
            return {"error": f"catalog parse failed: {exc2}", "results": []}

    q = (query or "").lower().strip()
    matches: List[Dict[str, Any]] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        haystack = " ".join([
            str(p.get("id", "")),
            str(p.get("sku", "")),
            str(p.get("name", "")),
            str(p.get("title", "")),
            str(p.get("category", "")),
            str(p.get("description", "")),
            " ".join(p.get("tags", [])) if isinstance(p.get("tags"), list) else "",
        ]).lower()
        if not q or q in haystack:
            matches.append({
                "sku": p.get("id") or p.get("sku"),
                "name": p.get("name") or p.get("title"),
                "category": p.get("category"),
                "msrp": p.get("msrp"),
                "description": (p.get("description") or "")[:240],
            })
        if len(matches) >= limit:
            break
    return {"ok": True, "query": query, "count": len(matches), "results": matches}


def _get_user_history(coastal_uid: str) -> Dict[str, Any]:
    if not coastal_uid:
        return {"error": "coastal_uid required"}
    try:
        import user_profile as up
        if not up.is_configured():
            return {"ok": True, "configured": False, "purchases": [], "preferences": {}}
        profile = up.get_profile(coastal_uid)
        purchases = up.get_recent_purchases(coastal_uid, limit=10) if hasattr(up, "get_recent_purchases") else []
        return {
            "ok": True,
            "configured": True,
            "preferences": (profile or {}).get("preferences") or {},
            "last_visit": (profile or {}).get("last_visit"),
            "purchases": purchases,
        }
    except Exception as exc:
        return {"error": f"history fetch failed: {exc}"}


def _get_cart(coastal_uid: str) -> Dict[str, Any]:
    if not coastal_uid:
        return {"error": "coastal_uid required"}
    try:
        import cart_store
        return cart_store.cart_summary(coastal_uid)
    except Exception as exc:
        return {"error": f"cart read failed: {exc}"}


def _cart_add(coastal_uid: str, sku: str, quantity: int, variant: Optional[str], task_id: str) -> Dict[str, Any]:
    if not coastal_uid:
        return {"error": "coastal_uid required"}
    try:
        import cart_store
        items = cart_store.add_item(
            coastal_uid=coastal_uid,
            sku=sku,
            quantity=int(quantity or 1),
            variant=variant,
            added_by="spinner",
            spinner_task_id=task_id,
        )
        # Enrich with catalog data so the SSE consumer can render a real
        # product card (image + name + msrp) instead of a bare SKU string.
        product: Dict[str, Any] = {}
        try:
            import catalog as _cat
            prod = _cat.get_product(sku) or {}
            product = {
                "name":     prod.get("name") or prod.get("title") or sku,
                "category": prod.get("category"),
                "image":    prod.get("image"),
                "msrp":     prod.get("msrp"),
                "size":     prod.get("size"),
                "blurb":    (prod.get("blurb") or "")[:160],
            }
        except Exception:
            pass
        return {
            "ok": True,
            "items": items,
            "added": {
                "sku": sku,
                "quantity": quantity,
                "variant": variant,
                **product,
            },
        }
    except Exception as exc:
        return {"error": f"cart add failed: {exc}"}


def _summarize_selection(items: list, voice_hint: str = "") -> Dict[str, Any]:
    """Build a 1-2 sentence summary the commissioning agent can narrate.
    No LLM call — straight string composition keeps Spinner's tail
    cheap. Gateway has its own summary surface if richer copy is needed."""
    if not items:
        return {"ok": True, "summary": "Nothing in the basket yet."}
    parts = []
    for it in items[:6]:
        qty = int(it.get("quantity", 1))
        name = it.get("name") or it.get("sku") or "item"
        parts.append(f"{qty}x {name}")
    summary = "Picked: " + "; ".join(parts) + "."
    return {"ok": True, "summary": summary, "count": len(items)}


# ─── Tool registry ──────────────────────────────────────────────────────


TOOL_SCHEMA: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "search_catalog",
        "description": "Search the live Coastal catalog by query (matches sku/name/category/description/tags). Returns up to `limit` matches with sku, name, category, msrp.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "maximum": 25},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "get_user_history",
        "description": "Look up a customer's preferences + recent purchases via their coastal_uid. Use this BEFORE picking items so recommendations align with their taste profile.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_cart",
        "description": "Read the current cart contents. Use to avoid duplicate adds + to verify state.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "cart_add",
        "description": "Add an item to the customer's cart. Use after searching the catalog and confirming the SKU exists. quantity defaults to 1.",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "quantity": {"type": "integer", "default": 1, "minimum": 1, "maximum": 12},
            "variant": {"type": "string", "description": "Optional variant key (decaf, ground, whole_bean, etc.). Leave empty for default."},
        }, "required": ["sku"]},
    }},
    {"type": "function", "function": {
        "name": "summarize_selection",
        "description": "Produce a 1-2 sentence summary of what was added to the cart, suitable for the commissioning agent to narrate. Call this LAST.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
            "voice_hint": {"type": "string", "description": "Brand voice style hint (sal, melli, acheevy)."},
        }, "required": ["items"]},
    }},
]


# ─── Agent loop ────────────────────────────────────────────────────────


@dataclass
class SpinnerResult:
    task_id: str
    final_summary: str = ""
    iterations: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0.0


_DEFAULT_SYSTEM_PROMPT = """\
You are Spinner — the A.I.M.S. site-action runtime. You were commissioned \
by a customer-facing agent (Sal/Melli/LUC/ACHEEVY) to perform real work on \
the customer's behalf. You do not talk to the customer. The commissioning \
agent will narrate; you execute.

For "shop for me" commissions:
1. Call get_user_history first to ground recommendations in actual preferences/purchases.
2. Call search_catalog with targeted queries — match the commission's stated interests + history.
3. Call cart_add for each item you pick. Be deliberate: 2-5 items unless told otherwise.
4. End with summarize_selection so the commissioning agent has narration material.

Hard constraints:
- Only add SKUs you confirmed exist via search_catalog.
- Quantity defaults to 1 unless the commission specified otherwise.
- Never call cart_add without the SKU appearing in a prior search_catalog result.
- Never invent variants. Use null/empty unless the catalog showed one.
- Stop after summarize_selection. Don't keep adding items.

Failure mode: if catalog returns nothing or history is empty, pick safe defaults \
that match Coastal's flagship blends. Don't refuse the task — give it your best shot.
"""


def mint_task_id() -> str:
    """Pre-allocate a task_id so the caller can return it before the
    agent loop runs (enables fire-and-stream — POST returns task_id
    immediately, run_agent runs in a background task, SSE consumer
    subscribes in parallel and catches live events)."""
    return "spin_" + uuid.uuid4().hex[:12]


def run_agent(
    commission_text: str,
    coastal_uid: str,
    commissioned_by: str = "agent",
    max_iterations: int = 8,
    system_prompt: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> SpinnerResult:
    """Execute a Spinner commission. Returns the final result; live
    progress is also recorded to _LIVE_EVENTS for SSE consumers.
    Pass `task_id` when the caller already minted one (fire-and-stream)."""
    from aims_gateway import chat_completion as _gw_chat_completion

    started = time.time()
    task_id = task_id or mint_task_id()
    result = SpinnerResult(task_id=task_id)
    _ensure_db()
    now = datetime.now(timezone.utc).isoformat()

    if not is_configured():
        result.error = f"spinner inactive — missing: {', '.join(missing_keys())}"
        result.duration_ms = (time.time() - started) * 1000
        _TASK_FINAL[task_id] = True
        return result

    with sqlite3.connect(SPINNER_DB_PATH) as c:
        c.execute(
            """INSERT INTO spinner_tasks
                (task_id, coastal_uid, commissioned_by, commission_text, status,
                 final_summary, tool_call_count, duration_ms, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', '', 0, 0, ?, ?)""",
            (task_id, coastal_uid, commissioned_by, commission_text[:2000], now, now),
        )

    _record_event(task_id, "spinner.started", {
        "commission": commission_text[:500],
        "commissioned_by": commissioned_by,
        "coastal_uid": coastal_uid,
    })

    user_msg = (
        f"Commission from {commissioned_by}:\n{commission_text}\n\n"
        f"Customer coastal_uid: {coastal_uid}\n"
    )
    if metadata:
        user_msg += f"\nAdditional context (JSON): {json.dumps(metadata)[:1000]}"

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    tool_dispatch = {
        "search_catalog": lambda **kw: _search_catalog(kw.get("query", ""), int(kw.get("limit", 10))),
        "get_user_history": lambda **kw: _get_user_history(coastal_uid),
        "get_cart": lambda **kw: _get_cart(coastal_uid),
        "cart_add": lambda **kw: _cart_add(coastal_uid, kw.get("sku", ""), int(kw.get("quantity", 1)), kw.get("variant"), task_id),
        "summarize_selection": lambda **kw: _summarize_selection(kw.get("items", []), kw.get("voice_hint", "")),
    }

    final_summary_payload: Optional[dict] = None

    try:
        for i in range(max_iterations):
            result.iterations = i + 1
            resp = _gw_chat_completion(
                surface="spinner_execution",
                messages=messages,
                max_tokens=1024,
                temperature=0.2,
                extra_body={"tools": TOOL_SCHEMA, "tool_choice": "auto"},
                timeout=45,
            )
            if not resp:
                result.error = "gateway returned no response"
                break
            choice = (resp.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = msg.get("tool_calls") or []

            if tool_calls:
                messages.append(msg)
                for call in tool_calls:
                    fn_name = (call.get("function") or {}).get("name", "")
                    fn_args_raw = (call.get("function") or {}).get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                    except json.JSONDecodeError:
                        fn_args = {}
                    impl = tool_dispatch.get(fn_name)
                    tool_result = impl(**fn_args) if impl else {"error": f"unknown tool: {fn_name}"}
                    if fn_name == "summarize_selection" and tool_result.get("ok"):
                        final_summary_payload = tool_result
                    record = {
                        "iteration": i + 1,
                        "tool": fn_name,
                        "args": fn_args,
                        "result_preview": json.dumps(tool_result)[:400],
                    }
                    result.tool_calls.append(record)
                    _record_event(task_id, f"tool.{fn_name}", {
                        "args": fn_args,
                        "result": tool_result if isinstance(tool_result, dict) else str(tool_result)[:1000],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(tool_result),
                    })
                continue

            content = msg.get("content")
            result.final_summary = (
                (final_summary_payload or {}).get("summary")
                or (content or "").strip()
            )
            break
        else:
            result.error = f"max_iterations ({max_iterations}) reached without final summary"

    except Exception as exc:
        log.exception("spinner: agent loop failed")
        result.error = f"agent loop failed: {exc}"
    finally:
        result.duration_ms = (time.time() - started) * 1000
        _TASK_FINAL[task_id] = True
        _record_event(task_id, "spinner.finished", {
            "summary": result.final_summary,
            "tool_call_count": len(result.tool_calls),
            "duration_ms": round(result.duration_ms, 1),
            "error": result.error,
        })
        try:
            with sqlite3.connect(SPINNER_DB_PATH) as c:
                c.execute(
                    """UPDATE spinner_tasks SET status = ?, final_summary = ?,
                       tool_call_count = ?, duration_ms = ?, updated_at = ?
                       WHERE task_id = ?""",
                    (
                        "error" if result.error else "done",
                        result.final_summary[:4000],
                        len(result.tool_calls),
                        result.duration_ms,
                        datetime.now(timezone.utc).isoformat(),
                        task_id,
                    ),
                )
        except Exception:
            pass
    return result
