# SPINNER — Wearable & Edge Architecture
*Sqwaadrun / Lil_Edge_Hawk · grounded against current 2026 sources · 2026-06-11*

> **Honesty contract.** Claims tagged **VERIFIED** (confirmed from public 2026 sources), **ASSUMED** (engineering inference), or **NEEDS REVIEW** (partnership/contract/test-gated). Spinner has **no** signed relationship with Apple, Google, Meta, Brilliant Labs, Even Realities, or XREAL as of this writing.

## 0 · Mental model: phone is the brain, wearable is the surface
The load-bearing decision: **the phone (or its companion app) is the compute hub; the wearable is an I/O peripheral.** No mainstream watch or glasses in 2026 runs Spinner's audio models locally, and none lets an arbitrary web page stream their mic in the background. The wearable's job is narrow and high-value: **capture audio at the edge, render the lens output (caption / haptic / whisper-audio), accept one tap.** VAD, model routing, and Inworld voice live on the phone/cloud. This keeps **Spinner's existing PWA + API as the core**; wearables are progressively-enhanced surfaces.

## 1 · Smart watch (Apple Watch / watchOS · WearOS)
**Value:** glanceable captions/translations on the wrist, a **haptic tap** the instant Decode flags a "watch-this" moment (a number, a commitment, a name, a contradiction), tap-to-talk, on-wrist mic.

| Capability | PWA today | Native watchOS companion | Native WearOS app |
|---|---|---|---|
| Glanceable caption on wrist | ❌ | ✅ WatchConnectivity relay from iPhone | ✅ Data Layer / phone relay |
| Haptic alert on Decode flag | ❌ | ✅ `WKInterfaceDevice.play(.notification)` | ✅ Vibrator / ongoing-activity |
| Tap-to-talk | ❌ | ✅ | ✅ |
| On-wrist mic capture | ❌ | ⚠️ short foreground dictation only | ✅ records, streams to phone |
| Background while screen off | ❌ | ⚠️ constrained (Live Activity) | ⚠️ constrained (ongoing notification) |

- **VERIFIED:** **watchOS 26 ships wrist Live Captions** (Live Listen) of what the iPhone hears — the Apple-blessed template Spinner mirrors with its own models + lenses.
- **VERIFIED:** the Apple Watch is **not** a usable always-on third-party mic; watch mic feeds Apple's own features. ⇒ **iPhone is the primary mic; watch = caption + haptic + tap.**
- **VERIFIED:** an **iOS PWA cannot reach the watch at all** (no background, no background audio, no watch target). ⇒ wrist UX is strictly a **native-companion** feature — do not promise it on the PWA tier.
- WearOS additionally **permits on-watch mic capture streamed to the phone** ⇒ the WearOS build can offer a wrist-mic mode the watchOS build cannot *(VERIFIED capability; ASSUMED API ergonomics)*.

**Recommended:** `iPhone companion (AVAudioEngine capture + Silero VAD + model routing + Inworld) → WatchConnectivity → Watch (caption view + Decode haptic + tap-to-talk)`.

## 2 · Smart glasses (Ray-Ban Display Meta · Brilliant Labs Frame · Even Realities · XREAL)
**Value:** heads-up **live captions in the lens** ("glance, don't stare"), **bone-conduction/whisper audio** of the translation/explanation, and a **camera feed for the Vision lens** (read this menu/sign/document).

| Platform | Lens display | Audio | Camera (Vision) | Dev access | Spinner fit |
|---|---|---|---|---|---|
| **Ray-Ban Display (Meta)** | ✅ monocular | ✅ open-ear | ✅ | ✅ Wearables Device Access Toolkit (May 2026): native SDK **+ web apps** | **Best closed-platform fit** (review-gated) |
| **Brilliant Labs Frame** | ✅ monocular | ❌ no speaker | ✅ | ✅ **fully open** (Flutter/Python/Lua) | **Best open fit — build today** |
| **Even Realities G1/G2** | ✅ green micro-LED text | ❌ none | ❌ none | companion + community BLE | **Caption-only** (no whisper, no Vision) |
| **XREAL** | ✅ large birdbath | ✅ | varies | tethered + SDK | "Theater caption" mode |

**Audio path (VERIFIED pattern):** glasses are a Bluetooth peripheral; the phone does the compute. `mic → phone (VAD → audio model → lens) → caption via BLE to lens HUD; whisper via BT audio (Meta speaker, or phone-paired earbuds for Frame/Even); Vision = glasses camera frame → phone → multimodal model`.

- **VERIFIED — Meta opened up (May 2026):** Wearables Device Access Toolkit gives third parties a native iOS/Android SDK **and** web apps that push to the monocular display; first-party live captioning already proves the HUD pattern. **NEEDS REVIEW:** developer-preview, review/approval-gated — *"coming to Ray-Ban Display, pending approval,"* never "available."
- **VERIFIED — Brilliant Labs Frame is genuinely open:** open-source hardware/software, official Flutter package (iOS+Android), Python lib, Lua-over-BLE; the open Noa assistant already does translate-what-you-see-and-hear. **Lowest-friction real integration → the hero device for a credible demo.**
- **VERIFIED — Even Realities G1/G2 have no speaker and no camera:** display-only text HUD ⇒ Spinner is **caption-only** there (no whisper, no Vision).
- **NEEDS REVIEW — Web Bluetooth:** absent in **all** iOS Safari; Chromium-only (Android/desktop). ⇒ a pure-PWA "connect my glasses over Web Bluetooth" experience is **Android-Chrome-only, nonexistent on iPhone.** Real glasses integration = **native companion app.**

