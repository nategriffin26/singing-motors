# UI Dashboard (Mac Display)

## Build frontend assets

```bash
cd ui/dashboard
npm install
npm run build
```

The build output must exist at `ui/dashboard/dist` before running `music2 run --ui`.

## Run playback with UI backend (precomputed 30fps)

```bash
music2 run assets/midi/simple4.mid --yes --ui --ui-host 127.0.0.1 --ui-port 8765 --ui-render-mode prerender-30 --ui-theme neon
```

Open `http://127.0.0.1:8765` on the external display.

The viewer is now vertical by default:
- Keyboard is anchored on the bottom edge.
- Lower notes appear on the left, higher notes on the right.
- Upcoming notes appear above and fall downward into the keyboard.

The default `--ui-render-mode` is `prerender-30`, which precomputes timeline data once and lets the browser run playback from a local 30fps clock (smoother on mobile clients).

Use `--ui-render-mode live` only if you want old live WebSocket frame behavior.

`--ui-theme` accepts:
`neon`, `retro`, `minimal`, `oceanic`, `terminal`, `sunset`, `chalkboard`, `blueprint`, `holographic`, `botanical`.

## UI-only preview (no ESP32 required)

```bash
music2 ui-preview assets/midi/simple4.mid --ui-host 0.0.0.0 --ui-port 8765
```

This serves the same viewer using precomputed timeline data without opening the serial device.

## Theme defaults

- Set the default viewer theme in `config.toml`:

```toml
[ui]
theme = "retro"
```

- You can override per run with `--ui-theme`.
- Theme is selected by config or `--ui-theme` at launch.

## Color mode defaults

- Set the default note color strategy in `config.toml`:

```toml
[ui]
color_mode = "monochrome_accent"
```

- Limit which modes are available in the dashboard switcher:

```toml
[ui]
color_modes = ["monochrome_accent", "channel", "frequency_bands"]
```

Available color modes:
`monochrome_accent`, `channel`, `octave_bands`, `frequency_bands`, `motor_slot`, `velocity_intensity`.

## Top controls visibility

Hide or show the top Theme/Color controls using config:

```toml
[ui]
show_controls = false
```

- `true` keeps controls visible (default).
- `false` runs a clean fullscreen viewer with no top control bar.

## UI sync offset tuning

Use `[ui].sync_offset_ms` in `config.toml` to apply a signed hardcoded timing offset on top of motor-reported playhead:

```toml
[ui]
sync_offset_ms = 75.0
```

- Positive values delay the UI relative to physical motors.
- Negative values advance the UI relative to physical motors.
- Start with small steps (for example `+25`, `-25`) and tune by eye.

## 9-inch display workflow

1. Connect the 9" monitor to the Mac (HDMI/USB-C).
2. Open the dashboard URL on that display.
3. Enter full screen with `Control+Command+F`.
4. Keep terminal playback control on the primary display.

## Reconnect behavior

- If the backend restarts, the UI shows a disconnected banner and retries automatically.
- On reconnect, it refetches `/api/viewer/session`; timeline playback remains local after the initial `/api/viewer/timeline` load.
- Playback is not interrupted by UI disconnects.

## Dev mode frontend (optional)

```bash
cd ui/dashboard
npm run dev
```

Vite dev server is for frontend iteration. Production playback should use the static build served by FastAPI.
