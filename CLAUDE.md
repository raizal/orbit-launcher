# Orbit — watch launcher + voice-note diary

Owner: Raiz (senior fullstack: TypeScript, Python, Kotlin is newer ground — explain Android-specific choices briefly).

## What we are building

**Orbit** is ONE Android app (one APK) with two built-in features, plus a companion server:

1. **Audio diary** (feature module `:feature-voice`) — record voice notes in one press, name them by timestamp, queue them, upload to the companion. Part of Orbit, reached through Orbit's own shortcuts (BACK on the face, face mic slot, center of the app grid, app shortcut). **Build this first** — the first Orbit release can be the diary alone, before Orbit is set as the HOME launcher.
2. **Orbit Notes companion** (Python) — receives notes, transcribes them, writes one Markdown diary page per day (Obsidian vault).
3. **Launcher** (feature module `:feature-launcher`) — home screen for the round screen, JSON watch faces. **Build after 1 + 2 work end to end.**

Crash safety: the recording service runs in its **own process** (`android:process=":voice"` on the Service) so a launcher crash or restart never stops a recording in progress.

Read in this order: `docs/PLAN.md` → `docs/voice-notes.md` → `docs/companion.md` → `docs/launcher.md`.
Visual reference: `design/` (HTML mockups; see `design/README.md`).

## Target device — facts, verified by owner

- "H10" Chinese smartwatch, **full Android 8.1 (API 27)**, NOT Wear OS. No Google Play Services assumed.
- Round **360 × 360 px**, 1.39″, **IPS** (not AMOLED — black does not save power), ~259 ppi.
- SoC Spreadtrum/Unisoc SC8541E (slow), 2 GB RAM, 16 GB storage.
- Radios: LTE, Wi-Fi, Bluetooth, GPS (inaccurate — city-level only).
- Heart-rate sensor exists but is junk — **do not use it**.
- Inputs: touch, **physical BACK button** (`KEYCODE_F9`; system also injects `KEYCODE_BACK` on short press), **POWER button** (home/lock, owned by system — we don't use it), **rotary control** (a *sensor*, type 36 `rotary_encoder`, not input events). Details: `docs/device-facts.md`.
- Inside apps the vendor forces a compat density (0.75); provide our own Compose `LocalDensity` = screen width / 400 so 1 dp = 1 design px.
- Record audio as **ADTS `.aac`** (survives power loss; `.m4a` does not).
- Has a camera (use for QR pairing). Probably no flash LED.
- Stock launcher can be replaced as default HOME. System pull-down status shade exists and cannot be disabled by us.
- Connected to the dev machine via adb (4-pin cable).

## Tech choices (do not change without asking)

- Android: Kotlin, **minSdk 27**, Gradle Kotlin DSL, version catalog. Modules: `:app` (the single Orbit APK, package `com.orbit`), `:feature-voice`, `:feature-launcher` (later), `:core` (theme, fonts, round-screen helpers, shared models).
- Voice app UI: Jetpack Compose is fine; keep it light.
- Background work: **foreground Service** for recording, **WorkManager** for uploads.
- HTTP: OkHttp. JSON: kotlinx.serialization. LAN discovery: `NsdManager` (mDNS). QR: ZXing (`zxing-android-embedded`) — **no ML Kit / GMS**.
- Companion: Python 3.11+, FastAPI + uvicorn, `zeroconf`, `faster-whisper`, `qrcode`, Typer CLI. Package manager: uv.

## Rules for the agent

- Work in small, testable steps. After each step: build, install on the watch (`./gradlew :app:installDebug`), and tell Raiz exactly what to test by hand.
- Judge performance only on **release builds with R8** — Compose debug builds are much slower on this chip.
- Never delete an unsent recording. Data loss is the worst possible bug in this project.
- Before adding a dependency, check it supports minSdk 27 and does not need Google Play Services.
- Design grid in `design/` is 400 px; real screen is 360 px → multiply design px by **0.9**. In code, wrap UI in our own `LocalDensity` (screen width / 400) so **1 dp = 1 design px** — do not trust `adb shell wm density` (apps get a different, vendor-forced density; see `docs/device-facts.md`).
- Tap targets ≥ 64 px on the real screen. Text must not sit in the corners of the square — keep dense text inside the inscribed square (~255 px centered).
- When something in the docs is ambiguous or contradicts the device, stop and ask; do not guess silently.
- Open decisions are listed in `docs/PLAN.md` → ask Raiz when you reach them.
