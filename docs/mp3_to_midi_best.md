# MP3 to MIDI Ultra Converter

Standalone entrypoint: `scripts/mp3_to_midi_best.py`

## Goal

Produce the highest-quality practical transcription pipeline in this repo while enforcing a strict physical limit of six simultaneous notes for motor playback.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[transcribe]'
```

Required external tool:

- `ffmpeg` on `PATH`

## Usage

```bash
python scripts/mp3_to_midi_best.py input.mp3 --out-dir assets/midi
```

Important flags:

- `--max-polyphony 6` hard ceiling (values > 6 are rejected)
- `--quality {ultra,high,balanced}`
- `--mode {music,speech}` selects separate transcription pipelines
- `--mt3-cmd 'python your_mt3_runner.py --in {input} --out {output}'` (optional external backend)
- `--no-demucs` to skip source separation in `--mode music`
- `--min-note-duration-s`, `--min-confidence` post-processing thresholds
- `--beat-quantize-max-shift-s` + `--no-beat-quantize` quantization control
- `--speech-start-confidence`, `--speech-sustain-confidence`
- `--speech-max-pitch-jump-semitones`, `--speech-median-filter-window`
- `--json` for machine-readable output

## Pipeline Summary

`--mode music`

1. Demucs stem separation (when available).
2. Piano transcription inference on accompaniment stem (`no_vocals.wav`).
3. Basic Pitch on full mix, optional MT3 via `--mt3-cmd`.
4. Candidate fusion, then music post-processing (duration/confidence filters, octave correction, optional beat quantization + velocity compression).
5. Deterministic polyphony-cap enforcement (<= 6).
6. Dual MIDI write:
   - `*.motor6.mid`: note-on/off only
   - `*.expressive6.mid`: includes pitchwheel events

`--mode speech`

1. Speech pitch tracking only (torchcrepe primary, librosa pYIN fallback).
2. Speech post-processing (80ms min duration, confidence filter, octave correction).
3. Deterministic polyphony-cap enforcement (<= 6).
4. Dual MIDI write + report JSON with backend usage, warnings, and drop counts.

## Compatibility Notes

- Python 3.14 can break parts of this heavy transcription stack. Use Python 3.13 when possible.
- If transcription backends are missing, install `.[transcribe]` or provide `--mt3-cmd`.
