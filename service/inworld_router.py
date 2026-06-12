"""Inworld Router — OpenAI-compatible smart-routing gateway.

POST https://api.inworld.ai/v1/chat/completions, Basic auth via INWORLD_API_KEY
(read at runtime — never hardcoded). Supports every routing mode the owner set up:

  * NAMED ROUTER  — model="inworld/aims-foai" (the owner's custom router) or
                    "inworld/maximize-uptime"; the router's dashboard config picks
                    the model/variant. `extra_body.metadata` feeds conditional
                    routing + {{var}} template substitution.
  * AUTO + SORT   — model="auto" + extra_body.sort ranks by price/latency/
                    throughput/intelligence/math/coding (+ extra_body.ignore).
  * DIRECT        — model="openai/gpt-5.5" etc.
  * FALLBACK CHAIN— extra_body.models=[...] + extra_body.fallback.ttft_timeout
                    ("900ms", min 300ms) for reliability.

Also supports web_search, OpenAI-format tools, and response_format. The resolved
model is in the RESPONSE (`model`), so callers read it from there.

SPINNER USE: the PAID tier's text-reasoning lenses route through the owner's
`inworld/aims-foai` router (default, env-overridable) with a cheap fallback chain;
the free tier stays on $0 OpenRouter models, and paid audio/vision stays on the
proven gemini multimodal path.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("coastal.companion.inworld_router")

_URL = os.environ.get("INWORLD_ROUTER_URL", "https://api.inworld.ai/v1/chat/completions")
# The owner's custom router (created in their Inworld account). Override per-call
# or via env; "auto" (+ sort) is the price/latency-ranked alternative.
DEFAULT_ROUTER = os.environ.get("INWORLD_ROUTER_MODEL", "inworld/aims-foai")


def _key() -> str:
    return os.environ.get("INWORLD_API_KEY", "").strip()


def configured() -> bool:
    return bool(_key())


def route_chat(
    messages: list,
    *,
    model: str | None = None,
    sort: list | None = None,
    web_search: dict | None = None,
    tools: list | None = None,
    response_format: dict | None = None,
    ignore: list | None = None,
    models: list | None = None,
    fallback: dict | None = None,
    metadata: dict | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    timeout: float = 70.0,
) -> dict:
    """Call the Inworld Router. Returns the parsed OpenAI-shaped response dict
    (so `_msg_content` / `usage.total_tokens` / `model` work the same as the
    OpenRouter path). Raises on missing key or HTTP error — callers fall back.

    `sort` is only meaningful with model="auto"; named routers
    (inworld/aims-foai, inworld/maximize-uptime) use their own config + metadata.
    """
    key = _key()
    if not key:
        raise RuntimeError("INWORLD_API_KEY not configured")
    body: dict = {
        "model": model or DEFAULT_ROUTER,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    extra: dict = {}
    if sort:
        extra["sort"] = sort
    if web_search:
        extra["web_search"] = web_search
    if ignore:
        extra["ignore"] = ignore
    if models:
        extra["models"] = models
    if fallback:
        extra["fallback"] = fallback
    if metadata:
        extra["metadata"] = metadata
    if extra:
        body["extra_body"] = extra
    if tools:
        body["tools"] = tools
    if response_format:
        body["response_format"] = response_format
    r = requests.post(
        _URL,
        headers={"Authorization": "Basic " + key, "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()
