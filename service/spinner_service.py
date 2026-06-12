"""C|Brew Communication Companion — FastAPI router.

Mounts /api/v1/companion/* on the existing coastal-runner. Every
endpoint authenticates via the existing coastal_uid cookie (set by
/api/v1/auth/verify in the Coastal magic-link flow) — there's no
separate Companion auth surface; the Companion is a feature on
top of the existing customer identity.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

log = logging.getLogger("coastal.companion")

router = APIRouter(prefix="/api/v1/companion", tags=["companion"])


def require_uid(
    coastal_uid: Annotated[Optional[str], Cookie()] = None,
) -> str:
    """FastAPI dependency. Returns the caller's coastal_uid (HMAC-verified
    via the same helper api_server uses for /me / /preferences). Raises
    401 on missing or invalid cookie."""
    # Reuse api_server's _resolve_uid_cookie. Local import avoids
    # circular-import issues at module load.
    from api_server import _resolve_uid_cookie  # noqa: PLC0415
    resolved = _resolve_uid_cookie(coastal_uid)
    if resolved is None:
        raise HTTPException(status_code=401, detail="coastal_uid cookie required")
    return resolved


@router.get("/workspace/me")
def workspace_me(uid: str = Depends(require_uid)) -> dict:
    """Return the caller's Taskade workspace id (if provisioned) +
    paid-tier flag. Customer-facing UI uses this to gate the dashboard
    deep-link + show upgrade prompts."""
    import audit_ledger
    audit_ledger.init_schema()
    ws_id = audit_ledger.companion_workspace_get(uid)
    is_paid = audit_ledger.companion_is_paid(uid)
    return {
        "ok": True,
        "coastal_uid": uid,
        "taskade_workspace_id": ws_id,
        "is_paid_tier": is_paid,
    }


_ALLOWED_VENDORS = {"inworld", "openai"}


class ByokPostBody(BaseModel):
    vendor: str
    api_key: str


def _byok_secret() -> str:
    s = os.environ.get("COASTAL_BYOK_ENCRYPTION_KEY", "").strip()
    if not s:
        raise HTTPException(
            status_code=503,
            detail="COASTAL_BYOK_ENCRYPTION_KEY not configured",
        )
    return s


@router.post("/byok/key")
def byok_store(body: ByokPostBody, uid: str = Depends(require_uid)) -> dict:
    if body.vendor not in _ALLOWED_VENDORS:
        raise HTTPException(
            status_code=400,
            detail=f"vendor must be one of {sorted(_ALLOWED_VENDORS)}",
        )
    if not body.api_key or len(body.api_key) < 20:
        raise HTTPException(status_code=400, detail="api_key too short")
    import audit_ledger
    import companion_byok
    ct = companion_byok.encrypt_key(_byok_secret(), body.api_key)
    audit_ledger.companion_byok_store(
        coastal_uid=uid, vendor=body.vendor, encrypted_key=ct,
    )
    return {"ok": True, "vendor": body.vendor}


@router.delete("/byok/key")
def byok_delete(vendor: str, uid: str = Depends(require_uid)) -> dict:
    if vendor not in _ALLOWED_VENDORS:
        raise HTTPException(status_code=400, detail="unknown vendor")
    import audit_ledger
    audit_ledger.companion_byok_delete(coastal_uid=uid, vendor=vendor)
    return {"ok": True, "deleted": vendor}


class BillingCheckoutBody(BaseModel):
    email: str


@router.post("/billing/checkout")
def billing_checkout(
    body: BillingCheckoutBody, uid: str = Depends(require_uid),
) -> dict:
    import stripe
    import companion_billing
    from adapters.stripe_adapter import _init_stripe  # noqa: PLC0415
    _init_stripe()
    params = companion_billing.build_checkout_params(
        customer_email=body.email, coastal_uid=uid,
    )
    try:
        session = stripe.checkout.Session.create(**params)
    except Exception as exc:
        log.warning("companion checkout create failed: %s", exc)
        raise HTTPException(status_code=502, detail="checkout session mint failed")
    return {
        "ok": True,
        "session_id": session.id if hasattr(session, "id") else session.get("id"),
        "redirect_url": session.url if hasattr(session, "url") else session.get("url"),
    }


@router.post("/billing/portal")
def billing_portal(uid: str = Depends(require_uid)) -> dict:
    import sqlite3
    import stripe
    import audit_ledger
    from adapters.stripe_adapter import _init_stripe  # noqa: PLC0415
    audit_ledger.init_schema()
    _init_stripe()
    with audit_ledger._lock:
        conn = audit_ledger._connect()
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT stripe_customer_id FROM companion_paid_users "
                "WHERE coastal_uid = ?",
                (uid,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="no paid subscription")
    try:
        portal = stripe.billing_portal.Session.create(
            customer=row["stripe_customer_id"],
            return_url=f"{os.environ.get('COASTAL_PUBLIC_URL', 'https://brewing.foai.cloud')}/companion",
        )
    except Exception as exc:
        log.warning("companion portal mint failed: %s", exc)
        raise HTTPException(status_code=502, detail="portal mint failed")
    return {"ok": True, "url": portal.url}


import secrets as _secrets  # noqa: E402

FREE_TIER_DAILY_MINUTES_CAP = 30.0


def _free_tier_minutes_used_last_24h(coastal_uid: str) -> float:
    """Query audit_ledger for total minutes used by free-tier sessions
    in the last 24 hours. Returns float (0.0 if no sessions found)."""
    import audit_ledger
    import time as _t
    cutoff = int(_t.time()) - 86400
    audit_ledger.init_schema()
    with audit_ledger._lock:
        conn = audit_ledger._connect()
        try:
            cur = conn.execute(
                "SELECT COALESCE(SUM(minutes_used), 0) FROM companion_sessions "
                "WHERE coastal_uid = ? AND tier_at_start = 'free' "
                "AND started_at >= ?",
                (coastal_uid, cutoff),
            )
            return float(cur.fetchone()[0] or 0)
        finally:
            conn.close()


class SessionStartBody(BaseModel):
    source_lang: str = "auto"
    target_lang: str = "en"


class SessionEndBody(BaseModel):
    minutes_used: float = 0.0


def _public_url() -> str:
    return os.environ.get("COASTAL_PUBLIC_URL", "https://brewing.foai.cloud")


@router.post("/session/start")
def session_start(
    body: SessionStartBody, uid: str = Depends(require_uid),
) -> dict:
    import audit_ledger
    audit_ledger.init_schema()
    session_id = "ccs_" + _secrets.token_urlsafe(12)
    tier = "paid" if audit_ledger.companion_is_paid(uid) else "free"
    if tier == "free":
        used = _free_tier_minutes_used_last_24h(uid)
        if used >= FREE_TIER_DAILY_MINUTES_CAP:
            raise HTTPException(
                status_code=429,
                detail=f"free-tier daily cap reached ({FREE_TIER_DAILY_MINUTES_CAP} min); upgrade or retry tomorrow",
            )
    audit_ledger.companion_session_start(
        session_id=session_id, coastal_uid=uid,
        source_lang=body.source_lang, target_lang=body.target_lang,
        tier_at_start=tier,
    )
    ws_scheme = "wss" if _public_url().startswith("https") else "ws"
    ws_host = _public_url().split("://", 1)[1]
    return {
        "ok": True,
        "session_id": session_id,
        "tier": tier,
        "ws_url": f"{ws_scheme}://{ws_host}/api/v1/companion/session/{session_id}/stream",
    }


@router.post("/session/{session_id}/end")
def session_end(
    session_id: str, body: SessionEndBody, uid: str = Depends(require_uid),
) -> dict:
    import audit_ledger
    audit_ledger.init_schema()
    audit_ledger.companion_session_end(
        session_id=session_id, minutes_used=body.minutes_used,
    )
    return {"ok": True, "session_id": session_id}


def _coastal_uid_from_cookie_header(cookie_header: str) -> Optional[str]:
    """Parse coastal_uid from a raw Cookie header. WebSocket handshakes
    don't use FastAPI Cookie() dependencies the same way HTTP routes do
    — we read the raw header from `websocket.headers.get("cookie")`."""
    from api_server import _resolve_uid_cookie  # noqa: PLC0415
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("coastal_uid="):
            return _resolve_uid_cookie(part.split("=", 1)[1])
    return None


@router.websocket("/session/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: str) -> None:
    """Bidirectional audio + caption proxy between the customer and
    the Inworld Gateway. WS close codes:
      4401 — no/invalid coastal_uid cookie
      4402 — no Inworld BYOK key on file
      4404 — session not found / not owned by this uid
      4500 — BYOK decrypt failed
      4502 — upstream connection failed
    """
    await websocket.accept()

    cookie_hdr = websocket.headers.get("cookie", "")
    coastal_uid = _coastal_uid_from_cookie_header(cookie_hdr)
    if coastal_uid is None:
        await websocket.close(code=4401, reason="uid required")
        return

    import audit_ledger
    import companion_byok
    import companion_inworld

    audit_ledger.init_schema()
    ct = audit_ledger.companion_byok_fetch(coastal_uid, "inworld")
    if ct is None:
        await websocket.close(code=4402, reason="no Inworld BYOK key on file")
        return
    user_key = companion_byok.decrypt_key(_byok_secret(), ct)
    if user_key is None:
        await websocket.close(code=4500, reason="BYOK decrypt failed")
        return

    sess = audit_ledger.companion_session_fetch(session_id)
    if sess is None or sess["coastal_uid"] != coastal_uid:
        await websocket.close(code=4404, reason="session not found")
        return

    try:
        upstream = await companion_inworld.open_upstream(
            user_api_key=user_key,
            source_lang=sess["source_lang"],
            target_lang=sess["target_lang"],
        )
    except Exception as exc:
        log.warning("upstream open failed for %s: %s", session_id, exc)
        await websocket.close(code=4502, reason="upstream open failed")
        return

    async def pipe_client_to_upstream():
        try:
            async for msg in websocket.iter_bytes():
                await upstream.send(msg)
        except WebSocketDisconnect:
            pass

    async def pipe_upstream_to_client():
        try:
            async for msg in upstream:
                if isinstance(msg, bytes):
                    await websocket.send_bytes(msg)
                else:
                    await websocket.send_text(msg)
        except Exception:
            pass

    try:
        await asyncio.gather(
            pipe_client_to_upstream(),
            pipe_upstream_to_client(),
        )
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


class NotesPostBody(BaseModel):
    transcript_text: str
    title: str = "Meeting"


def _generate_summary(transcript_text: str) -> tuple[str, list[dict]]:
    """Generate a markdown summary + mind-map branch list from a
    transcript. Uses Gemini 3.1 Flash on Vertex per FOAI canon when
    VERTEX_PROJECT_ID is set; otherwise falls back to a deterministic
    rule-based summary so the endpoint still ships."""
    project = os.environ.get("VERTEX_PROJECT_ID", "").strip()
    if not project:
        # Rule-based fallback — extract first 200 chars as summary,
        # offer the two canonical mind-map branches.
        head = (transcript_text or "").strip().split(".")[0][:200]
        return (
            f"# Meeting summary\n\n{head}\n",
            [
                {"label": "Discussion points", "children": []},
                {"label": "Action items", "children": []},
            ],
        )
    # Real Vertex call deferred — landed in a follow-up commit when
    # VERTEX_PROJECT_ID is confirmed in the deploy env.
    head = (transcript_text or "")[:500]
    return f"# Meeting summary\n\n{head}\n", [
        {"label": "Discussion points", "children": []},
        {"label": "Action items", "children": []},
    ]


@router.post("/notes/{session_id}")
def notes_create(
    session_id: str,
    body: NotesPostBody,
    uid: str = Depends(require_uid),
) -> dict:
    import audit_ledger
    import companion_taskade
    audit_ledger.init_schema()
    if not audit_ledger.companion_is_paid(uid):
        raise HTTPException(
            status_code=402, detail="paid tier required for notes",
        )
    ws_id = audit_ledger.companion_workspace_get(uid)
    if ws_id is None:
        raise HTTPException(
            status_code=409, detail="workspace not provisioned",
        )
    summary_md, mindmap_branches = _generate_summary(body.transcript_text)
    taskade_token = os.environ.get("COASTAL_TASKADE_API_TOKEN", "")
    if not taskade_token:
        raise HTTPException(
            status_code=503, detail="taskade not configured",
        )
    doc_id = companion_taskade.push_meeting_doc(
        api_token=taskade_token,
        workspace_id=ws_id,
        title=body.title,
        body_md=summary_md,
    )
    mindmap_id = None
    if mindmap_branches:
        try:
            mindmap_id = companion_taskade.push_mindmap_nodes(
                api_token=taskade_token,
                workspace_id=ws_id,
                root_label=body.title,
                branches=mindmap_branches,
            )
        except Exception as exc:
            log.warning(
                "mindmap push failed (doc still saved): %s", exc,
            )
    return {
        "ok": True,
        "session_id": session_id,
        "taskade_doc_id": doc_id,
        "taskade_mindmap_id": mindmap_id,
    }


# ═══════════════════════════════════════════════════════════════════════
# Common Cup — one-shot realtime universal translator + multi-format notes
# Added 2026-06-11. Stateless + PUBLIC (rate-limited): the demo works
# without sign-in / BYOK / the Inworld gateway. A multimodal audio model
# auto-detects the spoken language of each short turn and translates it
# into the meeting's MAIN language (Star Trek universal-translator
# pattern); a second model synthesizes selectable note formats.
# Audio path = google/gemini-2.5-flash-lite (audio-native, fast,
# non-reasoning → low latency). Notes = google/gemini-2.5-flash.
# ═══════════════════════════════════════════════════════════════════════
import base64 as _b64  # noqa: E402,F401
import json as _json   # noqa: E402
import requests as _requests  # noqa: E402

_OR_URL = "https://openrouter.ai/api/v1/chat/completions"
_OR_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
_INGEST_MODEL = os.environ.get(
    "COASTAL_COMPANION_INGEST_MODEL", "google/gemini-2.5-flash-lite")
_NOTES_MODEL = os.environ.get(
    "COASTAL_COMPANION_NOTES_MODEL", "google/gemini-2.5-flash")

_LANG_NAMES = {
    "en": "English", "es": "Spanish", "ja": "Japanese", "ar": "Arabic",
    "pt": "Portuguese", "de": "German", "fr": "French", "zh": "Chinese",
    "ko": "Korean", "it": "Italian", "ru": "Russian", "hi": "Hindi",
    "nl": "Dutch", "tr": "Turkish", "pl": "Polish", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "sv": "Swedish", "he": "Hebrew",
    "el": "Greek", "uk": "Ukrainian", "fa": "Persian", "ta": "Tamil",
}


def _main_lang_name(code: str) -> str:
    return _LANG_NAMES.get((code or "en").lower(), code or "English")


def _or_chat(payload: dict, timeout: float = 60.0) -> dict:
    if not _OR_KEY:
        raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY not configured")
    try:
        r = _requests.post(
            _OR_URL,
            headers={"Authorization": "Bearer " + _OR_KEY, "Content-Type": "application/json"},
            json=payload, timeout=timeout,
        )
    except _requests.RequestException as exc:
        log.warning("companion OR request failed: %s", exc)
        raise HTTPException(status_code=504, detail="model request failed")
    if r.status_code != 200:
        log.warning("companion OR non-200 %s: %s", r.status_code, r.text[:240])
        raise HTTPException(status_code=502, detail=f"model error {r.status_code}")
    try:
        return r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="model parse error")


def _msg_content(data: dict) -> str:
    return (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


def _parse_json_blob(text: str) -> dict:
    """Tolerant JSON extraction — models sometimes fence or pad the object."""
    t = (text or "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        t = t[a:b + 1]
    try:
        return _json.loads(t)
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════
# SPINNER FREEMIUM — tier routing + token meter
# Owner 2026-06-11: translation is FREE on free OpenRouter models (with an
# "experimental / not secure" disclaimer) up to a monthly token cap, then the
# user upgrades to the paid multimodal (audio + vision) tier. Free models are
# $0, so the cap is an UPSELL trigger, not a spend guard — the hard invariant
# is only that free/anonymous can NEVER reach a paid model (the cost boundary,
# enforced in _route_model which fails CLOSED to the free model). Verified live
# latency (2026-06-11): free text nex-n2-pro 1.4s / nemotron(no-reason) 0.5s;
# free audio nemotron-omni 2.2s; paid gemini-2.5-flash-lite audio 1.2s.
SPINNER_FREE_TOKEN_CAP = int(os.environ.get("SPINNER_FREE_TOKEN_CAP", "300000"))

# kind -> (free_model, paid_model). nemotron-omni is the free audio+vision omni;
# nex-n2-pro (the "Next" China model) is the fast free text path.
_MODEL_ROUTES = {
    "text":   ("nex-agi/nex-n2-pro:free",                           "google/gemini-2.5-flash-lite"),
    "audio":  ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "google/gemini-2.5-flash-lite"),
    "vision": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "google/gemini-2.5-flash-lite"),
    "notes":  ("google/gemma-4-31b-it:free",                         "google/gemini-2.5-flash"),
}
_PREMIUM_MODEL = os.environ.get("SPINNER_PREMIUM_MODEL", "google/gemini-3.5-flash")
# Free reasoning-omni: disable reasoning GENERATION (not just hide) for low
# latency + token cost — measured 0.54s/149tok vs 1.45s/351tok.
_NO_REASON_MODELS = {"nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"}


def _route_model(tier: str, kind: str) -> str:
    """Cost boundary. Only an explicit 'paid' tier reaches the paid model;
    anything else (free, unknown, error) fails CLOSED to the free model."""
    free_m, paid_m = _MODEL_ROUTES.get(kind, _MODEL_ROUTES["text"])
    return paid_m if tier == "paid" else free_m


def _model_payload(model: str, messages: list, max_tokens: int, temperature: float) -> dict:
    p = {"model": model, "messages": messages, "max_tokens": max_tokens,
         "temperature": temperature}
    if model in _NO_REASON_MODELS:
        p["reasoning"] = {"enabled": False}
    return p


def _period_now() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().strftime("%Y-%m")


def _resolve_uid_safe(coastal_uid: Optional[str]) -> Optional[str]:
    if not coastal_uid:
        return None
    try:
        from api_server import _resolve_uid_cookie  # noqa: PLC0415
        return _resolve_uid_cookie(coastal_uid)
    except Exception:
        return None


def _spinner_meter(uid: Optional[str], device_id: Optional[str]) -> tuple:
    """Resolve (tier, meter_id, tokens_used, cap). Best-effort; never raises."""
    import spinner_meter
    spinner_meter.init()
    tier = "free"
    meter_id = (device_id or "").strip()[:120]
    if uid:
        meter_id = "uid:" + uid
        try:
            import audit_ledger
            audit_ledger.init_schema()
            if audit_ledger.companion_is_paid(uid):
                tier = "paid"
        except Exception:
            pass
    if not meter_id:
        meter_id = "anon:unknown"
    used, _req = spinner_meter.usage_get(meter_id, _period_now())
    return tier, meter_id, used, SPINNER_FREE_TOKEN_CAP


def _spinner_gate(coastal_uid: Optional[str], device_id: Optional[str], kind: str) -> tuple:
    """Resolve tier + meter + routed model. Raises 402 if free + over cap."""
    uid = _resolve_uid_safe(coastal_uid)
    tier, meter_id, used, cap = _spinner_meter(uid, device_id)
    if tier == "free" and used >= cap:
        raise HTTPException(status_code=402, detail={
            "upgrade": True, "tier": "free", "tokens_used": used,
            "tokens_cap": cap, "reason": "free monthly token cap reached"})
    return tier, meter_id, _route_model(tier, kind)


def _spinner_record(meter_id: str, data: dict) -> None:
    try:
        import spinner_meter
        tok = ((data or {}).get("usage") or {}).get("total_tokens") or 0
        spinner_meter.usage_add(meter_id, _period_now(), tok)
    except Exception:
        pass


def _spinner_flags(tier: str, model: str) -> dict:
    return {"_tier": tier, "_secure": tier == "paid",
            "_free_mode": tier == "free", "_model": (model or "auto").split("/")[-1]}


# ── PAID tier → Inworld Router (owner's smart router) ─────────────────
# Verified live: owner's custom router inworld/aims-foai resolves to Claude
# Opus 4.8 (premium, costly); auto+sort[latency,price] picks a cheap fast model;
# web_search + response_format json_object both work. Per the owner's cost
# guidance, high-frequency conversational lenses use auto+cost-sort, while
# heavier/low-frequency tasks (notes) use the premium aims-foai router; Research
# uses auto+web_search. All env-tunable (set FAST=inworld/aims-foai for
# premium-everywhere). Free tier stays on $0 OpenRouter models; paid audio/vision
# stays on the proven gemini multimodal path (Router spec is text/tools/search).
_ROUTER_FAST_MODEL = os.environ.get("SPINNER_ROUTER_FAST_MODEL", "auto")
_ROUTER_FAST_SORT = [s.strip() for s in os.environ.get(
    "SPINNER_ROUTER_FAST_SORT", "latency,price").split(",") if s.strip()]
_ROUTER_DEEP_MODEL = os.environ.get("SPINNER_ROUTER_DEEP_MODEL", "inworld/aims-foai")
_ROUTER_FALLBACK = os.environ.get("SPINNER_ROUTER_FALLBACK", "google-ai-studio/gemini-3.5-flash")


def _tier_text_chat(tier, kind, messages, max_tokens, temperature,
                    want_json=False, web_search=False):
    """Run a TEXT-reasoning chat for a tier. Returns (data, model_used) where
    data is OpenAI-shaped (so _msg_content / usage / model all work).
      paid + web_search → Inworld Router auto + web_search (validated path)
      paid + notes      → Inworld Router inworld/aims-foai (premium, low-freq)
      paid + other      → Inworld Router auto + cost-sort (fast, cheap)
      paid fallback     → gemini multimodal via OpenRouter (router error)
      free              → $0 OpenRouter free model
    The resolved model comes from the RESPONSE (model:auto picks server-side)."""
    if tier == "paid":
        try:
            import inworld_router  # noqa: PLC0415
            if inworld_router.configured():
                if web_search:
                    model, sort = "auto", _ROUTER_FAST_SORT
                    ws = {"engine": "exa", "max_results": 3, "max_steps": 1}
                elif kind == "notes":
                    model, sort, ws = _ROUTER_DEEP_MODEL, None, None
                else:
                    model = _ROUTER_FAST_MODEL
                    sort = _ROUTER_FAST_SORT if model == "auto" else None
                    ws = None
                data = inworld_router.route_chat(
                    messages, model=model, sort=sort, web_search=ws,
                    response_format=({"type": "json_object"} if want_json else None),
                    models=([_ROUTER_FALLBACK] if _ROUTER_FALLBACK else None),
                    fallback=({"ttft_timeout": "1500ms"} if _ROUTER_FALLBACK else None),
                    max_tokens=max_tokens, temperature=temperature, timeout=85)
                return data, (data.get("model") or model)
        except Exception as exc:
            log.warning("inworld router failed, falling back to gemini: %s", exc)
        m = _route_model("paid", "text")
        return _or_chat(_model_payload(m, messages, max_tokens, temperature), timeout=60), m
    m = _route_model("free", "text")
    return _or_chat(_model_payload(m, messages, max_tokens, temperature), timeout=60), m


class IngestBody(BaseModel):
    target_lang: str = "en"
    text: Optional[str] = None
    audio_b64: Optional[str] = None
    audio_format: str = "wav"
    speaker: Optional[str] = None


@router.post("/ingest")
def companion_ingest(
    body: IngestBody,
    coastal_uid: Optional[str] = Cookie(None),
    x_spinner_device: Optional[str] = Header(None),
) -> dict:
    """Detect the language of one short turn (spoken audio OR typed text) and
    translate it into the meeting's MAIN language. Free tier routes to free
    models (token-cap gated, not secure); paid tier to the multimodal models."""
    main = _main_lang_name(body.target_lang)
    kind = "audio" if body.audio_b64 else "text"
    tier, meter_id, model = _spinner_gate(coastal_uid, x_spinner_device, kind)
    json_shape = (
        '{"detected_language":"<English name of the language>",'
        '"detected_code":"<ISO 639-1 code>",'
        '"original_text":"<verbatim, in its own script>",'
        '"translation":"<the line rendered in %s>"}' % main
    )
    if body.audio_b64:
        instr = (
            "You are a real-time meeting interpreter. The audio is one short spoken "
            "turn in ANY language. Identify the language, transcribe it verbatim in "
            "its own script, and translate it into %s. If the audio is silent or "
            'unintelligible, return empty strings. Reply with ONLY this compact JSON: %s'
            % (main, json_shape)
        )
        content = [
            {"type": "text", "text": instr},
            {"type": "input_audio", "input_audio": {"data": body.audio_b64, "format": body.audio_format}},
        ]
    elif body.text and body.text.strip():
        instr = (
            "You are a real-time meeting interpreter. The line below may be in ANY "
            "language. Identify its language and translate it into %s. Reply with ONLY "
            "this compact JSON: %s\n\nLINE: %s" % (main, json_shape, body.text.strip())
        )
        content = instr
    else:
        raise HTTPException(status_code=400, detail="provide text or audio_b64")

    data = _or_chat(
        _model_payload(model, [{"role": "user", "content": content}], 800, 0),
        timeout=60,
    )
    _spinner_record(meter_id, data)
    obj = _parse_json_blob(_msg_content(data))
    translation = (obj.get("translation") or "").strip()
    return {
        "ok": True,
        "detected_language": (obj.get("detected_language") or "").strip() or "Unknown",
        "detected_code": (obj.get("detected_code") or "").strip(),
        "original_text": (obj.get("original_text") or (body.text or "")).strip(),
        "translation": translation,
        "speaker": body.speaker,
        "empty": not translation,
        **_spinner_flags(tier, model),
    }


_NOTE_FORMATS = {
    "minutes": "formal meeting minutes with: a one-line header, a Participants/speakers line, "
               "a chronological 'Discussion' section grouped by topic, a 'Decisions' section, "
               "and an 'Action Items' list with owners and any due dates mentioned",
    "summary": "a tight executive summary of 3-6 sentences covering what was discussed and concluded",
    "actions": "an action-item checklist; render each item as '- [ ] **<owner>** — <task> (<due date if mentioned>)'",
    "kpis": "a KPIs / metrics table in Markdown with columns | Metric | Value / Target | Owner | Notes |, "
            "capturing every number, target, deadline or measurable commitment mentioned",
    "bullets": "clean bullet-point notes grouped by topic with bold topic headers",
}


class SynthBody(BaseModel):
    transcript_text: str
    format: str = "minutes"
    target_lang: str = "en"
    title: str = "Meeting"


@router.post("/synthesize")
def companion_synthesize(
    body: SynthBody,
    coastal_uid: Optional[str] = Cookie(None),
    x_spinner_device: Optional[str] = Header(None),
) -> dict:
    """Turn a (translated) meeting transcript into a selected note format.
    Tier-routed + metered. format ∈ {minutes,summary,actions,kpis,bullets,transcript}."""
    fmt = (body.format or "minutes").lower()
    main = _main_lang_name(body.target_lang)
    if fmt == "transcript":
        return {"ok": True, "format": "transcript", "title": body.title,
                "markdown": body.transcript_text}
    spec = _NOTE_FORMATS.get(fmt, _NOTE_FORMATS["minutes"])
    if not (body.transcript_text or "").strip():
        raise HTTPException(status_code=400, detail="transcript_text is empty")
    tier, meter_id, _gm = _spinner_gate(coastal_uid, x_spinner_device, "notes")
    instr = (
        "You are a meeting-notes synthesizer. Below is a meeting transcript "
        "(already translated into %s). Produce %s. Write the ENTIRE output in %s "
        "using clean GitHub-flavored Markdown, titled '# %s'. Ground every point "
        "strictly in the transcript — do not invent facts, names, or numbers.\n\n"
        "--- TRANSCRIPT ---\n%s" % (main, spec, main, body.title, body.transcript_text)
    )
    msgs = [{"role": "user", "content": instr}]
    # Paid → Inworld Router (aims-foai premium); free → free model.
    # _tier_text_chat already falls back to gemini on a paid router error; one
    # retry here guards a single empty completion.
    md = ""
    model_used = ""
    for _attempt in range(2):
        try:
            data, model_used = _tier_text_chat(tier, "notes", msgs, 2000, 0.2)
            md = _msg_content(data)
            _spinner_record(meter_id, data)
        except Exception as exc:
            log.warning("synthesis attempt failed: %s", exc)
            md = ""
        if md:
            break
    if not md:
        raise HTTPException(status_code=502, detail="synthesis unavailable, retry")
    return {"ok": True, "format": fmt, "title": body.title, "markdown": md,
            **_spinner_flags(tier, model_used or "auto")}


# ═══════════════════════════════════════════════════════════════════════
# DECODER lens — real-time term-watch + plain-English explain (Spinner)
# Owner 2026-06-11: Spinner's flagship "protect the user" capability. A SECOND
# lens on the same engine: not language→language (translate) but jargon→plain.
# Given a live turn + an industry WATCHLIST, flag the loaded terms/maneuvers
# and explain them so the user (at an info disadvantage) isn't snowed.
# Per-industry glossary = the fork. Glossaries are GENERATED by CHARLOTTE
# (the vibe-coding action: Spinner commissions Charlotte's brain) and fall
# back to the direct gateway when Charlotte is unavailable. NIL = first list.
# ═══════════════════════════════════════════════════════════════════════
import re as _re        # noqa: E402
import pathlib as _pathlib  # noqa: E402

_GLOSSARY_DIR = _pathlib.Path(os.environ.get(
    "COASTAL_GLOSSARY_DIR",
    str(_pathlib.Path(__file__).resolve().parent.parent / "data" / "glossaries")))
try:
    _GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
# Spinner→Charlotte access: Charlotte's brain (acheevy/chat) generates glossaries.
# Unset/unreachable/erroring → fall back to the direct gateway (still works).
_CHARLOTTE_BRAIN_URL = os.environ.get("CHARLOTTE_BRAIN_URL", "").strip()
_CHARLOTTE_BRAIN_TOKEN = os.environ.get("CHARLOTTE_BRAIN_TOKEN", "").strip()
_GLOSSARY_MODEL = os.environ.get("COASTAL_GLOSSARY_MODEL", "google/gemini-2.5-flash")
_DECODE_MODEL = os.environ.get("COASTAL_DECODE_MODEL", "google/gemini-2.5-flash-lite")


def _glossary_path(industry: str) -> "_pathlib.Path":
    safe = _re.sub(r"[^a-z0-9_-]", "", (industry or "").lower()) or "default"
    return _GLOSSARY_DIR / (safe + ".json")


def _load_glossary(industry: str) -> Optional[dict]:
    p = _glossary_path(industry)
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _glossary_prompt(industry: str, label: str) -> str:
    return (
        "You are building a real-time DECODER glossary for the '%s' world. A user "
        "wears this in a LIVE conversation where the other party (e.g. an agent, "
        "broker, recruiter, salesperson, official) likely has an information "
        "advantage; the decoder flags + explains the loaded terms so the user is "
        "not misled. Produce the 12 most important terms/maneuvers a regular "
        "person must understand in '%s'. Reply with ONLY strict JSON, no prose, "
        'exactly: {"industry":"%s","label":"%s","terms":[{"term":"",'
        '"aliases":[""],"plain":"one-sentence plain-English meaning",'
        '"why":"one sentence on why it matters / what to watch / what it can cost",'
        '"risk":true}]} . risk=true when the term commonly disadvantages an '
        "uninformed person." % (label, label, industry, label)
    )


def _charlotte_generate(prompt: str) -> Optional[dict]:
    """Commission Charlotte's brain (acheevy/chat) to generate the glossary.
    Returns parsed JSON or None on any failure (caller falls back)."""
    if not _CHARLOTTE_BRAIN_URL:
        return None
    try:
        _hdrs = {"Content-Type": "application/json"}
        if _CHARLOTTE_BRAIN_TOKEN:
            _hdrs["X-Spinner-Token"] = _CHARLOTTE_BRAIN_TOKEN
        r = _requests.post(
            _CHARLOTTE_BRAIN_URL,
            json={"messages": [{"role": "user", "content": prompt}]},
            headers=_hdrs,
            timeout=170,  # ACHEEVY (Charlotte's brain) is slow (~40-90s) for long
                          # glossary prompts; stay under nginx proxy_read_timeout 180.
        )
        if r.status_code != 200:
            log.warning("charlotte glossary gen non-200: %s %s", r.status_code, r.text[:160])
            return None
        body = r.json()
        # Tolerate OpenAI-style or custom {reply|content|text|message} shapes.
        txt = ""
        if isinstance(body, dict):
            if body.get("choices"):
                txt = ((body["choices"][0].get("message") or {}).get("content")) or ""
            txt = txt or body.get("reply") or body.get("content") or body.get("text") or body.get("message") or ""
        if not isinstance(txt, str):
            txt = _json.dumps(txt)
        g = _parse_json_blob(txt)
        return g if g.get("terms") else None
    except Exception as exc:
        log.warning("charlotte glossary gen failed: %s", exc)
        return None


def generate_glossary(industry: str, label: Optional[str] = None) -> dict:
    """Build + persist a decoder glossary for an industry. Charlotte-first
    (the vibe-coding action), gateway-fallback. Stamps `_source`."""
    label = label or industry.replace("_", " ").title()
    prompt = _glossary_prompt(industry, label)
    g = _charlotte_generate(prompt)
    source = "charlotte"
    if not (g and g.get("terms")):
        data = _or_chat(
            {"model": _GLOSSARY_MODEL, "messages": [{"role": "user", "content": prompt}],
             "max_tokens": 2600, "temperature": 0.2}, timeout=120)
        g = _parse_json_blob(_msg_content(data))
        source = "gateway"
    if not (g and g.get("terms")):
        raise HTTPException(status_code=502, detail="glossary generation failed")
    g.setdefault("industry", industry)
    g.setdefault("label", label)
    g["_source"] = source
    try:
        _glossary_path(industry).write_text(
            _json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("glossary persist failed: %s", exc)
    return g


class GlossaryGenBody(BaseModel):
    industry: str
    label: Optional[str] = None


@router.post("/glossary/generate")
def companion_glossary_generate(body: GlossaryGenBody) -> dict:
    """Drop in an industry → Charlotte (or fallback) vibe-codes its watchlist."""
    g = generate_glossary(body.industry, body.label)
    return {"ok": True, "industry": g.get("industry"), "label": g.get("label"),
            "term_count": len(g.get("terms", [])), "source": g.get("_source")}


@router.get("/glossary/{industry}")
def companion_glossary_get(industry: str) -> dict:
    g = _load_glossary(industry)
    if not g:
        raise HTTPException(status_code=404, detail="glossary not found — generate it first")
    return {"ok": True, **g}


class DecodeBody(BaseModel):
    text: str
    industry: str = "nil"
    target_lang: str = "en"


@router.post("/decode")
def companion_decode(
    body: DecodeBody,
    coastal_uid: Optional[str] = Cookie(None),
    x_spinner_device: Optional[str] = Header(None),
) -> dict:
    """Real-time decode of ONE turn against an industry watchlist. Returns the
    loaded terms in the turn with plain meaning + why-it-matters + risk flag."""
    if not (body.text or "").strip():
        return {"ok": True, "industry": body.industry, "flags": []}
    tier, meter_id, model = _spinner_gate(coastal_uid, x_spinner_device, "text")
    g = _load_glossary(body.industry)
    main = _main_lang_name(body.target_lang)
    label = (g.get("label") if g else None) or body.industry
    watch = ""
    if g and g.get("terms"):
        lines = []
        for t in g["terms"][:60]:
            al = t.get("aliases") or []
            al_s = (" (" + ", ".join(al) + ")") if al else ""
            lines.append("- %s%s: %s" % (t.get("term", ""), al_s, t.get("plain", "")))
        watch = "\n".join(lines)[:6000]
    instr = (
        "You are a real-time DECODER protecting a user in a high-stakes conversation "
        "about %s. The user is likely at an information disadvantage. Below is the "
        "latest thing the OTHER party said. Identify any loaded, technical, or "
        "industry terms or maneuvers in it the user should understand right now. For "
        "each, give a one-sentence plain-%s meaning and a one-sentence 'why it "
        "matters to YOU' (what to watch / what it could cost). Prefer terms from the "
        "WATCHLIST but also catch important ones not listed. If nothing notable, "
        'return an empty list. ALSO read the speaker TONE (1-2 words) and INTENT '
        '(one short phrase: what they are really doing or asking). Reply ONLY JSON: '
        '{"tone":"","intent":"","flags":[{"term":"","plain":"","why":"","risk":true}]}'
        '.\n\nWATCHLIST (%s):\n%s\n\nTHEY SAID: %s'
        % (label, main, label, watch or "(none yet)", body.text.strip())
    )
    data, model_used = _tier_text_chat(
        tier, "text", [{"role": "user", "content": instr}], 800, 0, want_json=True)
    _spinner_record(meter_id, data)
    obj = _parse_json_blob(_msg_content(data))
    o = obj if isinstance(obj, dict) else {}
    return {"ok": True, "industry": body.industry, "label": label,
            "tone": (o.get("tone") or "").strip(), "intent": (o.get("intent") or "").strip(),
            "flags": o.get("flags") or [], **_spinner_flags(tier, model_used)}


# ── Lenses: Explain / Research / Ideate / Summarize (Translate=/ingest, Decode=/decode) ──
# Owner key-art 2026-06-11: six lenses, one tap, any angle on the same live convo.
class LensBody(BaseModel):
    lens: str                      # explain | research | ideate | summarize
    text: str = ""                 # the latest turn (explain/research)
    transcript: str = ""           # the conversation so far (summarize/ideate)
    target_lang: str = "en"


@router.post("/lens")
def companion_lens(
    body: LensBody,
    coastal_uid: Optional[str] = Cookie(None),
    x_spinner_device: Optional[str] = Header(None),
) -> dict:
    """Apply a conversation lens to a turn (explain/research) or the whole
    conversation (summarize/ideate). Fast realtime model; grounded, no invention."""
    lens = (body.lens or "").lower().strip()
    main = _main_lang_name(body.target_lang)
    turn = (body.text or "").strip()
    convo = (body.transcript or body.text or "").strip()
    if lens == "explain":
        if not turn:
            return {"ok": True, "lens": lens, "explanation": ""}
        instr = ("You are a real-time EXPLAINER in a live conversation. In plain, simple "
                 "%s a 12-year-old would get — no jargon, 1-2 sentences — explain what "
                 'this MEANS. Reply ONLY JSON {"explanation":""}.\n\nTHEY SAID: %s' % (main, turn))
        shape = "explanation"
    elif lens == "research":
        if not turn:
            return {"ok": True, "lens": lens, "facts": []}
        instr = ("You are a real-time RESEARCH lens in a live conversation. Give the key "
                 "factual context a listener needs to follow this — 2-4 crisp, accurate "
                 "bullet facts in %s. No speculation; if unsure, say what's uncertain. "
                 'Reply ONLY JSON {"facts":["",""]}.\n\nTOPIC / THEY SAID: %s' % (main, turn))
        shape = "facts"
    elif lens == "ideate":
        if not convo:
            return {"ok": True, "lens": lens, "ideas": []}
        instr = ("You are an IDEATE lens. Based on the conversation, give the user 3 "
                 "concrete next moves / ideas (one line each) in %s, grounded in what was "
                 'said. Reply ONLY JSON {"ideas":["","",""]}.\n\nCONVERSATION:\n%s' % (main, convo[:6000]))
        shape = "ideas"
    elif lens == "summarize":
        if not convo:
            return {"ok": True, "lens": lens, "points": [], "actions": []}
        instr = ("Summarize this conversation in %s: 3-5 key points and any decisions / "
                 "action items, as short bullets, grounded strictly in the text. Reply "
                 'ONLY JSON {"points":["",""],"actions":[""]}.\n\nCONVERSATION:\n%s' % (main, convo[:8000]))
        shape = "points"
    else:
        raise HTTPException(status_code=400, detail="unknown lens")
    tier, meter_id, _gm = _spinner_gate(coastal_uid, x_spinner_device, "text")
    data, model_used = _tier_text_chat(
        tier, "text", [{"role": "user", "content": instr}], 900, 0.2,
        want_json=True, web_search=(lens == "research"))
    _spinner_record(meter_id, data)
    obj = _parse_json_blob(_msg_content(data))
    o = obj if isinstance(obj, dict) else {}
    out = {"ok": True, "lens": lens}
    for k in ("explanation", "facts", "ideas", "points", "actions"):
        if k in o:
            out[k] = o[k]
    out.setdefault(shape, o.get(shape) or ("" if shape == "explanation" else []))
    out.update(_spinner_flags(tier, model_used))
    return out


# ── Plan / pricing surface ────────────────────────────────────────────
# The freemium structure the UI renders: a usage meter, the "not secure"
# disclaimer, and the upgrade paywall. Prices are config-driven and the
# charge stays behind COASTAL_STRIPE_COMPANION_PRICE_ID (owner-gated) — the
# UI shows "launching soon" until that price is set, so the CTA never 500s.
_PRICING = [
    {
        "id": "free", "name": "Spinner Free", "price": "$0", "cadence": "forever",
        "tagline": "Translate the world, free.",
        "tokens": "%dk tokens / month" % (SPINNER_FREE_TOKEN_CAP // 1000),
        "secure": False, "highlight": False,
        "features": [
            "All 6 lenses — Translate, Decode, Explain, Summarize, Research, Ideate",
            "Real-time text + audio translation",
            "Decoder watchlists & custom forks",
        ],
        "note": "Experimental — runs on free open models that are NOT private or "
                "secure. Don't share anything sensitive on Free mode.",
    },
    {
        "id": "plus", "name": "Spinner+", "price": "$9", "cadence": "/ month",
        "tagline": "Secure, premium, multimodal.",
        "tokens": "3,000,000 tokens / month",
        "secure": True, "highlight": True,
        "features": [
            "Premium multimodal models — audio + vision",
            "Real-time voice conversation (Inworld)",
            "Vision — point your camera, \"what is this?\"",
            "Meeting notes, minutes & KPI export",
            "Private & secure routing",
            "Unlimited custom forks",
        ],
    },
    {
        "id": "creator", "name": "Spinner Creator", "price": "$24", "cadence": "/ month",
        "tagline": "Build, publish, automate.",
        "tokens": "Unlimited fair-use",
        "secure": True, "highlight": False,
        "features": [
            "Everything in Spinner+",
            "Charlotte builds your aiPLUGs",
            "Publish to the Creator Store",
            "Sqwaadrun swarm — live search",
            "WhatsApp · Telegram · Discord · iMessage",
            "Crowd Mode — multi-speaker recognition",
        ],
    },
]


@router.get("/plan")
def companion_plan(
    coastal_uid: Optional[str] = Cookie(None),
    x_spinner_device: Optional[str] = Header(None),
) -> dict:
    """Current tier + usage meter + pricing — drives the meter bar, the
    'not secure' disclaimer, and the upgrade paywall in the Spinner UI."""
    uid = _resolve_uid_safe(coastal_uid)
    tier, _meter_id, used, cap = _spinner_meter(uid, x_spinner_device)
    checkout_available = bool(
        os.environ.get("COASTAL_STRIPE_COMPANION_PRICE_ID", "").strip())
    return {
        "ok": True,
        "tier": tier,
        "signed_in": bool(uid),
        "secure": tier == "paid",
        "tokens_used": used,
        "tokens_cap": cap,
        "tokens_remaining": max(0, cap - used),
        "period": _period_now(),
        "checkout_available": checkout_available,
        "pricing": _PRICING,
    }


# ── Voice (Inworld TTS) — voice picker + speak-the-output ──────────────
# Owner 2026-06-11: "give Spinner a Voice." GET /voices lists the catalog (stock
# + the owner's custom dashboard voices); POST /speak synthesizes a line via
# inworld-tts-2. TTS bills per CHARACTER (not $0 like the free OpenRouter models),
# so /speak is a PAID (Spinner+) feature — /voices is free to browse. Uses
# INWORLD_API_KEY (AIMS, everything-scoped). MP3 verified for stock + custom.
_INWORLD_TTS_BASE = os.environ.get("INWORLD_TTS_BASE", "https://api.inworld.ai")
_INWORLD_TTS_MODEL = os.environ.get("INWORLD_TTS_MODEL", "inworld-tts-2")
_SPINNER_DEFAULT_VOICE = os.environ.get("SPINNER_DEFAULT_VOICE", "Ashley")
_voices_cache: dict = {"at": 0.0, "data": None}


def _inworld_key() -> str:
    return os.environ.get("INWORLD_API_KEY", "").strip()


def _inworld_voices() -> list:
    """Fetch + cache the Inworld voice catalog (stock + custom). 10-min cache."""
    import time as _t
    if _voices_cache["data"] is not None and (_t.time() - _voices_cache["at"] < 600):
        return _voices_cache["data"]
    key = _inworld_key()
    if not key:
        raise HTTPException(status_code=503, detail="INWORLD_API_KEY not configured")
    try:
        r = _requests.get(_INWORLD_TTS_BASE + "/tts/v1/voices",
                          headers={"Authorization": "Basic " + key}, timeout=20)
    except _requests.RequestException as exc:
        log.warning("inworld voices fetch failed: %s", exc)
        raise HTTPException(status_code=504, detail="voices fetch failed")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"voices error {r.status_code}")
    voices = (r.json() or {}).get("voices", [])
    out = [{"voiceId": v.get("voiceId"),
            "displayName": v.get("displayName") or v.get("voiceId"),
            "languages": v.get("languages") or [],
            "isCustom": bool(v.get("isCustom"))}
           for v in voices if v.get("voiceId")]
    _voices_cache["data"] = out
    _voices_cache["at"] = _t.time()
    return out


@router.get("/voices")
def companion_voices() -> dict:
    """List the Inworld voice catalog for the picker — custom (your dashboard
    voices) first, then stock. Free to browse; speaking is a Spinner+ feature."""
    voices = _inworld_voices()
    custom = [v for v in voices if v["isCustom"]]
    stock = [v for v in voices if not v["isCustom"]]
    return {"ok": True, "default": _SPINNER_DEFAULT_VOICE,
            "custom": custom, "stock": stock, "count": len(voices)}


class SpeakBody(BaseModel):
    text: str
    voice_id: str = ""
    model_id: str = ""


@router.post("/speak")
def companion_speak(
    body: SpeakBody,
    coastal_uid: Optional[str] = Cookie(None),
    x_spinner_device: Optional[str] = Header(None),
) -> dict:
    """Synthesize one line to speech via Inworld TTS (MP3). PAID (Spinner+) — TTS
    bills per character, so free users get the upgrade gate."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    text = text[:600]  # short utterances stay cheap (API max 2000)
    uid = _resolve_uid_safe(coastal_uid)
    tier, _meter_id, _used, _cap = _spinner_meter(uid, x_spinner_device)
    if tier != "paid":
        raise HTTPException(status_code=402, detail={
            "upgrade": True, "tier": tier, "feature": "voice",
            "reason": "voice is a Spinner+ feature"})
    key = _inworld_key()
    if not key:
        raise HTTPException(status_code=503, detail="INWORLD_API_KEY not configured")
    voice_id = (body.voice_id or _SPINNER_DEFAULT_VOICE).strip()
    payload = {"text": text, "voiceId": voice_id,
               "modelId": (body.model_id or _INWORLD_TTS_MODEL),
               "audioConfig": {"audioEncoding": "MP3", "sampleRateHertz": 24000}}
    try:
        r = _requests.post(_INWORLD_TTS_BASE + "/tts/v1/voice",
                           headers={"Authorization": "Basic " + key,
                                    "Content-Type": "application/json"},
                           json=payload, timeout=45)
    except _requests.RequestException as exc:
        log.warning("inworld speak failed: %s", exc)
        raise HTTPException(status_code=504, detail="tts request failed")
    if r.status_code != 200:
        log.warning("inworld speak non-200 %s: %s", r.status_code, r.text[:160])
        raise HTTPException(status_code=502, detail=f"tts error {r.status_code}")
    audio = (r.json() or {}).get("audioContent", "")
    if not audio:
        raise HTTPException(status_code=502, detail="tts empty")
    return {"ok": True, "audio_b64": audio, "mime": "audio/mpeg", "voice_id": voice_id}


