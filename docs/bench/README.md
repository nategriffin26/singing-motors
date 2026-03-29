# Benchmark Workflow

Quick suite:

```bash
python3 scripts/bench/run_suite.py --config config.toml --quick --mode analyze-only
```

Single hardware case:

```bash
python3 scripts/bench/run_case.py fur_elise_dense --config config.toml --mode hardware-run
```

Compare two bundles:

```bash
python3 scripts/bench/compare_runs.py .cache/bench/<old> .cache/bench/<new>
python3 scripts/bench/render_report.py .cache/bench/<old> .cache/bench/<new>
```

Red means transport/runtime/backend regressions increased. Yellow means comfort or plan-quality pressure increased. Green means no worsening was detected in the tracked metrics.
