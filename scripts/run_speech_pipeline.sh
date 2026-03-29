#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TEXT=""
TEXT_FILE=""
VOICE=""
PRESET=""
OUT=""
MODE="preview"
SKIP_PY_DEPS="0"
DRY_RUN="0"
EVALUATE="0"
FORWARD_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/run_speech_pipeline.sh --text "hello nate" [options]
  scripts/run_speech_pipeline.sh --text-file phrase.txt [options]

Modes:
  default                Render an offline preview with `music2 speech-preview`
  --hardware             Stream to hardware with `music2 speech-run`
  --analyze              Compile/analyze only with `music2 speech-analyze`
  --corpus               Render/evaluate the bundled phrase corpus

Options:
  --text TEXT            Input phrase text.
  --text-file PATH       Read phrase text from a file.
  --voice VOICE          Voice/frontend hint (default from config.speech.toml).
  --preset PRESET        Speech preset id.
  --out PATH             Output WAV path for preview/render modes.
  --evaluate             Run evaluation when supported by the selected mode.
  --skip-py-deps         Skip pip install -e '.[dev]'.
  --dry-run              Print the final command and exit.
  --yes                  Forwarded to hardware mode to skip Enter prompt.
  -h, --help             Show this help.

Notes:
  - This wrapper leaves scripts/run_pipeline.sh untouched.
  - Unknown trailing args are forwarded to the selected music2 speech command.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --text)
      TEXT="${2:-}"
      shift 2
      ;;
    --text-file)
      TEXT_FILE="${2:-}"
      shift 2
      ;;
    --voice)
      VOICE="${2:-}"
      shift 2
      ;;
    --preset)
      PRESET="${2:-}"
      shift 2
      ;;
    --out)
      OUT="${2:-}"
      shift 2
      ;;
    --hardware)
      MODE="hardware"
      shift
      ;;
    --analyze)
      MODE="analyze"
      shift
      ;;
    --corpus)
      MODE="corpus"
      shift
      ;;
    --evaluate)
      EVALUATE="1"
      shift
      ;;
    --skip-py-deps)
      SKIP_PY_DEPS="1"
      shift
      ;;
    --dry-run)
      DRY_RUN="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      FORWARD_ARGS+=("$@")
      break
      ;;
    *)
      FORWARD_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "$MODE" != "corpus" ]]; then
  if [[ -n "$TEXT" && -n "$TEXT_FILE" ]]; then
    echo "Provide exactly one of --text or --text-file." >&2
    exit 2
  fi
  if [[ -z "$TEXT" && -z "$TEXT_FILE" ]]; then
    echo "Provide --text or --text-file." >&2
    exit 2
  fi
fi

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "Creating Python venv at .venv"
  python3 -m venv .venv
fi

VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ "$SKIP_PY_DEPS" != "1" ]]; then
  "$VENV_PY" -m pip install -e '.[dev]' >/dev/null
fi

CMD=("$VENV_PY" "-m" "music2.cli")
case "$MODE" in
  preview)
    CMD+=("speech-preview")
    ;;
  analyze)
    CMD+=("speech-analyze")
    ;;
  hardware)
    CMD+=("speech-run")
    ;;
  corpus)
    CMD+=("speech-corpus")
    ;;
esac

if [[ "$MODE" != "corpus" ]]; then
  if [[ -n "$TEXT" ]]; then
    CMD+=("--text" "$TEXT")
  else
    CMD+=("--text-file" "$TEXT_FILE")
  fi
fi
if [[ -n "$VOICE" ]]; then
  CMD+=("--voice" "$VOICE")
fi
if [[ -n "$PRESET" ]]; then
  CMD+=("--preset" "$PRESET")
fi
if [[ -n "$OUT" ]]; then
  CMD+=("--out" "$OUT")
fi
if [[ "$EVALUATE" == "1" && "$MODE" != "hardware" ]]; then
  CMD+=("--evaluate")
fi
CMD+=("${FORWARD_ARGS[@]}")

if [[ "$DRY_RUN" == "1" ]]; then
  printf '%q ' "${CMD[@]}"
  printf '\n'
  exit 0
fi

printf 'Running: '
printf '%q ' "${CMD[@]}"
printf '\n'
"${CMD[@]}"