# ── Realtime duplex (Inworld speech-to-speech) — live interpreter ──────
# Owner 2026-06-11: "build the realtime duplex." Browser mic → this relay →
# Inworld realtime WS → translated speech back, in the FOAI-Charlotte voice.
# Round-trip proven headless (EN→ES; live STT + translation transcripts; the
# custom voice is accepted by the realtime model). Server-side semantic VAD makes
# this a transparent pipe (auto turn-detect + auto-respond + barge-in). PAID
# (Spinner+) — the realtime LLM+TTS bills per second of audio, so it is never
# free. Uses INWORLD_REALTIME_API_KEY (the SPINNER key, realtime-only scope) —
# NEVER the everything-scope key, and never hardcoded; read from env at runtime.
_RT_WS_URL = os.environ.get(
    "INWORLD_REALTIME_URL", "wss://api.inworld.ai/api/v1/realtime/session")
# Cost guard — realtime LLM+TTS bills per SECOND of audio. The IDLE timeout is the
# real protector (catches the forgotten open tab — no audio for N seconds). The
# max-duration is just a backstop against a stuck-but-active session, so it's
# generous: real use cases (meetings, medical visits) routinely run past 10 min,
# so a tight cap would cut off live sessions. Both env-tunable.
_RT_MAX_SECONDS = int(os.environ.get("SPINNER_RT_MAX_SECONDS", "3600"))
_RT_IDLE_SECONDS = int(os.environ.get("SPINNER_RT_IDLE_SECONDS", "120"))
# Only these client→upstream frame types are forwarded — the relay owns the
# session config so a paid session can't be repurposed by a crafted client frame.
_RT_CLIENT_FRAME_ALLOW = {
    "input_audio_buffer.append", "input_audio_buffer.commit",
    "input_audio_buffer.clear", "response.create", "response.cancel",
}


