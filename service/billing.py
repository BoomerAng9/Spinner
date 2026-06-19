"""
Spinner billing — Stripe checkout + secure webhook + AppSumo redemption.

MONEY DISCIPLINE
- Tokens/tier are granted ONLY on a signature-verified webhook (never on checkout
  creation). No money path runs without STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET.
- All keys/price-IDs come from env. Nothing is hardcoded. Endpoints return 503 when
  billing is not configured (safe default — the service still runs).
- Fulfillment calls entitlements.set_tier()/grant_tokens() and mirrors paid state to
  identity.set_paid() so existing paid-gates keep working.

SKUs
- bmc                    one-time $6.54 → ~900K tokens, unlock (no expiry)
- {drip,flow,current}_{3,6,9}  one-time pack: pay N months, get access months
  (3→3, 6→6, 9→12), tokens = monthly_bucket × access_months, expires at period end
- appsumo_t{1,2,3}       redeemed via code → 1-year, capped annual tokens (carry-over add)
"""
from __future__ import annotations
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import identity
import entitlements as E
import db

log = logging.getLogger("spinner.billing")
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# ── SKU catalog (amounts derive from the locked TIERS prices) ───────────────
# sku -> (tier, paid_months, access_months). bmc handled specially.
_PACKS = {f"{t}_{p}": (t, p, (12 if p == 9 else p))
          for t in ("drip", "flow", "current") for p in (3, 6, 9)}


def _amount_cents(sku: str) -> int:
    if sku == "bmc":
        return round(E.TIERS["bmc"]["price"] * 100)
    tier, paid, _ = _PACKS[sku]
    return round(E.TIERS[tier]["price"] * paid * 100)


def _price_env(sku: str) -> str:
    return os.environ.get(f"STRIPE_PRICE_{sku.upper()}", "").strip()


def _configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())


def _stripe():
    import stripe  # lazy — module loads even without the dep
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"].strip()
    return stripe


def _uid(request: Request) -> Optional[str]:
    return identity.resolve_uid(request.cookies.get("coastal_uid"))


# ── Fulfillment (called only after verified payment / valid code) ───────────
def _fulfill(uid: str, sku: str, source: str) -> None:
    now = datetime.now(timezone.utc)
    if sku == "bmc":
        tier, tokens, expires = "bmc", E.TIERS["bmc"]["tokens"], None
    elif sku in _PACKS:
        tier, _paid, access = _PACKS[sku]
        tokens = E.TIERS[tier]["tokens"] * access
        expires = now + timedelta(days=round(30.44 * access))
    elif sku in E.APPSUMO_TIERS:
        tier, tokens = sku, E.TIERS[sku]["tokens"]      # carry-over: grant ADDS to balance
        expires = now + timedelta(days=365)
    else:
        raise ValueError(f"unknown sku: {sku}")
    E.set_tier(uid, tier, source=source, expires_at=expires, grant=False)
    E.grant_tokens(uid, tokens, reference=sku)
    identity.set_paid(uid, "active")                    # keep existing paid-gates in sync
    log.info("fulfilled uid=%s sku=%s tier=%s tokens=%s", uid, sku, tier, tokens)


# ── Endpoints ───────────────────────────────────────────────────────────────
@router.get("/catalog")
def catalog():
    """Non-secret SKU list for the app to render REVEALED offers (slow-drip decides when)."""
    items = [{"sku": "bmc", "tier": "bmc", "amount_cents": _amount_cents("bmc"),
              "kind": "onetime", "configured": bool(_price_env("bmc"))}]
    for sku, (tier, paid, access) in _PACKS.items():
        items.append({"sku": sku, "tier": tier, "paid_months": paid, "access_months": access,
                      "amount_cents": _amount_cents(sku), "kind": "pack",
                      "configured": bool(_price_env(sku))})
    return {"billing_configured": _configured(), "items": items}


class CheckoutReq(BaseModel):
    sku: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


@router.post("/checkout")
def checkout(req: CheckoutReq, request: Request):
    if not _configured():
        return JSONResponse({"detail": "billing not configured"}, status_code=503)
    uid = _uid(request)
    if not uid:
        return JSONResponse({"detail": "identity required"}, status_code=401)
    if req.sku != "bmc" and req.sku not in _PACKS:
        return JSONResponse({"detail": "unknown sku"}, status_code=400)
    price_id = _price_env(req.sku)
    if not price_id:
        return JSONResponse({"detail": f"price not configured for {req.sku}"}, status_code=503)
    base = os.environ.get("SPINNER_PUBLIC_URL", "").rstrip("/")
    stripe = _stripe()
    sess = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=req.success_url or f"{base}/billing/success?sku={req.sku}",
        cancel_url=req.cancel_url or f"{base}/billing/cancel",
        client_reference_id=uid,
        metadata={"uid": uid, "sku": req.sku},
    )
    return {"url": sess.url, "id": sess.id}


@router.post("/webhook")
async def webhook(request: Request):
    """Stripe → grant entitlements. EXEMPT from the service-token gate; secured by signature."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not _configured() or not secret:
        return JSONResponse({"detail": "billing not configured"}, status_code=503)
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as exc:  # signature/parse failure
        log.warning("webhook signature verification failed: %s", exc)
        return JSONResponse({"detail": "invalid signature"}, status_code=400)
    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"]
        if s.get("payment_status") == "paid":
            meta = s.get("metadata") or {}
            uid, sku = meta.get("uid"), meta.get("sku")
            if uid and sku:
                _fulfill(uid, sku, source="stripe")
    return {"received": True}


class RedeemReq(BaseModel):
    code: str


@router.post("/appsumo/redeem")
def appsumo_redeem(req: RedeemReq, request: Request):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"detail": "identity required"}, status_code=401)
    code = req.code.strip()
    with db.get_conn() as c:
        row = c.execute(
            "select tier, status from spinner_appsumo_code where code=%s", (code,)
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "invalid code"}, status_code=404)
        tier, status = row
        if status != "unused":
            return JSONResponse({"detail": "code already redeemed"}, status_code=409)
        c.execute(
            "update spinner_appsumo_code set status='redeemed', redeemed_by=%s, redeemed_at=now() "
            "where code=%s and status='unused'", (uid, code),
        )
    _fulfill(uid, tier, source="appsumo")               # carry-over: adds annual tokens to balance
    return {"redeemed": True, "tier": tier, "status": E.status(uid)}
