# stepper-music

`stepper-music` lets you play MIDI music on stepper motors using an ESP32.
Current CLI command name in this repo: `music2`.

## Quick Start (60 seconds)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/generate_simple_midi.py
scripts/run_pipeline.sh assets/midi/simple4.mid
```

Then open the dashboard URL printed by the script.

If you are new, use this mental model:
- `stepper-music` reads a MIDI file.
- It turns note boundaries into sparse per-motor playback event groups.
- It streams those event groups to ESP32 firmware over USB serial.
- The ESP32 owns continuous playback motion and generates STEP pulses in hardware.
- An optional web dashboard shows playback visually.

## Architecture

The active control boundary in this repo is:

- Host:
  - analyze MIDI
  - compile one playback plan/program
  - stream sparse future intent
  - render CLI/UI observers
- Firmware:
  - own playback timing
  - own event-boundary dispatch
  - execute motor transitions and pulse generation
  - report transport/runtime/backend diagnostics

Song playback and exact motion intentionally use different backends today:

- `playback_wave_engine`: continuous song playback on the MCPWM path
- `pulse_engine`: exact-step `HOME`, `WARMUP`, and `STEP_MOTION`

`HELLO.playback_motor_count` is the source of truth for how many motors the
continuous playback engine currently supports.

## Start here (recommended)

Use the primary run script:

```bash
scripts/run_pipeline.sh assets/midi/simple4.mid
```

This is the main "run everything" entrypoint for this repo.
It can:
- set up Python deps,
- build the dashboard frontend,
- print dashboard URLs,
- and launch `music2 run ...` with your extra flags.

## What this repo contains

- `src/music2`: Python host app (CLI, compiler, serial protocol client, UI backend, warmups, transcription package).
- `firmware/esp32`: ESP32 firmware.
- `ui/dashboard`: React/Vite dashboard frontend.
- `scripts`: helper scripts for running, diagnostics, and conversion.
- `tests`: regression tests.
- `docs`: protocol + bringup + quality + UI docs.

## Requirements

- Python 3.11+
- Node + npm (for dashboard builds)
- PlatformIO (for firmware builds/flashing)
- `ffmpeg` on `PATH` (only needed for MP3 transcription flows)

## Basic install (Python host)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Fast beginner workflow

1. Generate sample MIDI:

```bash
python scripts/generate_simple_midi.py
```

2. Run full stack with the main script:

```bash
scripts/run_pipeline.sh assets/midi/simple4.mid
```

3. If you want direct CLI playback instead:

```bash
music2 run assets/midi/simple4.mid
```

## Main CLI commands

- `music2 run <midi_path>`
  - analyze + compile + stream to hardware
- `music2 ui-preview <midi_path>`
  - preview dashboard timeline without serial hardware
- `music2 analyze <midi_path>`
  - inspect compile/playback quality metrics
- `music2 render-wav <midi_path>`
  - render a stepper-style WAV offline from the event-group playback plan
- `music2 doctor`
  - check environment and project readiness

## Speech Text Mode

The repo now has an additive text-first speech path alongside the MIDI music path.
It does not replace `music2 run`, `music2 ui-preview`, `music2 render-wav`, or `scripts/run_pipeline.sh`.

Primary commands:

- `music2 speech-preview --text "hello nate"`
  - compile text to the six-lane speech motor model and render an offline preview WAV by default
- `music2 speech-analyze --text "hello nate"`
  - inspect phonemes, lane usage, event density, and compile warnings
- `music2 speech-render-wav --text "hello nate"`
  - export a firmware-emulated speech WAV plus metadata
- `music2 speech-run --text "hello nate"`
  - stream the compiled speech plan to hardware over the existing playback transport
- `music2 speech-corpus`
  - render the bundled phrase corpus to `.cache/speech_corpus`

Wrapper entrypoint:

```bash
scripts/run_speech_pipeline.sh --text "hello nate"
scripts/run_speech_pipeline.sh --text "hello nate" --hardware --yes
scripts/run_speech_pipeline.sh --corpus
```

Speech config and assets:

- `config.speech.toml`
- `speech_presets/*.toml`
- `assets/speech_corpus/default.json`
- `assets/speech_lexicon/en_us.json`

Notes:

- The first shipped input path is plain text, not MP3.
- `espeak-ng` is preferred when available; the repo also carries a deterministic rules+lexicon fallback.
- `acoustic_v2` is now the default speech engine. Use `--engine symbolic_v1` if you want the older phoneme-feature compiler.
- `speech-run` will use a speech-only firmware assist profile when the device advertises it, but falls back cleanly to host-only playback on older firmware.
- STT scoring is opt-in (`--evaluate`) so preview/corpus runs stay deterministic and fast by default.

## Dashboard usage

Build frontend manually (if you are not using the main script):

```bash
cd ui/dashboard
npm install
npm run build
```

Run playback + UI:

```bash
music2 run assets/midi/simple4.mid --yes --ui --ui-host 127.0.0.1 --ui-port 8765 --ui-theme neon
```

Open:

```text
http://127.0.0.1:8765
```

UI-only preview (no ESP32 needed):

```bash
music2 ui-preview assets/midi/simple4.mid --ui-host 0.0.0.0 --ui-port 8765
```

## Configuration (`config.toml`)

Main sections:
- `[serial]`: USB serial port and timeouts
- `[hardware]`: connected motor count (`1..8`)
- `[pipeline]`: note allocation + freq limits + event-stream lookahead behavior
- `[homing]`: auto-home and homing motion parameters
- `[warmups]`: optional pre-song warmup sequence and speed settings
- `[playback]`: interactive startup timing and visual playback flags such as `startup_countdown_s`
- `[ui]`: dashboard host/port/theme/color/sync settings

Important behavior:
- Host max frequency is safety-clamped to `800 Hz`.
- Song playback uses protocol-v2 sparse event groups rather than dense full-state segments.
- The ESP32 playback engine owns ramps, flip-to-stop-restart behavior, and STEP timing.
- `playback.startup_countdown_s` defaults to `10`; set it to `0` to disable the interactive pre-start delay.
- `playback.flip_direction_on_note_change` is best treated as an opt-in visual/mechanical effect; when enabled, firmware flips DIR on real note-boundary changes for that motor.
- The checked-in `config.toml` now matches the event-stream playback path and no longer contains legacy mitigation keys.

## Firmware (ESP32)

Firmware lives in `firmware/esp32`.

Build:

```bash
pio run -d firmware/esp32
```

Flash:

```bash
pio run -d firmware/esp32 -t upload
```

Monitor:

```bash
pio device monitor -b 921600
```

Bringup references:
- `docs/bringup_checklist.md`
- `WIRING_DIAGRAM.md`

## Diagnostics and quality checks

```bash
music2 doctor
music2 analyze assets/midi/simple4.mid --json
python3 scripts/quality_baseline.py assets/midi/simple4.mid
python3 scripts/missed_step_probe.py assets/midi/simple4.mid --runs 30
```

Useful docs:
- `docs/sound_quality_runbook.md`
- `docs/speech_text_mode.md`
- `docs/protocol_v2.md`
- `docs/ui_dashboard.md`
- `docs/bringup_checklist.md`

## MP3 to MIDI (optional)

Install transcription extras:

```bash
pip install -e '.[transcribe]'
```

Convert:

```bash
python scripts/mp3_to_midi_best.py path/to/song.mp3 --out-dir assets/midi
```

Outputs:
- `*.motor6.mid`
- `*.expressive6.mid`
- `*.report.json`

## Tests

Run Python tests:

```bash
pytest -q
```

Firmware compile gate:

```bash
pio run -d firmware/esp32
```
