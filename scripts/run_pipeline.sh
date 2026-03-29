#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MIDI_PATH=""
UI_HOST="0.0.0.0"
UI_PORT="8765"
SERIAL_PORT=""
SKIP_PY_DEPS="0"
SKIP_FRONTEND_BUILD="0"
DRY_RUN="0"
HIGH_RATE="0"
UI_RENDER_MODE="prerender-30"
NPM_CACHE_DIR=""
UI_PREWARM="1"
FORWARD_ARGS=()
POSITIONAL_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/run_pipeline.sh [midi_path] [wrapper_options] [stepper_music_args...]
  scripts/run_pipeline.sh [midi_path] [wrapper_options] -- [stepper_music_args...]

Examples:
  scripts/run_pipeline.sh assets/midi/simple4.mid
  scripts/run_pipeline.sh --sample
  scripts/run_pipeline.sh my_song.mid --ui-port 9000 --serial-port /dev/cu.usbserial-0001
  scripts/run_pipeline.sh my_song.mid --transpose -12
  scripts/run_pipeline.sh my_song.mid --home-hz 90 --home-steps-per-rev 800
  scripts/run_pipeline.sh --sample -- --motors 6 --lookahead-ms 1000

Options:
  --sample                 Generate/use assets/midi/simple4.mid.
  --ui-host HOST           UI bind host (default: 0.0.0.0 for Tailscale reachability).
  --ui-port PORT           UI bind port (default: 8765).
  --serial-port PORT       Serial port override passed to stepper-music run --port.
  --ui-render-mode MODE    Viewer mode: prerender-30 (default) or live.
  --high-rate              Enable higher-rate UI telemetry updates.
  --no-ui-prewarm          Skip background UI/API prewarm requests.
  --npm-cache-dir PATH     npm cache location (default: <repo>/.npm-cache).
  --skip-py-deps           Skip pip install -e '.[dev]'.
  --skip-frontend-build    Skip npm install/build.
  --dry-run                Print the final command and exit.
  -h, --help               Show this help.

Notes:
  - This script prepares playback + UI in one command, then waits for Enter to start.
  - It prints Tailscale URLs you can open from iPhone.
  - Unknown options are forwarded directly to "stepper-music run" for future-flag compatibility.
  - To auto-start without waiting, pass --yes in forwarded stepper-music args.
  - Put wrapper options before forwarded options (or separate with "--").
EOF
}

# Parser contract:
# - This script handles only wrapper-owned options in the case statement below.
# - Any unknown option starts passthrough mode and forwards that token plus the rest to stepper-music run.
# - Forwarded args are appended last in CMD, so they override earlier wrapper-provided defaults.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sample)
      MIDI_PATH="assets/midi/simple4.mid"
      shift
      ;;
    --ui-host)
      UI_HOST="${2:-}"
      shift 2
      ;;
    --ui-port)
      UI_PORT="${2:-}"
      shift 2
      ;;
    --serial-port)
      SERIAL_PORT="${2:-}"
      shift 2
      ;;
    --ui-render-mode)
      UI_RENDER_MODE="${2:-}"
      shift 2
      ;;
    --high-rate)
      HIGH_RATE="1"
      shift
      ;;
    --no-ui-prewarm)
      UI_PREWARM="0"
      shift
      ;;
    --npm-cache-dir)
      NPM_CACHE_DIR="${2:-}"
      shift 2
      ;;
    --skip-py-deps)
      SKIP_PY_DEPS="1"
      shift
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD="1"
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
    -*)
      FORWARD_ARGS+=("$@")
      break
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL_ARGS[@]} -gt 1 ]]; then
  echo "Expected at most one positional MIDI path, got ${#POSITIONAL_ARGS[@]}." >&2
  echo "Tip: place stepper-music args after your first unknown option or after '--'." >&2
  usage
  exit 2
fi

