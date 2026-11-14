# Device facts — H10 (Phase 0)

Collected 2026-11-13 over adb (`getprop`, `dumpsys`, `getevent`). Items marked **PENDING** need the Phase 0 probe app.

## Identity & hardware

| | |
|---|---|
| Model / brand | `H10` / `hesc`, product `sl8541e_1h10_oversea` |
| Build | `K1A_V32U1O_HM_H10_EN_ZX_HSC_4.4V900_20241024`, Android **8.1.0, API 27**, patch 2019-11-05 |
| SoC | Spreadtrum **SC9832E** (platform `sp9832e`; SL8541E board) — 4 × Cortex-A53 @ 1.4 GHz, arm64-v8a (+ armeabi-v7a) |
| RAM | **1.8 GB** total (`MemTotal 1840048 kB`), zram on, ~1.3 GB available at idle |
| Storage | `/data` 11 GB, 6.4 GB free |
| Heap | `heapgrowthlimit 192m`, `heapsize 512m` (largeHeap) |
| Display | **360 × 360**, panel `gc9b71_mipi_boe1389` |
| Density | system: physical 150, override 180 (`display_density_forced=180`). **But apps get something else** — see below. |
| Time zone | `Asia/Jakarta` (+07:00) |
| Screen | timeout 15 s, brightness 120/255 |

### Density inside our app (measured)

The system config says 180 dpi / 320 dp, but every app (ours, the stock launcher, Music) runs in a vendor compat mode (`compat={150dpi always-compat}`, prop `ro.hsc.newscale=true`). Our activity's `DisplayMetrics` reported **density 0.75 (120 dpi), 360 × 360 px = 480 × 480 dp**, `isScreenRound=true`.

→ `wm density` is not the number to use, and dp values from the design would come out far too small.

### Design → code conversion

Don't convert design px to dp by hand. In Compose, provide our own `LocalDensity` so that **1 dp = 1 design px**: `Density(density = screenWidthPx / 400f, fontScale = 1f)` (= 0.9 on this watch). Then the 400-unit design and face JSON numbers can be used as-is, and the layout stays right if the vendor scale changes.
Checks (design px): 64 real px tap target = 71 design px. Inscribed square 255 real px ≈ 283 design px.

## Inputs

All `/dev/input/event*` nodes are mode `rw-rw-rw-` (world-readable).

