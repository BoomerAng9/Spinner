"""Realtime engine adapters for the Spinner relay — the wrap-the-next-model pattern.

The relay speaks ONE normalized browser-facing protocol (client sends
`input_audio_buffer.append {audio:<b64 PCM16 24kHz>}`; server sends
`response.output_audio.delta {delta:<b64 PCM16 24kHz>}` + transcript events).
Each realtime ENGINE is an adapter that bridges that normalized protocol to the
provider's native one. Adding a new model (OpenAI realtime, the next Gemini, …)
= add one `relay_*` adapter here. The frontend never changes.

Engines:
  - inworld : the proven Inworld realtime duplex (lives in spinner_service.py).
              STT → LLM → Inworld TTS; speaks in a chosen BRAND voice (FOAI-Charlotte).
  - gemini  : Google `gemini-3.5-live-translate-preview` via the Gemini Live API.
              End-to-end audio→audio; VOICE-PRESERVING (no selectable brand voice).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from array import array

log = logging.getLogger("spinner.engines")

GEMINI_LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.5-live-translate-preview")


def _gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def configured_gemini() -> bool:
    return bool(_gemini_key())


def _pcm_peak(pcm: bytes) -> int:
    """Peak absolute PCM16 sample — cheap silence detector (C-speed max/min)."""
    if len(pcm) < 2:
        return 0
    a = array("h")
    a.frombytes(pcm[:len(pcm) // 2 * 2])
    if not a:
        return 0
    return max(max(a), -min(a))


# Gemini Live Translate is a CONTINUOUS stream: after the real translation it keeps
# emitting silence/comfort frames forever (turn_complete never fires). We drop
# near-silent output so the browser doesn't schedule endless silent buffers and we
# don't relay dead air. Threshold is well below speech, above codec noise.
_SILENCE_PEAK = int(os.environ.get("SPINNER_GEMINI_SILENCE_PEAK", "350"))


def _resample_24k_to_16k(pcm: bytes) -> bytes:
    """Linear-resample PCM16 mono 24kHz → 16kHz (ratio 2/3). Gemini Live wants
    16kHz in; the browser/relay protocol is 24kHz. stdlib only (audioop was
    removed in Python 3.13)."""
    src = array("h")
    src.frombytes(pcm)
    n = len(src)
    if n == 0:
        return b""
    out_n = (n * 2) // 3
    if out_n == 0:
        return b""
    out = array("h", bytes(2 * out_n))
    for i in range(out_n):
        pos = i * 1.5
        i0 = int(pos)
        frac = pos - i0
        s0 = src[i0]
        s1 = src[i0 + 1] if i0 + 1 < n else s0
        out[i] = int(s0 + (s1 - s0) * frac)
    return out.tobytes()


async def relay_gemini(websocket, *, target, source, mode, device,
                       idle_s, max_s, main_lang_name):
    """Bridge browser ↔ Gemini 3.5 Live Translate. `websocket` is already
    accepted + paid-gated by the caller (spinner_service.realtime_stream)."""
    from google import genai  # noqa: PLC0415
    from google.genai import types as gtypes  # noqa: PLC0415

    key = _gemini_key()
    if not key:
        try:
            await websocket.send_text(json.dumps({
                "type": "spinner.error", "code": "engine_unconfigured",
                "feature": "realtime", "reason": "Gemini live engine not configured"}))
        finally:
            await websocket.close(code=4503)
        return

    client = genai.Client(api_key=key, http_options={"api_version": "v1alpha"})
    config = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "translation_config": {"target_language_code": target, "echo_target_language": True},
    }
    loop = asyncio.get_event_loop()
    state = {"start": loop.time(), "last": loop.time(), "in_buf": "", "resp_open": False}

    try:
        async with client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=config) as session:
            await websocket.send_text(json.dumps({
                "type": "spinner.ready", "engine": "gemini", "target": target,
                "target_name": main_lang_name, "voice": "(speaker-preserved)", "mode": mode}))

            async def client_to_gemini():
                try:
                    async for raw in websocket.iter_text():
                        if len(raw) > 1_400_000:
                            continue
                        try:
                            d = json.loads(raw)
                        except Exception:
                            continue
                        mtype = d.get("type")
                        if mtype == "input_audio_buffer.append":
                            b = d.get("audio") or ""
                            if not b:
                                continue
                            pcm16 = _resample_24k_to_16k(base64.b64decode(b))
                            if not pcm16:
                                continue
                            state["last"] = loop.time()
                            await session.send_realtime_input(
                                audio=gtypes.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))
                        elif mtype in ("input_audio_buffer.commit", "input_audio_buffer.end"):
                            # signal end-of-input so the model finalizes the turn
                            # (fire this when the mic stops) instead of running on.
                            try:
                                await session.send_realtime_input(audio_stream_end=True)
                            except Exception:
                                pass
                except Exception:
                    pass

            async def _open_response():
                if not state["resp_open"]:
                    state["resp_open"] = True
                    await websocket.send_text(json.dumps({"type": "response.created"}))

            async def gemini_to_client():
                try:
                    async for resp in session.receive():
                        sc = getattr(resp, "server_content", None)
                        if not sc:
                            continue
                        it = getattr(sc, "input_transcription", None)
                        if it and getattr(it, "text", None):
                            state["in_buf"] += it.text
                            await websocket.send_text(json.dumps({
                                "type": "conversation.item.input_audio_transcription.delta",
                                "delta": state["in_buf"]}))
                        ot = getattr(sc, "output_transcription", None)
                        if ot and getattr(ot, "text", None):
                            await _open_response()
                            await websocket.send_text(json.dumps({
                                "type": "response.output_audio_transcript.delta", "delta": ot.text}))
                        mt = getattr(sc, "model_turn", None)
                        if mt and getattr(mt, "parts", None):
                            for part in mt.parts:
                                idata = getattr(part, "inline_data", None)
                                if idata and getattr(idata, "data", None):
                                    if _pcm_peak(idata.data) < _SILENCE_PEAK:
                                        continue  # drop continuous silence frames
                                    await _open_response()
                                    await websocket.send_text(json.dumps({
                                        "type": "response.output_audio.delta",
                                        "delta": base64.b64encode(idata.data).decode()}))
                        if getattr(sc, "turn_complete", False):
                            if state["in_buf"]:
                                await websocket.send_text(json.dumps({
                                    "type": "conversation.item.input_audio_transcription.completed",
                                    "transcript": state["in_buf"]}))
                                state["in_buf"] = ""
                            state["resp_open"] = False
                            await websocket.send_text(json.dumps({"type": "response.done"}))
                except Exception:
                    pass

            async def watchdog():
                reason = ""
                while True:
                    await asyncio.sleep(2)
                    now = loop.time()
                    if now - state["start"] > max_s:
                        reason = "max session duration reached"; break
                    if now - state["last"] > idle_s:
                        reason = "idle timeout"; break
                try:
                    await websocket.send_text(json.dumps({"type": "spinner.session_end", "reason": reason}))
                except Exception:
                    pass

            tasks = [asyncio.create_task(c()) for c in (client_to_gemini, gemini_to_client, watchdog)]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception as exc:
        log.warning("gemini relay error: %s", exc)
        try:
            await websocket.send_text(json.dumps({
                "type": "spinner.error", "code": "engine_error", "reason": str(exc)[:200]}))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
