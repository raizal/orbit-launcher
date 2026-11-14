# Build plan

Each phase ends with a checkpoint Raiz can test by hand. Do not start the next phase until the checkpoint passes.

## Phase 0 — Device probe (≈1 hour) — ✅ done (`docs/device-facts.md`)

Goal: confirm the facts the later phases depend on.

1. Scaffold the Gradle project: `:app` (single Orbit APK), `:core`, `:feature-voice`, `:feature-launcher` (empty stub).
2. Tiny debug screen in `:app` that logs:
   - all `KeyEvent`s and `MotionEvent`s with `SOURCE_ROTARY_ENCODER` (turn the rotary both ways, press BACK, press HOME),
   - `SensorManager.getSensorList(TYPE_ALL)` (is `TYPE_STEP_COUNTER` present?),
   - `wm density` / `DisplayMetrics`,
   - microphone test: record 10 s with `MediaRecorder` (AAC, 16 kHz, mono, 32 kbps) and play it back.
3. Also ask Raiz to run `adb shell getevent -l` while turning the rotary and paste the output.

**Checkpoint:** a short `docs/device-facts.md` written from the results.

## Phase 1 — Orbit audio diary (watch, inside the Orbit app) — ✅ built; open: 10-min unplugged screen-off test, tests 4–5 by hand

Spec: `docs/voice-notes.md`. In this phase Orbit is installed as a normal app (not yet HOME). Entry: the Orbit app icon opens the recorder; a static app shortcut "New voice note" (long-press icon) starts recording.

- 1a. Recorder screen + foreground recording service (own `:voice` process) + file naming + sidecar JSON. Works with screen off. **Checkpoint:** record 3 notes (one 10 min with screen off); files appear with correct names; `adb pull` and play them.
- 1b. Local queue (Room or a simple JSON index) + inbox screen showing waiting / sending / sent.
- 1c. Upload with WorkManager to a **hard-coded** LAN IP:8765 + token. Retries, sha256 check. **Checkpoint:** turn Wi-Fi off, record, turn it on → note uploads once, no duplicates.
- 1d. Server discovery (last IP → mDNS → /24 scan, with HMAC proof check) + QR pairing + server picker screen. **Checkpoint:** fresh install, scan QR, notes recorded away from home upload after joining home Wi-Fi; still works after the PC's IP changes.

## Phase 2 — Orbit Notes companion — ✅ built (`companion/`, 26 tests); 2c/2d defaults pending decisions 2–5 below

Spec: `docs/companion.md`. Can be built in parallel with 1c (1c needs at least the `PUT` endpoint).

- 2a. FastAPI receiver: health, HEAD, PUT; storage by date; sha256 verify. Before Phase 1c.
- 2b. mDNS advertise + `orbit-notes pair` (QR in terminal).
- 2c. Transcription worker (faster-whisper).
- 2d. `orbit-notes diary <date>` → Markdown page in the vault.

**Checkpoint:** speak 3 notes during a day → next morning a diary page exists with transcripts and links to audio.

## Phase 3 — Orbit Launcher

Spec: `docs/launcher.md`. Order: face renderer (JSON → Compose Canvas) → swipe navigation → **diary shortcuts wired in (rotary on face → diary side panel, face mic slot, center of app grid)** → honeycomb app grid → quick settings → notifications → curved list → face picker / customize. Then set Orbit as the default HOME app. The diary must keep working unchanged when Orbit becomes HOME.

## Open decisions — ask Raiz when you reach them

1. ~~Where the companion runs~~ — **decided: local network only**, found by last IP → mDNS → subnet scan. Uploads only on home Wi-Fi.
2. **Diary language:** Indonesian, English, or same as spoken. Needed before 2d.
3. **Obsidian vault path** and folder for diary pages. Needed before 2d.
4. **Transcription:** local faster-whisper (which model size fits the machine?) or a hosted API. Needed before 2c.
5. **Diary LLM provider** (e.g. Claude API) and API key handling. Needed before 2d.
6. **Rotary diary side panel:** does turning the rotary on the face only open the panel, or open it *and* start recording (one-press rule)? Needed before Phase 3.
7. **Long-press BACK:** free — assign something (e.g. start recording from any Orbit screen)? Optional.
