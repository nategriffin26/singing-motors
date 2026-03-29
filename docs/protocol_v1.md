# music2 Serial Protocol v1

This document is the legacy playback reference. The coordinated playback
redesign now targets [protocol v2](protocol_v2.md), which keeps the same frame
transport and command IDs but switches song playback from dense segments to
sparse event groups.

## Frame Transport

1. Encode each frame using COBS.
2. Terminate each encoded frame with delimiter byte `0x00`.
3. Compute CRC-16/CCITT-FALSE over `header || payload` (not over COBS bytes).

## Frame Layout (before COBS)

Header struct `<BBHHBH>`:

1. `version` (`u8`) - must be `1`
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

1. `for_command` (`u8`) - command being acknowledged
2. `ack_flags` (`u8`) - reserved
3. `credits` (`u16`) - available stream credits
4. `queue_depth` (`u16`) - queued timed segments
5. `extra` (`bytes`) - optional command-specific metadata

For `HELLO`, `extra` layout is `<BBBHH>`:

1. `protocol_version` (`u8`)
2. `motor_count` (`u8`)
3. `feature_flags` (`u8`) (`bit0=timed streaming`, `bit1=HOME`, `bit2=WARMUP`, `bit3=STEP_MOTION`)
4. `queue_capacity` (`u16`)
5. `scheduler_tick_us` (`u16`)

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

Struct `<BBBBb>`:

1. `motors` (`u8`) - expected connected motors
2. `idle_mode` (`u8`) - `0=idle`, `1=duplicate`
3. `min_note` (`u8`) - reporting/telemetry only
4. `max_note` (`u8`) - reporting/telemetry only
5. `transpose` (`i8`) - applied global transpose semitones

### STREAM_BEGIN (`0x03`)

Struct `<IH>`:

1. `total_segments` (`u32`)
2. `requested_credits` (`u16`)

Validation rules:

1. `total_segments` must be `> 0`.
2. `requested_credits` must be `> 0`.

### STREAM_APPEND (`0x04`)

Payload:

1. `segment_count` (`u8`)
2. Repeated `segment_count` times:
   - Legacy form:
     - `duration_us` (`u32`)
     - `freq_dhz[8]` (`u16[8]`) where `0` means motor idle
   - Flip-aware form:
     - `duration_us` (`u32`)
     - `direction_flip_mask` (`u8`) bitmask of motors that must flip DIR at segment start
     - `freq_dhz[8]` (`u16[8]`)

`freq_dhz` is frequency in deci-Hz (`Hz * 10`).

Validation/safety rules:

1. `duration_us` must be at least the firmware pulse tick (`PULSE_ENGINE_TICK_US`, default `25`).
2. `freq_dhz` is safety-clamped to `12000` (1200 Hz) to protect pulse timing headroom.

### STREAM_END (`0x05`), PLAY (`0x06`), STOP (`0x07`)

No payload.

### HOME (`0x0A`)

Payload supports both legacy and extended forms:

- Legacy struct `<HH>`:
1. `steps_per_rev` (`u16`) - full-step+microstep count for one 360-degree revolution
2. `home_freq_dhz` (`u16`) - homing frequency in deci-Hz (`Hz * 10`)

- Extended struct `<HHHH>`:
1. `steps_per_rev` (`u16`)
2. `home_start_freq_dhz` (`u16`) - initial homing speed (`Hz * 10`)
3. `home_freq_dhz` (`u16`) - target homing speed (`Hz * 10`)
4. `home_accel_hz_per_s_dhz` (`u16`) - acceleration in deci-Hz per second (`Hz/s * 10`)

Behavior:

1. Firmware reads each motor's emitted step count modulo `steps_per_rev`.
2. Each configured motor homes sequentially (one motor at a time) to the next modulo-0 boundary.
3. Extended payload enables ramped homing speed (`start -> target`) per motor.
3. ACK for `HOME` is sent only after homing completes (or ERR on failure).

### STEP_MOTION (`0x0C`)

Step-targeted motion command for warmups, diagnostics, and deterministic position tests.

Payload:

1. `motor_count` (`u8`) number of motor profiles (`1..8`)
2. Repeated `motor_count` times:
   - `start_delay_ms` (`u16`)
   - `trigger_motor` (`u8`) (`0xFF` = no trigger)
   - `trigger_steps` (`u16`) relative threshold on `trigger_motor`
   - `phase_count` (`u8`) (`1..4`)
   - Repeated `phase_count` times:
     - `target_steps` (`u16`) desired steps in phase
     - `peak_dhz` (`u16`) peak frequency (`Hz * 10`)
     - `accel_dhz_per_s` (`u16`) acceleration (`Hz/s * 10`)
     - `decel_dhz_per_s` (`u16`) deceleration (`Hz/s * 10`)
     - `hold_ms` (`u16`) minimum hold time at peak before decel

Behavior:

1. Runs only when not playing and stream is closed.
2. Each active phase is step-capped (`target_steps`) with trapezoidal accel/decel shaping.
3. Motors can cascade by position trigger (`trigger_motor`, `trigger_steps`) exactly like WARMUP.
4. Command blocks until all profiles complete, then ACKs `STEP_MOTION`.

### STATUS (`0x08`) response payload

Firmware responds with `cmd=0x08` and payload struct `<BBBBHHHBBI>`:

1. `version` (`u8`)
2. `state_flags` (`u8`) (`bit0=playing`, `bit1=stream_open`, `bit2=stream_end_received`)
3. `motor_count` (`u8`)
4. `reserved` (`u8`)
5. `queue_depth` (`u16`)
6. `queue_capacity` (`u16`)
7. `credits` (`u16`)
8. `active_motors` (`u8`)
9. `reserved2` (`u8`)
10. `playhead_us` (`u32`)

### METRICS (`0x09`) response payload

Firmware responds with `cmd=0x09` and payload legacy struct `<IHHIIIHH>`:

1. `underrun_count` (`u32`)
2. `queue_high_water` (`u16`)
3. `reserved` (`u16`)
4. `scheduling_late_max_us` (`u32`)
5. `crc_parse_errors` (`u32`)
6. `rx_parse_errors` (`u32`)
7. `queue_depth` (`u16`)
8. `credits` (`u16`)

Extended payload (backward-compatible append) adds `<III>`:

9. `timer_empty_events` (`u32`) - count of one-shot scheduler callbacks that found no queued segment
10. `timer_restart_count` (`u32`) - count of scheduler restarts after an underrun/empty period
11. `segments_started` (`u32`) - count of segments started by playback scheduler

Extended payload v3 (backward-compatible append) adds another `<III>`:

12. `scheduler_guard_hits` (`u32`) - count of prevented concurrent scheduler starts
13. `pulse_late_max_us` (`u32`) - maximum observed pulse scheduler lateness
14. `pulse_edge_drop_count` (`u32`) - count of collapsed/late pulse edges

Extended payload v4 (backward-compatible append) adds `<I>`:

15. `playback_slew_clamp_count` (`u32`) - count of runtime high-note slew clamps applied before pulse output
