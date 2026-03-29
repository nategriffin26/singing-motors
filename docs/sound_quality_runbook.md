# Sound Quality Runbook

## Goal

Get reproducible, low-artifact playback with the event-group + continuous
MCPWM playback path before tuning for expression.

For the additive speech mode, keep the same transport/runtime health goals while judging intelligibility on its own terms instead of musical fidelity.

## Current Playback Model

The active playback path is:

- host compiles MIDI into sparse playback event groups,
- host streams those groups over protocol v2,
- firmware schedules group boundaries on its playback clock,
- firmware generates STEP pulses continuously on dedicated MCPWM lanes,
- firmware handles stop, flip DIR, and restart locally.

That means playback quality is now judged primarily by event-stream health and playback-engine health, not by dense segment-shaping heuristics.

## Task 0 Baseline Freeze

This project phase freezes the current playback architecture before deeper
runtime refactors:

- protocol v2 event-group playback is the only supported song-playback path
- the continuous MCPWM-backed `playback_wave_engine` is the active song-playback backend
- `pulse_engine` remains the exact-motion backend for `HOME`, `WARMUP`, and `STEP_MOTION`
- the playback backend currently supports 6 motors while exact-motion paths still support 8 motors
- the host compiles and streams future intent, but firmware owns playback timing

Future architectural changes in this phase must not move playback timing
ownership back into the host transport loop or reintroduce host-side pulse
scheduling.

## Recommended Defaults

- `idle_mode = "idle"`
- `min_freq_hz = 15`
- `max_freq_hz = 800`
- `lookahead_ms = 2000`
- `lookahead_strategy = "p95"`
- `lookahead_min_ms = 400`
- `lookahead_min_segments = 24`
- `flip_direction_on_note_change = false` unless you specifically want the visual reversal effect
- `direction_flip_cooldown_ms = 150`
- `direction_flip_safety_margin_ms = 50`
- `playback_run_accel_hz_per_s = 8000`
- `playback_launch_start_hz = 60`
- `playback_launch_accel_hz_per_s = 5000`
- `playback_launch_crossover_hz = 180`
- `home_start_hz = 120`
- `home_hz = 160`
- `home_accel_hz_per_s = 240`

## Baseline Procedure

1. Run diagnostics:
   - `music2 doctor`
2. Analyze playback-plan quality:
   - `music2 analyze assets/midi/simple4.mid`
3. Generate baseline report for references:
   - `python3 scripts/quality_baseline.py assets/midi/simple4.mid assets/midi/Tetris\ -\ Tetris\ Main\ Theme.mid --out .cache/quality_baseline.json`
4. Playback test:
   - `music2 run assets/midi/simple4.mid --yes`
5. Repeated reliability probe:
   - `python3 scripts/missed_step_probe.py assets/midi/simple4.mid --runs 30`
6. Stress test the real song:
   - `music2 run assets/midi/Fur\ Elise.mid --yes`

## Acceptance Gates

The following are frozen hard gates for the architecture work in
`MOTOR_CONTROL_ARCHITECTURE_IMPLEMENTATION_PLAN.md`:

- `underrun_count == 0`
- `crc_parse_errors == 0`
- `rx_parse_errors == 0`
- `scheduler_guard_hits == 0`
- `pulse_edge_drop_count` is no longer the primary song-playback metric
- `event_groups_started == compiled event_group_count`
- `control_overrun_count == 0`
- `engine_fault_count == 0`
- `engine_fault_mask == 0`
- `measured_pulse_drift_total == 0` on stable baseline hardware runs, or any non-zero drift is explicitly investigated
- `flip_restart_count` stays explainable and consistent with the requested musical material
- `launch_guard_count` stays explainable and consistent with short-note/high-note material
- The run has no synchronized multi-motor skipping and no scheduler-caused missed notes.

Speech-mode additions:

- `music2 speech-preview --text "hello nate"` should render deterministically for the same input and preset.
- `music2 speech-preview --text "hello nate" --engine acoustic_v2` is the main tuning path; compare against `--engine symbolic_v1` when judging improvement.
- `music2 speech-analyze --text "hello nate"` should expose lane usage, retarget counts, burst counts, and any safe-envelope warnings.
- `music2 speech-run ...` must still satisfy:
  - `underrun_count == 0`
  - `crc_parse_errors == 0`
  - `rx_parse_errors == 0`
  - `scheduler_guard_hits == 0`
  - `engine_fault_count == 0`
- If firmware advertises speech assist, verify the speech run still stays inside those gates before keeping it enabled.
- Do not “improve” intelligibility by accepting engine faults or transport instability.

For the newer benchmark/calibration/simulation platform gates, also use
[`docs/bench/release_gate.md`](./bench/release_gate.md).

## Hardware Checklist

### Driver setup
- Verify microstep settings are consistent across channels.
- Verify current-limit trim on each driver (avoid over-current resonance/noise).
- Confirm driver STEP/DIR pulse-width requirements are compatible with the firmware playback engine.

### Mechanical setup
- Use rigid motor mounting with vibration isolation from the frame.
- Reduce resonance via mass-loading or dampers on high-noise channels.
- Keep wiring strain-relieved to avoid intermittent ground or signal movement.

### Power and grounding
- Shared signal ground between ESP32 and drivers.
- Keep motor power wiring physically separated from logic/control wiring.
- Use stable supply rails under peak load.

## Tuning Loop

1. Start from the checked-in defaults.
2. If underruns appear, raise lookahead first (`lookahead_ms`, `lookahead_min_ms`, `lookahead_min_segments`) before changing anything musical.
3. If `control_overrun_count` or `engine_fault_count` appears in dense passages, treat that as playback-engine pressure, not a reason to reintroduce note thinning.
4. If `inferred_pulse_total` and `measured_pulse_total` diverge, treat that as hardware-truth evidence of missed or miscounted playback pulses and debug backend/runtime behavior before changing the arrangement.
5. If high notes stall or sound mushy from rest, lower `playback_launch_start_hz`, lower `playback_launch_crossover_hz`, or reduce playback launch/run acceleration before touching the arrangement.
6. If the sound is harsh overall, reduce `max_freq_hz` before changing the arrangement.
7. If the direction-flip visual mode is enabled and the piece becomes mechanically rough, disable `flip_direction_on_note_change` first and retest.
8. If homing drift appears, lower `home_hz` toward `120` or reduce `home_accel_hz_per_s`.
9. Only after playback is stable should you tune for more expression or denser material.
