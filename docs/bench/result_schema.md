# Benchmark Result Schema

Each benchmark bundle lives under `.cache/bench/<bundle-id>/`.

Required files:

- `manifest.json`: schema version, case metadata, provenance, mode
- `analyze.json`: compile/arrangement/playback-plan summary
- `run_metrics.json`: final hardware metrics and device capabilities when a hardware run happened
- `status_trace.jsonl`: normalized status samples
- `metrics_trace.jsonl`: normalized metrics samples
- `stdout.txt`: reserved operator capture slot

Key machine-readable health surfaces:

- transport health: `underrun_count`, `crc_parse_errors`, `rx_parse_errors`
- scheduler/runtime health: `scheduling_late_max_us`, `scheduler_guard_hits`, `timer_empty_events`
- backend health: `control_overrun_count`, `launch_guard_count`, `engine_fault_count`
- plan/completion health: `event_group_count`, `event_groups_started`, `motor_change_count`
- operator-facing quality: arrangement loss, comfort violations, preferred/avoid/resonance hits
