"""
Spinner entitlements + token ledger + INTERNAL slow-drip reveal-gate.

Pure data/logic over the Neon schema (spinner_profile / spinner_token_ledger /
spinner_entitlement). NO money movement here — Stripe wiring is a separate layer
that calls grant_tokens()/set_tier() after a confirmed payment.

Slow-drip is INTERNAL — never a pricing page. Offers surface only at the wall:
  FREE  → reveal BMC when free tokens exhausted OR the user attempts a fork
  BMC   → reveal PLANS when the BMC bucket hits 20%-REMAINING (~80% consumed)
  PLANS → on exhaust, overage handoff to LUC
AppSumo is exempt (directly sold).

Token grants are budgeted at the worst-case model rate (Kimi K2.7 $3.41/M ×1.055
OpenRouter fee); real mixed usage costs less, so margin runs above the stated 50%.
"""
from __future__ import annotations
import os
from typing import Optional

import db  # local module: get_conn()

# ── Locked tier config (2026-06-19). Prices in app config, NOT schema. ──────────
FREE_TOKENS = int(os.environ.get("SPINNER_FREE_TOKEN_CAP", "300000"))
DRIP_THRESHOLD = float(os.environ.get("SPINNER_DRIP_REVEAL_REMAINING", "0.20"))  # reveal plans at 20% remaining

TIERS: dict[str, dict] = {
    "free":       {"price": 0.00,  "tokens": FREE_TOKENS, "recurring": None,  "unlocks": []},
    "bmc":        {"price": 6.54,  "tokens": 900_000,     "recurring": None,  "unlocks": ["paid_models", "fork:1"]},
    "drip":       {"price": 14.99, "tokens": 2_000_000,   "recurring": "mo",  "unlocks": ["paid_models", "forks"]},
    "flow":       {"price": 24.99, "tokens": 3_400_000,   "recurring": "mo",  "unlocks": ["paid_models", "forks", "priority"]},
    "current":    {"price": 39.99, "tokens": 5_500_000,   "recurring": "mo",  "unlocks": ["paid_models", "forks", "priority", "charlotte_aiplug"]},
    "appsumo_t1": {"price": 129.0, "tokens": 25_000_000,  "recurring": "yr",  "unlocks": ["paid_models", "forks", "cert:entrepreneurship"]},
    "appsumo_t2": {"price": 249.0, "tokens": 75_000_000,  "recurring": "yr",  "unlocks": ["paid_models", "forks", "cert:entrepreneurship", "cert:cybersecurity", "business_copilot", "course_authoring"]},
    "appsumo_t3": {"price": 399.0, "tokens": 150_000_000, "recurring": "yr",  "unlocks": ["paid_models", "forks", "cert:entrepreneurship", "cert:cybersecurity", "cert:six_sigma_green", "business_copilot", "course_authoring", "charlotte_aiplug"]},
}
PLAN_TIERS = ("drip", "flow", "current")
APPSUMO_TIERS = ("appsumo_t1", "appsumo_t2", "appsumo_t3")


# ── Profile ─────────────────────────────────────────────────────────────────
def ensure_profile(user_id: str, cti_profile_id: Optional[str] = None, display_name: Optional[str] = None) -> None:
    with db.get_conn() as c:
        c.execute(
            "insert into spinner_profile (user_id, cti_profile_id, display_name) values (%s,%s,%s) "
            "on conflict (user_id) do update set "
            "cti_profile_id = coalesce(excluded.cti_profile_id, spinner_profile.cti_profile_id), "
            "display_name = coalesce(excluded.display_name, spinner_profile.display_name), "
            "updated_at = now()",
            (user_id, cti_profile_id, display_name),
        )


# ── Token ledger ────────────────────────────────────────────────────────────
def grant_tokens(user_id: str, tokens: int, reference: str) -> None:
    ensure_profile(user_id)
    with db.get_conn() as c:
        c.execute(
            "insert into spinner_token_ledger (user_id, event, tokens, reference) values (%s,'grant',%s,%s)",
            (user_id, abs(int(tokens)), reference),
        )


def consume_tokens(user_id: str, tokens: int, model: str, reference: str) -> None:
    with db.get_conn() as c:
        c.execute(
            "insert into spinner_token_ledger (user_id, event, tokens, model, reference) values (%s,'consume',%s,%s,%s)",
            (user_id, -abs(int(tokens)), model, reference),
        )


def balance(user_id: str) -> int:
    """Net tokens remaining = sum(grants) + sum(consumes, which are negative)."""
    with db.get_conn() as c:
        row = c.execute(
            "select coalesce(sum(tokens),0) from spinner_token_ledger where user_id=%s", (user_id,)
        ).fetchone()
        return int(row[0])


# ── Entitlement state ───────────────────────────────────────────────────────
def get_tier(user_id: str) -> str:
    with db.get_conn() as c:
        row = c.execute("select tier from spinner_entitlement where user_id=%s", (user_id,)).fetchone()
        return row[0] if row else "free"


def set_tier(user_id: str, tier: str, source: str, expires_at=None, grant: bool = True) -> None:
    """Activate a tier. If grant=True, also credits that tier's token bucket."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier}")
    ensure_profile(user_id)
    with db.get_conn() as c:
        c.execute(
            "insert into spinner_entitlement (user_id, tier, status, source, activated_at, expires_at, updated_at) "
            "values (%s,%s,'active',%s, now(), %s, now()) "
            "on conflict (user_id) do update set tier=excluded.tier, status='active', "
            "source=excluded.source, activated_at=now(), expires_at=excluded.expires_at, updated_at=now()",
            (user_id, tier, source, expires_at),
        )
    if grant:
        grant_tokens(user_id, TIERS[tier]["tokens"], reference=f"tier:{tier}")


# ── Slow-drip reveal-gate (INTERNAL — what offer to surface, if any) ─────────
def next_offer(user_id: str, fork_attempt: bool = False) -> Optional[str]:
    """
    Returns the ONLY offer to surface right now, or None (show nothing).
      'bmc'     → free user exhausted tokens or tried to fork
      'plans'   → bmc user hit the 20%-remaining threshold
      'overage' → plan/appsumo user exhausted → hand to LUC
      None      → don't interrupt the user
    """
    tier = get_tier(user_id)
    bal = balance(user_id)

    if tier == "free":
        if fork_attempt or bal <= 0:
            return "bmc"
        return None

    if tier == "bmc":
        bucket = TIERS["bmc"]["tokens"]
        if bal <= int(DRIP_THRESHOLD * bucket):   # 20% remaining of the BMC bucket
            return "plans"
        return None

    if tier in PLAN_TIERS or tier in APPSUMO_TIERS:
        if bal <= 0:
            return "overage"   # → LUC overage calculator
        return None

    return None


def status(user_id: str) -> dict:
    """Non-secret snapshot for the app/UX (does not itself reveal offers)."""
    tier = get_tier(user_id)
    return {
        "tier": tier,
        "balance": balance(user_id),
        "tokens_in_tier": TIERS.get(tier, {}).get("tokens"),
        "unlocks": TIERS.get(tier, {}).get("unlocks", []),
    }