if [[ ${#POSITIONAL_ARGS[@]} -eq 1 ]]; then
  MIDI_PATH="${POSITIONAL_ARGS[0]}"
fi

if [[ -z "$MIDI_PATH" ]]; then
  MIDI_PATH="assets/midi/simple4.mid"
fi

if [[ "$UI_RENDER_MODE" != "prerender-30" && "$UI_RENDER_MODE" != "live" ]]; then
  echo "Invalid --ui-render-mode: $UI_RENDER_MODE (expected prerender-30 or live)" >&2
  exit 2
fi

if [[ "$MIDI_PATH" == "assets/midi/simple4.mid" && ! -f "$MIDI_PATH" ]]; then
  echo "Generating sample MIDI at $MIDI_PATH"
  python3 "$ROOT_DIR/scripts/generate_simple_midi.py"
fi

if [[ ! -f "$MIDI_PATH" ]]; then
  echo "MIDI file not found: $MIDI_PATH" >&2
  exit 2
fi

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "Creating Python venv at .venv"
  python3 -m venv .venv
fi

VENV_PY="$ROOT_DIR/.venv/bin/python"
PY_DEPS_STAMP="$ROOT_DIR/.venv/.music2_dev_deps.sig"

py_deps_signature() {
  cksum "$ROOT_DIR/pyproject.toml" | awk '{print $1 ":" $2}'
}

python_env_ready() {
  [[ -x "$VENV_PY" ]] || return 1
  "$VENV_PY" -c "import fastapi, mido, music2, serial, uvicorn, websockets" >/dev/null 2>&1
}

start_ui_prewarm() {
  if [[ "$UI_PREWARM" != "1" ]]; then
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    return 0
  fi

  local probe_host="$UI_HOST"
  if [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "::" || "$probe_host" == "[::]" ]]; then
    probe_host="127.0.0.1"
  fi

  (
    local origin="http://${probe_host}:${UI_PORT}"
    local health_url="${origin}/api/health"
    local try=0
    while [[ $try -lt 120 ]]; do
      if curl --silent --show-error --max-time 1 --output /dev/null "$health_url"; then
        break
      fi
      try=$((try + 1))
      sleep 0.1
    done

    # Best-effort warmup so first browser load doesn't wait on cold paths.
    curl --silent --max-time 3 --output /dev/null "${origin}/" || true
    curl --silent --max-time 3 --output /dev/null "${origin}/api/session" || true
    curl --silent --max-time 3 --output /dev/null "${origin}/api/viewer/session" || true
    curl --silent --max-time 3 --output /dev/null "${origin}/api/viewer/playhead" || true
    curl --silent --max-time 10 --output /dev/null "${origin}/api/viewer/timeline" || true
  ) >/dev/null 2>&1 &
}

if [[ "$SKIP_PY_DEPS" != "1" ]]; then
  current_py_sig="$(py_deps_signature)"
  installed_py_sig=""
  if [[ -f "$PY_DEPS_STAMP" ]]; then
    installed_py_sig="$(<"$PY_DEPS_STAMP")"
  fi

  if [[ "$installed_py_sig" == "$current_py_sig" ]] && python_env_ready; then
    echo "Python deps already ready; skipping install"
  else
    echo "Installing Python deps"
    "$VENV_PY" -m pip install -e '.[dev]'
    printf '%s\n' "$current_py_sig" > "$PY_DEPS_STAMP"
  fi
fi

if [[ "$SKIP_FRONTEND_BUILD" != "1" ]]; then
  if [[ -z "$NPM_CACHE_DIR" ]]; then
    NPM_CACHE_DIR="$ROOT_DIR/.npm-cache"
  fi
  mkdir -p "$NPM_CACHE_DIR"

  echo "Building frontend"
  pushd "$ROOT_DIR/ui/dashboard" >/dev/null
  export npm_config_cache="$NPM_CACHE_DIR"
  if ! npm install --no-audit --no-fund; then
    echo "npm install failed; clearing local npm cache and retrying once"
    rm -rf "$NPM_CACHE_DIR"
    mkdir -p "$NPM_CACHE_DIR"
    npm install --no-audit --no-fund
  fi
  npm run build
  popd >/dev/null
fi

TAILSCALE_IPS=()
if command -v tailscale >/dev/null 2>&1; then
  while IFS= read -r ip; do
    [[ -n "$ip" ]] && TAILSCALE_IPS+=("$ip")
  done < <(tailscale ip -4 2>/dev/null || true)
fi

echo
echo "Dashboard URLs:"
if [[ ${#TAILSCALE_IPS[@]} -gt 0 ]]; then
  for ip in "${TAILSCALE_IPS[@]}"; do
    echo "  http://${ip}:${UI_PORT}"
  done
else
  echo "  http://127.0.0.1:${UI_PORT}"
fi
echo

CMD=(
  "$VENV_PY" -m music2.cli run "$MIDI_PATH"
  --ui
  --ui-host "$UI_HOST"
  --ui-port "$UI_PORT"
  --ui-render-mode "$UI_RENDER_MODE"
)

if [[ "$HIGH_RATE" == "1" ]]; then
  CMD+=(--ui-high-rate)
fi

if [[ -n "$SERIAL_PORT" ]]; then
  CMD+=(--port "$SERIAL_PORT")
fi

if [[ ${#FORWARD_ARGS[@]} -gt 0 ]]; then
  CMD+=("${FORWARD_ARGS[@]}")
fi

echo "Running: ${CMD[*]}"

if [[ "$DRY_RUN" == "1" ]]; then
  exit 0
fi

start_ui_prewarm
exec "${CMD[@]}"
