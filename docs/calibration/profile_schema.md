# Calibration Profile Schema

Calibration remains inside the instrument profile TOML so the compiler and runtime can consume it directly.

Motor-level calibration keys are additive and backward-compatible. Important fields:

- `calibration_status`
- `calibration_confidence`
- `measured_min_hz` / `measured_max_hz`
- `fitted_min_hz` / `fitted_max_hz`
- `override_min_hz` / `override_max_hz`
- `measured_preferred_min_hz` / `measured_preferred_max_hz`
- `fitted_preferred_min_hz` / `fitted_preferred_max_hz`
- `override_preferred_min_hz` / `override_preferred_max_hz`
- `fitted_launch_start_hz` / `fitted_launch_crossover_hz`
- `fitted_safe_reverse_min_gap_ms`
- `safe_accel_min_hz_per_s` / `safe_accel_max_hz_per_s`
- `stall_prone_bands`
- `calibration_measurement_ids`
- `operator_notes`

The compiler/reporting layer resolves values in this order:

1. override
2. fitted
3. measured
4. legacy/base field