## 3 · Edge / on-device — latency budget AND the privacy story
| Stage | Where | Why | Tag |
|---|---|---|---|
| VAD (is anyone speaking?) | **On-device** (Silero, ~2 MB, <1 ms/30 ms frame) | gate the mic; cut 40–60% compute on silence; never stream silence | VERIFIED |
| Wake word ("Hey Spinner") | **On-device** (Picovoice/Vosk + Silero) | hands-free without an always-open cloud mic | VERIFIED |
| Short STT / private captions | **On-device** (WhisperKit/Core ML iOS; Android STT) | first-word 780 ms → **140 ms**; nothing leaves device | VERIFIED |
| Lens reasoning (Decode/Explain/Research/Ideate) | **Phone → cloud** multimodal | needs the big models | ASSUMED |
| Realtime voice (Inworld) | **Cloud** | existing Spinner path | VERIFIED |
| Local cache (recent captions, last result) | **On-device** | instant glance-back; survives a dropped link | ASSUMED |

**Latency target (caption path):** VAD endpoint <20 ms → STT first word ~140 ms (on-device) → lens model 300–900 ms (cloud, +50–500 ms network) → caption to lens <50 ms. ⇒ **Translate/Summarize feel near-instant (local STT); Decode/Research take a ~1–1.5 s "thinking" beat — fine, it's real reasoning.**

**Privacy ladder (a product tier, not a footnote):**
- **Free = cloud.** Cheap multimodal models, labeled "not private." Honest, already shipped.
- **Edge/Private (Spinner+) = on-device VAD + STT.** Raw audio **never leaves the device**; only minimal derived text goes up for heavy lenses (opt-in per lens). **NEEDS REVIEW:** never market "fully private" while any lens calls the cloud — the accurate claim is **"on-device capture; you choose what leaves the device."**

## 4 · Phased build path
- **Phase 1 — PWA + phone-in-mount + Android Web Bluetooth (today).** Ships: wrist glance-mirror via Live Activities/notifications from a thin companion, "open on the table" mode, Android-Chrome-only Frame caption spike. Effort: low (days–2 wks). **Demo-grade, not the real wearable experience — don't oversell.** *(VERIFIED constraints.)*
- **Phase 2 — Native companion app (the real unlock).** iOS + Android companion = the Spinner brain (continuous capture, **on-device Silero VAD + WhisperKit/Android STT = the edge tier**, model routing, Inworld voice) + watchOS app (wrist captions + Decode haptic + tap-to-talk) + WearOS app (+ wrist-mic) + **Brilliant Labs Frame via the official Flutter package** (in-lens captions + Vision via Frame camera, whisper to earbuds). **90% of the promised value lives here.** Effort: medium–high (~6–10 wks v1) *(ASSUMED estimate)*.
- **Phase 3 — Native SDK + closed-platform integrations.** **Ray-Ban Display** via the Meta toolkit (in-lens overlays + open-ear whisper + camera Vision, web or native micro-app); deeper watchOS Live Activities; Even Realities **caption-only**; optional XREAL theater mode. Effort: high + **external-approval-gated**. **Do not announce any closed-platform integration until the app is accepted and tested.**

## 5 · Verification ledger
- **VERIFIED:** watchOS 26 wrist Live Captions; Watch not a third-party always-on mic; iOS PWA has no background audio/watch target/Web Bluetooth; Silero VAD perf; WhisperKit 140 ms first word; Meta Wearables Device Access Toolkit (SDK + web apps, May 2026); Brilliant Labs Frame open SDK + Noa translate; Even Realities G1/G2 no speaker/no camera; Web Bluetooth absent on all iOS Safari.
- **ASSUMED:** integration effort; BLE caption-render latency; on-device cache design; models too large for fully-local lens reasoning.
- **NEEDS REVIEW / gated:** Spinner's admission to Meta's toolkit program; any "fully private" claim spanning cloud lenses; XREAL specifics; WearOS wrist-mic API ergonomics.

### Sources
Road to VR / Engadget / gHacks (Meta Wearables Device Access Toolkit, May 2026) · Brilliant Labs Frame SDK docs + GitHub · TechRadar (Even Realities G1) + Even Realities G2 · Apple Support (watch Live Listen/Live Captions) + MacRumors (watchOS 26) + Apple Developer (watch audio recording) · MagicBell (PWA iOS limitations 2026) · Picovoice (VAD 2026; iOS speech recognition / WhisperKit) · Zenn (on-device wake word) · caniuse + MDN (Web Bluetooth).
