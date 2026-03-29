# music2 Serial Protocol v2

This is the coordinated playback-redesign protocol. It keeps the same framed
transport and command IDs, but song playback changes from dense full-state
segments to sparse timestamped event groups.

This document describes the active protocol for song playback on the continuous
MCPWM-backed engine. Non-playback commands such as `HOME`, `WARMUP`, and
`STEP_MOTION` still reuse their legacy wire shapes from `docs/protocol_v1.md`.

## Control-Layer Boundary

- Host responsibilities:
  - analyze musical material
  - compile sparse playback intent
  - stream that intent over framed serial transport
- Firmware responsibilities:
  - own the playback clock
  - dispatch event boundaries
  - execute motor state transitions deterministically
  - expose diagnostics for scheduler/runtime/backend faults
- Backend split:
  - `playback_wave_engine` owns continuous song playback
  - `pulse_engine` owns exact-step motion (`HOME`, `WARMUP`, `STEP_MOTION`)
  - the playback motor count may be lower than the exact-motion motor count; `HELLO.playback_motor_count` is the durable source of truth

## Frame Transport

1. Encode each frame using COBS.
2. Terminate each encoded frame with delimiter byte `0x00`.
3. Compute CRC-16/CCITT-FALSE over `header || payload` before COBS.

## Frame Layout

Header struct `<BBHHBH>`:

1. `version` (`u8`) - must be `2`
2. `cmd` (`u8`) - command ID
3. `magic` (`u16`) - must be `0x4D32`
4. `seq` (`u16`) - host-chosen sequence number echoed by firmware responses
5. `flags` (`u8`) - currently `0`
6. `payload_len` (`u16`) - byte length of payload

Body:

1. `payload` (`payload_len` bytes)
2. `crc16` (`u16`) - CRC-16/CCITT-FALSE over `header || payload`

## Command IDs

- `0x01` `HELLO`
- `0x02` `SETUP`
- `0x03` `STREAM_BEGIN`
- `0x04` `STREAM_APPEND`
- `0x05` `STREAM_END`
- `0x06` `PLAY`
- `0x07` `STOP`
- `0x08` `STATUS`
- `0x09` `METRICS`
- `0x0A` `HOME`
- `0x0B` `WARMUP`
- `0x0C` `STEP_MOTION`
- `0x7E` `ACK`
- `0x7F` `ERR`

## ACK / ERR Payloads

### ACK (`0x7E`)

Struct prefix:

1. `for_command` (`u8`)
2. `ack_flags` (`u8`) - reserved
3. `credits` (`u16`)
4. `queue_depth` (`u16`)
5. `extra` (`bytes`) - optional command metadata

For `HELLO`, `extra` layout is:

- Legacy-compatible v1 shape: `<BBBHH>`
- v2 playback-engine shape: `<BBBHHIB>`

Fields:

1. `protocol_version` (`u8`)
2. `motor_count` (`u8`)
3. `feature_flags` (`u8`)
4. `queue_capacity` (`u16`)
5. `scheduler_tick_us` (`u16`)
6. `playback_run_accel_dhz_per_s` (`u32`) - default firmware run acceleration in deci-Hz per second
7. `playback_motor_count` (`u8`) - number of motors supported by the continuous song-playback engine

The host must accept both exact `HELLO` extra layouts during the migration to
the full redesign. If the extended fields are absent, playback-engine defaults
to host-side fallbacks. Extra lengths other than `7`, `11`, or `12` bytes are
malformed.

### ERR (`0x7F`)

Struct prefix:

1. `for_command` (`u8`)
2. `error_code` (`u8`)
3. `credits` (`u16`)
4. `queue_depth` (`u16`)
5. optional UTF-8 diagnostic bytes

## Command Payloads

### HELLO (`0x01`)

- `host_version_len` (`u8`)
- `host_version` (`host_version_len` bytes UTF-8)

### SETUP (`0x02`)

Struct prefix `<BBBBb>`:

1. `motors` (`u8`)
2. `idle_mode` (`u8`) - `0=idle`, `1=duplicate`
3. `min_note` (`u8`) - reporting only
4. `max_note` (`u8`) - reporting only
5. `transpose` (`i8`) - applied global transpose semitones

Optional playback-profile tail `<IHIH>`:

6. `playback_run_accel_dhz_per_s` (`u32`)
7. `playback_launch_start_dhz` (`u16`)
8. `playback_launch_accel_dhz_per_s` (`u32`)
9. `playback_launch_crossover_dhz` (`u16`)

Behavior:

1. The base 5-byte payload remains valid.
2. If the playback-profile tail is present, firmware applies it to the
   continuous song-playback engine.
