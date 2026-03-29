# Speech Text Mode

## Goal

Add a text-first speech mode on top of the existing `music2` control shell without disturbing the current music path.

## Operator Surface

- `music2 speech-preview --text "hello nate"`
- `music2 speech-analyze --text "hello nate"`
- `music2 speech-render-wav --text "hello nate"`
- `music2 speech-run --text "hello nate"`
- `music2 speech-corpus`
- `scripts/run_speech_pipeline.sh --text "hello nate"`

Input options:

- `--text`
- `--text-file`
- `--phonemes-file`
- `--voice`
- `--engine`
- `--preset`

## Architecture

The speech path is additive:

- text or phoneme JSON input
- deterministic frontend (`espeak-ng` when available, repo fallback otherwise)
- speech utterance IR
- engine selection:
  - `symbolic_v1`: original phoneme-feature compiler
  - `acoustic_v2`: formant/resonance compiler with coarticulation smoothing and optional oracle analysis
- six-lane speech compiler
- playback plan on the existing event-group transport
- optional speech-only firmware assist on `speech-run`
- offline WAV render and optional evaluation

The speech code lives under `src/music2/speech_text/`.

## Presets and Config

- Global defaults: `config.speech.toml`
- Presets: `speech_presets/*.toml`
- Phrase corpus: `assets/speech_corpus/default.json`
- Lexicon overrides: `assets/speech_lexicon/en_us.json`

## Guardrails

- The music path remains the default and is unchanged.
- `scripts/run_pipeline.sh` remains the music wrapper.
- Speech mode is reached only through the new speech commands and wrapper.
- Offline render and compile diagnostics are the first tuning surface.
- Speech-only firmware assist is negotiated separately and must not change `music2 run` behavior.
- STT evaluation is opt-in via `--evaluate`.

## What To Watch

- `max_event_rate_hz`
- `lane_retarget_count`
- `burst_count`
- compile warnings about event density
- hardware metrics:
  - `underrun_count`
  - `crc_parse_errors`
  - `rx_parse_errors`
  - `scheduler_guard_hits`
  - `engine_fault_count`