| Physical | Kernel device | Raw event | Android sees |
|---|---|---|---|
| **BACK** button (confirmed by Raiz) | `sprd-gpio-keys` (event5) | `KEY_F9` | App receives **`KEYCODE_F9`** (scan 67). On a **short press** the vendor framework also injects a synthetic **`KEYCODE_BACK` DOWN+UP** (deviceId −1) before F9 UP — so normal back handling just works. On a **long press** F9 repeats (`isLongPress=true` at repeat 1) and **no BACK is injected** → long-press BACK is free for us to use. |
| **Home / lock** button (confirmed by Raiz) | `sprd-gpio-keys` (event5) | `KEY_POWER` | WindowManager handles it as POWER (vendor hook `goBack mPowerKeyHandled` = "go home" on short press; also locks / screen off). **Owned by the system — Orbit does not use it.** |
| Rotary | `rotary@0` (event0) | `EV_REL REL_X` with value **5 or 9** = direction code, not a delta (~1 event per detent, bursts at 20–100 ms) | **Not an Android input device** (no `.idc`, ignored by EventHub). Exposed instead as **sensor type 36 `android.sensor.rotary_encoder`** ("HSC encoder", 5–200 Hz). A vendor framework class `android.app.EventEncoderHelper` registers that sensor for the foreground app (seen for launcher, Play Store, system). App reads it with `SensorManager`: **one `SensorEvent` per detent, `values[0]` = 9.0 or 5.0** (the raw code). **9 = clockwise, 5 = counter-clockwise** (tentative: from Raiz's "3 CW then 3 CCW" capture — confirm by hand once). **No `MotionEvent`/scroll and no key events reach our app** — we must listen to the sensor ourselves (register in `onResume`, unregister in `onPause`). |
| Touch | `Betterlife_ts` (event8) | MT, 0–360 | Normal. Driver also declares gesture keys (W/E/U/O/…, BACK, MENU, APPSELECT) — unused. |
| Wrist/other keys | `sc7a22_input_key` (event3) | `KEY_F6`, `KEY_F10`, `KEY_POWER` | Probably raise-to-wake (`ro.config.raise_wakeup_timeout=3000`). Not seen in capture. |

Vibration for rotary detents: `ro.config.encoder_vibrate_time=60` (ms) — the system already buzzes per detent in some contexts.

## Sensors

| Sensor | Type | Notes |
|---|---|---|
| ST 3-axis accelerometer | 1 | 0.5–100 Hz |
| Light | 5 | |
| Proximity | 8 | wake-up |
| bd1820 heart rate | 21 | junk — do not use |
| HSC rotary encoder | 36 (`android.sensor.rotary_encoder`) | see Inputs |

**No `TYPE_STEP_COUNTER` / `TYPE_STEP_DETECTOR`.** Steps exist only as a raw kernel device `sc7a22_input_step` (event4, `ABS_X` 0–65535) and inside vendor app `com.hsc.walk`. → Face binding `steps.*` must be hidden for now (as `docs/launcher.md` already allows); reading event4 directly is a possible later option.

## Audio

Inputs: Built-In Mic, Built-In Back Mic, Wired Headset Mic, BT SCO. Mic accepts 8–48 kHz incl. **16 kHz**.

MediaRecorder AAC-LC, 16 kHz, mono, 32 kbps works in both containers (≈ 4.1 kB/s ≈ 15 MB/h). Survival tests (probe app, recording in its own `:voice` process):

| Event during recording | `.m4a` (MPEG_4) | `.aac` (AAC_ADTS) |
|---|---|---|
| Clean `stop()` | OK | OK |
| `kill -9` of the `:voice` process | **OK** — full length | **OK** — full length |
| `am force-stop com.orbit` | OK (tested m4a) | (same mechanism) |
| **Reboot** (≈ battery dies / system crash) | **LOST** — 60 kB of data, `moov atom not found`, unplayable | **OK** — 13.6 s of ~15 s playable |

Why app kills are safe: MediaRecorder actually runs inside the system media server, which notices our process died and finishes the file itself. Only a media server / system death (reboot, empty battery) loses the MP4 index.
→ **Record ADTS `.aac`.** Remaining risk with ADTS: the last ~1–2 s before a power loss.

Levels: quiet room ≈ −51 dB mean. Raiz's own speech samples: **mean −26 to −39 dB, peaks −1.5 to 0 dB** → plenty for Whisper, no gain needed; loud close speech can touch 0 dBFS (mild clipping), acceptable.

## Software environment

- **Google Play Services IS installed** (`com.google.android.gms` 24.46.31, `com.android.vending`, `gsf`). Policy stays: do not depend on it.
- Current HOME: `com.hsc.launcher3/.Launcher` (system priv-app). Other vendor apps: `com.hsc.launchers`, `com.zmapp.watch`, `com.hsc.walk`, `com.hsc.heartrate`.
- Third-party: `com.ricktop.ClockSkinCoco` holds the only notification-listener grant.
- Keyguard with face unlock (`cn.heils.faceunlock`) is active.

## Power management

- Stock doze (`deviceidle`) user whitelist is empty. Can add with `adb shell dumpsys deviceidle whitelist +com.orbit`.
- **Vendor `PowerController` (Sprd, inside system_server)** tracks per-app foreground/proc/notification/audio state (`PowerController.AppState`, `.RecogA`, `PowerControllerEx`) — this is the Sprd background-restriction engine. Settings: `savepowerdefault=1`, `custom.powersave=true`. No user-facing whitelist found yet. **PENDING:** 30-min screen-off recording survives.
- `lmkd` with `ro.lmk.vmpressurenhanced=true`.

## Network

- Wi-Fi 2.4 GHz, home SSID **"HomeWiFi"**, BSSID `aa:bb:cc:dd:ee:ff`.
- **Our app can read SSID and BSSID** (`WifiManager.getConnectionInfo()` → `"HomeWiFi"` with quotes, BSSID ok) with `ACCESS_COARSE_LOCATION` granted and location mode on. Not yet tested with location off (expect `<unknown ssid>` → fall back to gateway `192.168.1.1` + subnet).
- `NetworkCapabilities` on home Wi-Fi: `NOT_METERED`, `INTERNET`, `TRUSTED`. Network also has IPv6 (global addresses + IPv4 `192.168.1.24/24`, gateway `192.168.1.1`).
- **After a reboot Wi-Fi did not reconnect by itself** (supplicant `DISCONNECTED`, saved network is marked `HIDDEN`); toggling Wi-Fi off/on fixed it. Uploads simply wait, but worth knowing when testing.
- Watch `192.168.1.24/24`, dev PC `192.168.1.17/24`, same subnet. Ping RTT 3–177 ms (Wi-Fi power save causes spikes) → the 300 ms scan connect-timeout is tight; consider 500–800 ms or retry once.
- **No SIM inserted** (`gsm.sim.state=ABSENT`) → LTE paths cannot be tested right now; "away from home" tests must use Wi-Fi off.
- USB/adb drops and reconnects when the watch is handled (transport id changed mid-session). Long-running captures should run on-device (`nohup … &`), not over a live adb stream.

## Decisions from Phase 0 (Raiz, 2026-11-13) — to fold into launcher.md / voice-notes.md

- **F9 = BACK.** On the face: opens the app list. Elsewhere: normal back.
- **POWER = home + lock**, system-owned. Orbit does not try to use it (the spec's "HOME on face → app grid" is dropped).
- **Rotary on the face opens a side panel ("side view") containing the audio diary** — this replaces "BACK on face starts a voice note" and "rotary steps through tiles".

## Still pending

1. Confirm rotary direction by hand (is 9 really clockwise?).
2. 30-min screen-off recording **unplugged** (USB charging disables doze and probably the vendor power saver). Plugged run of 3.5 min was fine; screen off + `PARTIAL_WAKE_LOCK` held.

Testing note: the watch has a **secure lock screen**. adb tests can't unlock it; the probe activity uses `setShowWhenLocked(true)` to run over it. Raw input can be simulated without touching the watch: `sendevent /dev/input/event0 2 0 9; sendevent /dev/input/event0 0 0 0` (rotary), `sendevent /dev/input/event5 1 67 1|0` + SYN (F9 down/up).

## Found during Phase 3 (2026-11-14)

- **Notification listeners can't bind on this ROM.** Ours (and the already-approved `com.ricktop.ClockSkinCoco`) is listed as allowed, but `NotificationListeners: Unable to bind notification listener service` every time (after install, reinstall, settings toggle), with no reason logged. `cmd notification allow_listener` silently does nothing on 8.1; `settings put secure enabled_notification_listeners` marks it allowed but binding still fails. Likely the vendor auto-start restriction. → The notifications panel shows its "access is off" state. Open issue.
- **Location:** `getLastKnownLocation` is null for all providers; a single NETWORK_PROVIDER update does work (weather loads: 34°, sunrise 04:59 / sunset 17:26 for the watch's area).
- Launcher shows the stock lock screen when locked (correct). For adb checks, debug builds accept `--ez overLock true`.
- Orbit is **not** set as default HOME yet. To switch: `adb shell cmd package set-home-activity com.orbit/com.orbit.launcher.LauncherActivity`; back to stock: `adb shell cmd package set-home-activity com.hsc.launcher3/.Launcher`.
