# Orbit Launcher — spec (Phase 3)

Home launcher for the round 360 px screen. The watch face is home; everything else is one swipe or one button away.

## Navigation

| Gesture / button | From the face |
|---|---|
| Swipe down from top edge | System shade (not ours — leave it) |
| Swipe right | Quick settings (ours, round) |
| Swipe left | Tiles (weather first) |
| Swipe up | Notifications |
| Tap a face slot | Opens that slot's app / action |
| Long-press face | Face picker → Customize |
| POWER (home/lock) button | **System-owned** (short press = home / screen off, hold = lock). We don't handle it; when pressed elsewhere it brings the HOME app (us) back via `onNewIntent`. |
| BACK button (`KEYCODE_F9`) | On face: **open the app list** (honeycomb). Elsewhere: normal back (the system also injects `KEYCODE_BACK` on short press). Long press: unassigned. |
| Rotary (sensor, see `device-facts.md`) | Face: **open the side panel with the audio diary** · App list: scroll · Honeycomb: zoom · Quick settings: brightness · Customize: change data source |

Start our own panels' gestures below the top ~40 px so they never fight the system shade. The POWER button is the system's; we cannot change it.

## Round-screen rules

1. Dense text only inside the inscribed square.
2. Outer ~22 px ring is for arcs, curved text and page dots.
3. Lists curve: each row's left inset follows the circle; the focused (center) row is larger.
4. Honeycomb grid with fisheye scale toward the edge; edge icons are not tappable until scrolled to center.
5. Tap targets ≥ 64 px real.
6. IPS: dark theme for contrast, not battery. No always-on face; screen off on wrist-down is the system's.

## Screens (design files in `design/`)

FaceOrbit, FaceAnalog, FacePicker, FaceCustomize, LauncherHoneycomb, LauncherList, QuickSettings, Notifications, Tiles.

## Watch face format

A face is a JSON file of layers drawn on one Compose `Canvas`. Coordinates are on a 400-unit grid (`size`), scaled to the screen. No scripting on the watch.

```json
{
  "id": "orbit", "name": "Orbit", "size": 400,
  "theme": { "bg": "#000000", "ink": "#F2F1EC", "muted": "#9A9A94", "accent": "#FFB547" },
  "layers": [
    { "type": "ticks", "count": 60, "r": 176, "len": 5, "major": 5, "color": "muted" },
    { "type": "arc", "r": 188, "start": 135, "sweep": 270, "width": 8,
      "track": "#1E1E1C", "color": "accent", "value": "{steps.progress}" },
    { "type": "text", "text": "{date.EEE} {date.d}", "x": 200, "y": 118, "size": 20, "font": "grotesk", "color": "muted" },
    { "type": "text", "text": "{time.HH}:{time.mm}", "x": 200, "y": 196, "size": 108, "weight": 600, "font": "grotesk", "color": "ink" },
    { "type": "complication", "slot": 1, "x": 128, "y": 286, "default": "weather" },
    { "type": "complication", "slot": 2, "x": 200, "y": 286, "default": "voice_note" },
    { "type": "complication", "slot": 3, "x": 272, "y": 286, "default": "battery" }
  ],
  "tweaks": { "accent": ["#FFB547", "#5CD6E0", "#B9F26B"] }
}
```

Layer → composable: `text`→`FaceText`, `curvedText`→`FaceCurvedText`, `arc`→`FaceArc`, `ticks`→`FaceTicks`, `hands`→`FaceHands`, `complication`→`ComplicationSlot`, `image`→`FaceImage`, `group`→`FaceGroup`.
Bindings: `time.HH/mm/ss`, `date.EEE/d/MMM`, `battery.level`, `steps.count/progress` (only if `TYPE_STEP_COUNTER` exists — else hide), `weather.temp/icon`, `notif.unread`, `net.lte/wifi`.
Faces live in `/sdcard/Android/data/<pkg>/files/faces/*.face.json`, hot-reloaded on change (push with adb). Invalid file → show error face, never crash.

## Quick settings toggles (what is possible on 8.1)

Wi-Fi, Bluetooth (direct API), Do-not-disturb (notification policy access), Sound/vibrate, Location (needs `adb shell pm grant <pkg> android.permission.WRITE_SECURE_SETTINGS`), Screen light (full-white screen, max brightness). Brightness via `WRITE_SETTINGS`. No airplane or mobile-data toggle (not allowed for apps on 8.1) — show LTE status only.

## Data sources

Weather: Open-Meteo (no key), refresh every 30–60 min, coarse location. Notifications: `NotificationListenerService` (grant via `adb shell cmd notification allow_listener <component>` if the settings screen is missing). No heart rate.
