# Calibration Workflow

Frequency sweep:

```bash
python3 scripts/calibrate/frequency_sweep.py 0 --config config.toml \
  --start-hz 20 --stop-hz 220 --step-hz 20 --transport synthetic
```

Launch sweep:

```bash
python3 scripts/calibrate/launch_sweep.py 0 --config config.toml \
  --target-hz 220 --launch-starts 40,60,80 --launch-crossovers 160,180,220 \
  --transport synthetic
```

Reversal sweep:

```bash
python3 scripts/calibrate/reversal_sweep.py 0 --config config.toml \
  --target-hz 180 --reversal-gaps-ms 4,8,12,16 --transport synthetic
```

Fit a draft patch:

```bash
python3 scripts/calibrate/fit_profile.py \
  --instrument-profile profiles/default_instrument.toml \
  --bundle .cache/calibration/<bundle-a> \
  --bundle .cache/calibration/<bundle-b> \
  --out .cache/calibration/profile_patch.json
```

Merge into a new profile file:

```bash
python3 scripts/calibrate/merge_profile.py \
  --instrument-profile profiles/default_instrument.toml \
  --patch .cache/calibration/profile_patch.json \
  --out profiles/default_instrument.calibrated.toml
```
