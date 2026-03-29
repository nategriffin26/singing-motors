#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _ensure_music2_python() -> None:
    if os.environ.get("MUSIC2_SCRIPT_BOOTSTRAPPED") == "1":
        return
    try:
        import mido  # noqa: F401
        return
    except ImportError:
        pass

    music2_bin = shutil.which("music2")
    if not music2_bin:
        return
    first_line = Path(music2_bin).read_text(encoding="utf-8").splitlines()[0].strip()
    if not first_line.startswith("#!"):
        return
    target_python = first_line[2:]
    if not target_python or Path(target_python).resolve() == Path(sys.executable).resolve():
        return

    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing_pythonpath else f"{SRC_ROOT}:{existing_pythonpath}"
    env["MUSIC2_SCRIPT_BOOTSTRAPPED"] = "1"
    os.execve(target_python, [target_python, str(Path(__file__).resolve()), *sys.argv[1:]], env)


_ensure_music2_python()

from music2.compiler import compile_segments
from music2.config import load_config
from music2.instrument_profile import load_instrument_profile
from music2.midi import analyze_midi
from music2.models import CompileOptions
from music2.playback_modes import build_default_playback_program


def _pct(sorted_values: list[int], pct: int) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = (len(sorted_values) - 1) * (max(0, min(100, pct)) / 100.0)
    low = int(idx)
    high = min(len(sorted_values) - 1, low + 1)
    alpha = idx - low
    return int(round((1.0 - alpha) * sorted_values[low] + alpha * sorted_values[high]))


def analyze_one(midi_path: Path, cfg_path: Path) -> dict[str, object]:
    cfg = load_config(cfg_path)
    instrument_profile = load_instrument_profile(cfg.instrument_profile_path)
    analysis, _tempo_map = analyze_midi(
        midi_path,
        min_freq_hz=cfg.min_freq_hz,
        max_freq_hz=cfg.max_freq_hz,
        transpose_override=cfg.transpose_override,
        auto_transpose=cfg.auto_transpose,
    )
    compiled = compile_segments(
        analysis.notes,
        CompileOptions(
            connected_motors=cfg.connected_motors,
            idle_mode=cfg.idle_mode,
            overflow_mode=cfg.overflow_mode,
            sticky_gap_s=cfg.sticky_gap_ms / 1000.0,
            flip_direction_on_note_change=cfg.flip_direction_on_note_change,
            suppress_tight_direction_flips=cfg.suppress_tight_direction_flips,
            direction_flip_safety_margin_ms=cfg.direction_flip_safety_margin_ms,
        ),
        instrument_profile=instrument_profile,
    )
    playback_program = build_default_playback_program(analysis=analysis, compiled=compiled)
    playback_plan = playback_program.playback_plan
    durations = [max(1, group.delta_us) for group in playback_plan.event_groups]
    durations_sorted = sorted(durations)
    avg_active = (
        sum(sum(1 for freq in segment.motor_freq_hz if freq > 0.0) for segment in playback_plan.shadow_segments)
        / max(1, len(playback_plan.shadow_segments))
    )
    return {
        "midi": str(midi_path),
        "note_count": analysis.note_count,
        "max_polyphony": analysis.max_polyphony,
        "transpose_semitones": analysis.transpose_semitones,
        "clamped_note_count": analysis.clamped_note_count,
        "segments": {
            "count": playback_plan.shadow_segment_count,
            "min_us": min(durations_sorted) if durations_sorted else 0,
            "median_us": int(statistics.median(durations_sorted)) if durations_sorted else 0,
            "p90_us": _pct(durations_sorted, 90),
            "p95_us": _pct(durations_sorted, 95),
            "max_us": max(durations_sorted) if durations_sorted else 0,
            "short_counts": {
                "<=500us": sum(1 for value in durations if value <= 500),
                "<=1ms": sum(1 for value in durations if value <= 1_000),
                "<=2ms": sum(1 for value in durations if value <= 2_000),
            },
            "total_us": sum(durations),
            "expected_us": int(round(analysis.duration_s * 1_000_000.0)),
        },
        "allocation": {
            "stolen": compiled.stolen_note_count,
            "dropped": compiled.dropped_note_count,
            "retained": analysis.note_count - compiled.dropped_note_count,
            "avg_active_motors": avg_active,
        },
        "playback_plan": {
            "event_group_count": playback_plan.event_group_count,
            "shadow_segment_count": playback_plan.shadow_segment_count,
            "motor_change_count": playback_plan.motor_change_count,
            "duration_total_us": playback_plan.duration_total_us,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate compile/playback quality baseline metrics")
    parser.add_argument("midi", nargs="+", help="MIDI files to analyze")
    parser.add_argument("--config", default="config.toml", help="Path to music2 config")
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    args = parser.parse_args()

    cfg_path = Path(args.config).expanduser().resolve()
    report = {
        "config": str(cfg_path),
        "results": [analyze_one(Path(m).expanduser().resolve(), cfg_path) for m in args.midi],
    }

    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
