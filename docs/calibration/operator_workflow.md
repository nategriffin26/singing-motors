# Operator Annotation Workflow

Machine metrics still miss some human-obvious issues like roughness, ugly resonance, or visible missed-step behavior.

Attach structured notes to a session like this:

```bash
python3 scripts/calibrate/annotate_session.py .cache/calibration/<bundle> \
  --test-id frequency-0-1800 \
  --label audible_roughness \
  --severity 0.7 \
  --notes "buzzing starts around 180 Hz"
```

Annotations are stored in `annotations.json` and stay attached to a concrete measurement id instead of being lost in prose.
