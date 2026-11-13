# Design reference

These `.dc.html` files are the mockups from the Orbit design canvas. They are **reference only** — do not ship or port the HTML. Read them for exact layout, sizes, colors and copy.

- Round screens are drawn on a **400 × 400 px grid**. The watch is 360 × 360 → multiply px by **0.9**.
- Positions are absolute from the top-left of the 400 px square; the circle's center is (200, 200).
- Some screens compute positions in the `<script>` block (`renderVals()`), e.g. the honeycomb hex grid, curved list insets and quick-settings ring — port that math to Kotlin.
- `{{name}}` in markup is a template value from `renderVals()`.
- `Main` and `FaceSpec` / `VoiceSpec` are overview sheets, not screens.
- PNG exports (if present in `png/`) show the rendered look.

| File | Screen |
|---|---|
| FaceOrbit | Default digital face |
| FaceAnalog | Analog face |
| FacePicker | Long-press face picker |
| FaceCustomize | Change a face slot |
| LauncherHoneycomb | App grid (HOME on face) |
| LauncherList | App curved list |
| QuickSettings | Swipe right panel |
| Notifications | Swipe up |
| Tiles | Swipe left (weather) |
| VoiceRecord | Recorder (idle / recording / saved) |
| VoiceInbox | Notes and send status |
| VoiceServers | Choose / pair server |