3. `playback_launch_start_dhz` must be `> 0`.
4. `playback_launch_crossover_dhz` must be `>= playback_launch_start_dhz`.

### STREAM_BEGIN (`0x03`)

Struct `<IH>`:

1. `total_segments` (`u32`) - interpreted as `total_event_groups` in v2 playback
2. `requested_credits` (`u16`)

Validation:

1. `total_segments` must be `> 0`
2. `requested_credits` must be `> 0`

### STREAM_APPEND (`0x04`) — Playback Event Groups

Payload:

1. `event_group_count` (`u8`)
2. Repeated `event_group_count` times:
   - `delta_us` (`u32`) - relative to the previous event-group start
   - `change_count` (`u8`) - must be in `1..8`
   - repeated `change_count` times:
     - `motor_idx` (`u8`) - must be in `0..7`
     - `target_dhz` (`u16`) - target frequency in deci-Hz
     - `change_flags` (`u8`)

`change_flags` bit assignments:

- bit0 = `flip_before_restart`
- bits1-7 are reserved and must be zero

Receivers must reject any event-group change with reserved bits set.

Behavior:

1. Only motors listed in the event group change at that boundary.
2. Unlisted motors keep their current playback state.
3. `target_dhz = 0` means stop that motor.
4. A flip-bearing change means the firmware must stop that motor, flip DIR,
   and restart toward the pending target.
5. A single event group must not contain duplicate `motor_idx` entries.

Validation:

1. `event_group_count` must be `> 0`
2. `event_group_count` must not exceed the protocol payload capacity for the
   minimum event-group shape
3. `change_count` must be `> 0`
4. `change_count` must be `<= 8`
5. Reserved bits in `change_flags` must be zero
6. Duplicate `motor_idx` entries in a single event group are invalid
7. Total payload must fit within the protocol `MAX_PAYLOAD`

### STREAM_END (`0x05`), PLAY (`0x06`), STOP (`0x07`)

No payload.

### METRICS (`0x09`)

Payload layout is backward-compatible and append-only. Older hosts may parse a
shorter prefix; newer hosts should accept all currently documented tails.

The first `48` bytes report transport and playback-runtime health:

1. `underrun_count` (`u32`)
2. `queue_high_water` (`u16`)
3. reserved (`u16`)
4. `scheduling_late_max_us` (`u32`)
5. `crc_parse_errors` (`u32`)
6. `rx_parse_errors` (`u32`)
7. `queue_depth` (`u16`)
8. `credits` (`u16`)
9. `timer_empty_events` (`u32`)
10. `timer_restart_count` (`u32`)
11. `event_groups_started` (`u32`)
12. `scheduler_guard_hits` (`u32`)

The next `28` bytes are engine-neutral playback counters:

13. `control_late_max_us` (`u32`)
14. `control_overrun_count` (`u32`)
15. `wave_period_update_count` (`u32`)
16. `motor_start_count` (`u32`)
17. `motor_stop_count` (`u32`)
18. `flip_restart_count` (`u32`)
19. `launch_guard_count` (`u32`)
20. `engine_fault_count` (`u32`)
21. `engine_fault_mask` (`u32`)

When present, the payload may continue with backend-fault breakdown fields:

22. `engine_fault_attach_count` (`u32`)
23. `engine_fault_detach_count` (`u32`)
24. `engine_fault_period_count` (`u32`)
25. `engine_fault_force_count` (`u32`)
26. `engine_fault_timer_count` (`u32`)
27. `engine_fault_invalid_change_count` (`u32`)
28. `engine_fault_last_reason` (`u32`)
29. `engine_fault_last_motor` (`u32`)

When present, the payload may continue with pulse-accounting fields sourced
from hardware-truth counting:

30. `inferred_pulse_total` (`u32`) - planned/inferred emitted playback pulses
31. `measured_pulse_total` (`u32`) - measured playback pulses from pulse accounting
32. `measured_pulse_drift_total` (`u32`) - absolute cumulative drift between inferred and measured counts
33. `measured_pulse_active_mask` (`u32`) - bitmask of playback motors actively tracked by the pulse-accounting backend
34. `exact_position_lost_mask` (`u32`) - bitmask of exact-motion motors whose signed position tracking was marked unreliable
35. `playback_position_unreliable_mask` (`u32`) - bitmask of playback motors whose measured signed position cannot be trusted for end homing
36. `playback_signed_position_drift_total` (`u32`) - absolute cumulative signed-position drift between inferred playback motion and measured hardware-truth playback motion

### HOME (`0x0A`), WARMUP (`0x0B`), STEP_MOTION (`0x0C`)

Unchanged from v1 in this task. See `docs/protocol_v1.md` for the existing
wire shapes until those docs are fully migrated.