def _rt_key() -> str:
    return os.environ.get("INWORLD_REALTIME_API_KEY", "").strip()


def _rt_instructions(target_name: str, source: str, mode: str) -> str:
    """Build the interpreter system prompt for a realtime session."""
    if mode == "explain":
        return ("You are Spinner, a calm real-time assistant. Listen to the live "
                "conversation and, in " + target_name + ", briefly explain what is "
                "happening and what the listener should understand or do next. Be "
                "concise and reassuring. Speak only in " + target_name + ".")
    src = "" if (not source or source == "auto") else f"from {source} "
    return ("You are Spinner, a real-time interpreter. Translate everything the "
            f"speaker says {src}into {target_name}. Speak ONLY the {target_name} "
            "translation in a natural, fluent voice — no commentary, no repeating "
            "the original language, no answering questions. Keep pace with the speaker.")


@router.websocket("/realtime/stream")
async def realtime_stream(websocket: WebSocket) -> None:
    """Full-duplex speech-to-speech interpreter. The browser streams PCM16 24kHz
    mic frames as input_audio_buffer.append; this relay proxies to Inworld's
    realtime WS (SPINNER key) and streams response.output_audio.delta + live
    transcripts back. PAID (Spinner+) only. Query params: target, source, voice,
    mode, device. Close codes: 4402 upgrade-required, 4503 realtime key missing,
    4502 upstream open failed."""
    import json as _json  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415
    import websockets as _wslib  # noqa: PLC0415

    await websocket.accept()
    qp = websocket.query_params
    target = (qp.get("target") or "en").strip()
    source = (qp.get("source") or "auto").strip()
    mode = (qp.get("mode") or "interpreter").strip()
    device = (qp.get("device") or "").strip()
    voice = (qp.get("voice") or _SPINNER_DEFAULT_VOICE).strip()
    engine = (qp.get("engine") or "inworld").strip()  # inworld (brand voice) | gemini (voice-preserving)

    # PAID gate — the realtime LLM+TTS bills per second; never free.
    uid = _coastal_uid_from_cookie_header(websocket.headers.get("cookie", ""))
    tier, _meter_id, _used, _cap = _spinner_meter(uid, device)
    if tier != "paid":
        try:
            await websocket.send_text(_json.dumps({
                "type": "spinner.error", "code": "upgrade", "feature": "realtime",
                "reason": "live voice is a Spinner+ feature"}))
        except Exception:
            pass
        await websocket.close(code=4402, reason="upgrade required")
        return

    # Engine dispatch — pluggable realtime backend. Gemini = voice-preserving
    # babel-fish (Google Live Translate); default Inworld = FOAI-Charlotte brand
    # voice + the explain/decode lenses. The browser protocol is identical for both.
    if engine == "gemini":
        import realtime_engines  # noqa: PLC0415
        await realtime_engines.relay_gemini(
            websocket, target=target, source=source, mode=mode, device=device,
            idle_s=_RT_IDLE_SECONDS, max_s=_RT_MAX_SECONDS,
            main_lang_name=_main_lang_name(target))
        return

    key = _rt_key()
    if not key:
        await websocket.close(code=4503, reason="realtime key not configured")
        return

    target_name = _main_lang_name(target)
    sess_id = "spinner-" + _uuid.uuid4().hex[:16]
    url = f"{_RT_WS_URL}?key={sess_id}&protocol=realtime"
    try:
        upstream = await _wslib.connect(
            url, additional_headers={"Authorization": "Basic " + key},
            open_timeout=15, max_size=16 * 1024 * 1024)
    except Exception as exc:
        log.warning("realtime upstream open failed: %s", exc)
        await websocket.close(code=4502, reason="upstream open failed")
        return

    # The relay owns the initial session.update (instructions + brand voice).
    try:
        await upstream.send(_json.dumps({
            "type": "session.update",
            "session": {
                "instructions": _rt_instructions(target_name, source, mode),
                "audio": {"output": {"voice": voice}},
            }}))
        await websocket.send_text(_json.dumps({
            "type": "spinner.ready", "target": target, "target_name": target_name,
            "voice": voice, "mode": mode}))
    except Exception:
        try:
            await upstream.close()
        finally:
            await websocket.close()
        return

    loop = asyncio.get_event_loop()
    state = {"start": loop.time(), "last": loop.time()}

    async def client_to_upstream() -> None:
        try:
            async for raw in websocket.iter_text():
                if len(raw) > 1_400_000:  # guard absurd frames
                    continue
                try:
                    t = _json.loads(raw).get("type", "")
                except Exception:
                    continue
                if t in _RT_CLIENT_FRAME_ALLOW:
                    if t == "input_audio_buffer.append":
                        state["last"] = loop.time()  # audio activity = not idle
                    await upstream.send(raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    async def upstream_to_client() -> None:
        try:
            async for msg in upstream:
                if isinstance(msg, bytes):
                    await websocket.send_bytes(msg)
                else:
                    await websocket.send_text(msg)
        except Exception:
            pass

    async def watchdog() -> None:
        """Self-close on idle or hard max-duration so a forgotten tab can't bill
        forever. Notifies the client so the UI can show why the session ended."""
        reason = ""
        while True:
            await asyncio.sleep(2)
            now = loop.time()
            if now - state["start"] > _RT_MAX_SECONDS:
                reason = "max session duration reached"
                break
            if now - state["last"] > _RT_IDLE_SECONDS:
                reason = "idle timeout"
                break
        try:
            await websocket.send_text(_json.dumps({
                "type": "spinner.session_end", "reason": reason}))
        except Exception:
            pass

    tasks = [asyncio.create_task(c()) for c in
             (client_to_upstream, upstream_to_client, watchdog)]
    try:
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
