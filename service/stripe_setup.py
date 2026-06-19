"""
Idempotent Stripe product/price provisioner for Spinner billing.

OWNER RUNS THIS (with your own STRIPE_SECRET_KEY in the env). It creates the
one-time Products/Prices for every SKU and PRINTS the price IDs to paste into the
service env (STRIPE_PRICE_<SKU>). It never moves money and never deletes anything.

    STRIPE_SECRET_KEY=sk_live_... python stripe_setup.py        # create + print
    STRIPE_SECRET_KEY=sk_test_... python stripe_setup.py --test # same, on test mode

Re-running is safe: it looks up existing prices by lookup_key and reuses them.
"""
from __future__ import annotations
import os
import sys

import entitlements as E
from billing import _PACKS, _amount_cents


def _stripe():
    import stripe
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        sys.exit("STRIPE_SECRET_KEY not set — run with your Stripe key in the env.")
    stripe.api_key = key
    return stripe


def _skus() -> list[str]:
    return ["bmc", *_PACKS.keys()]


def _ensure_price(stripe, sku: str) -> str:
    """Create-or-reuse a one-time price keyed by lookup_key=spinner_<sku>. Returns price id."""
    lookup = f"spinner_{sku}"
    existing = stripe.Price.list(lookup_keys=[lookup], limit=1).data
    if existing:
        return existing[0].id
    if sku == "bmc":
        name = "Spinner — Buy Me a Coffee"
    else:
        tier, paid, access = _PACKS[sku]
        name = f"Spinner — {tier.title()} ({paid} mo" + (f", {access} mo access" if access != paid else "") + ")"
    product = stripe.Product.create(name=name, metadata={"sku": sku})
    price = stripe.Price.create(
        product=product.id,
        unit_amount=_amount_cents(sku),
        currency="usd",
        lookup_key=lookup,
        metadata={"sku": sku},
    )
    return price.id


def main() -> None:
    stripe = _stripe()
    test = "--test" in sys.argv
    mode = "TEST" if test else "LIVE"
    print(f"# Stripe {mode} — provisioning Spinner SKUs (idempotent)\n")
    env_lines = []
    for sku in _skus():
        pid = _ensure_price(stripe, sku)
        env_lines.append(f"STRIPE_PRICE_{sku.upper()}={pid}")
        print(f"  {sku:12} ${_amount_cents(sku)/100:>7.2f}  -> {pid}")
    print("\n# Paste into the service env:\n")
    print("\n".join(sorted(env_lines)))
    print("\n# Then set STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, SPINNER_PUBLIC_URL and restart.")


if __name__ == "__main__":
    main()
