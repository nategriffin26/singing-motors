## ESP32 Bringup Checklist (music2 firmware)

1. Connect ESP32 over USB and confirm the serial device path appears.
2. From `firmware/esp32`, build firmware:
   - `pio run`
3. Flash firmware:
   - `pio run -t upload`
4. Open serial monitor at `921600`:
   - `pio device monitor -b 921600`
5. Confirm startup log includes the active backend split:
   - continuous song playback on the MCPWM `playback_wave_engine`
   - exact-step `HOME` / `WARMUP` / `STEP_MOTION` on `pulse_engine`
   - playback motor count may differ from exact-motion motor count
6. Verify driver wiring before playback:
   - STEP pins mapped to GPIO16,17,18,19,21,22,23,25.
   - DIR pins are firmware-driven on the playback-capable lanes when direction flips are enabled.
   - ENA is wired consistently with the current board/driver bringup.
7. Send protocol commands in order:
   - `HELLO -> SETUP -> STREAM_BEGIN -> STREAM_APPEND -> STREAM_END -> PLAY`
   - Optional completion cleanup: `HOME` (if `HELLO` feature bit1 is set)
8. Confirm responses:
   - `ACK` for control commands.
   - `STATUS` returns queue/credits/state.
   - `METRICS` returns transport health, playback-runtime health, engine-fault counters, and pulse-accounting fields (`inferred_pulse_total`, `measured_pulse_total`, `measured_pulse_drift_total`, `measured_pulse_active_mask`) when present.
9. Stop playback cleanly with `STOP` before disconnecting power.
