# Benchmark Release Gate

Quick gate:

- run the default benchmark suite in `analyze-only` mode
- run at least one representative `hardware-run` case when hardware is attached
- compare the candidate bundle(s) against a saved baseline bundle

Full gate:

- run the full suite
- include at least one dense case, one reversal-pressure case, and one long-duration case on hardware
- compare against a baseline taken with the same instrument profile and firmware generation

Skip a long hardware suite only when the change is clearly docs-only or when no playback/runtime/planning/calibration path changed.
