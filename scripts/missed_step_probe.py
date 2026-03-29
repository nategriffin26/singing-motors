#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
import time
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

from music2.cli import _supports_home
from music2.compiler import compile_segments
from music2.config import HostConfig, load_config
from music2.instrument_profile import load_instrument_profile
from music2.midi import analyze_midi
from music2.models import CompileOptions
from music2.playback_modes import build_default_playback_program
from music2.serial_client import SerialClient


def _analyze_compile(cfg: HostConfig, midi_path: Path):
    instrument_profile = load_instrument_profile(cfg.instrument_profile_path)
    analysis, _tempo_map = analyze_midi(
        midi_path=midi_path,
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
    if not playback_program.playback_plan.event_groups:
        raise RuntimeError("compiled playback plan is empty")
    return analysis, compiled, playback_program.playback_plan


def _run_once(cfg: HostConfig, midi_path: Path) -> dict[str, object]:
    analysis, compiled, playback_plan = _analyze_compile(cfg, midi_path)
    expected_duration_us = int(round(analysis.duration_s * 1_000_000.0))
    compiled_duration_us = playback_plan.duration_total_us

    started_at = time.monotonic()
    with SerialClient(
        port=cfg.port,
        baudrate=cfg.baudrate,
        timeout_s=cfg.timeout_s,
        write_timeout_s=cfg.write_timeout_s,
        retries=cfg.retries,
    ) as client:
        hello = client.hello()
        feature_flags = int(hello.get("feature_flags", 0))
        home_supported = _supports_home(feature_flags)
        device_motors = int(hello.get("motor_count", 8))
        playback_motors = int(hello.get("playback_motor_count", device_motors))
        if cfg.connected_motors > device_motors:
            raise RuntimeError(
                f"config requests {cfg.connected_motors} motors but device reports {device_motors}"
            )
        if cfg.connected_motors > playback_motors:
            raise RuntimeError(
                f"config requests {cfg.connected_motors} playback motors but firmware reports {playback_motors}"
            )

        client.setup(
            motors=cfg.connected_motors,
            idle_mode=cfg.idle_mode,
            min_note=max(0, min(127, int(round(analysis.min_source_note or 0)))),
            max_note=max(0, min(127, int(round(analysis.max_source_note or 127)))),
            transpose=analysis.transpose_semitones,
            playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
            playback_launch_start_hz=cfg.playback_launch_start_hz,
            playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
        )
        client.stream_song_and_play(
            list(playback_plan.event_groups),
            lookahead_ms=cfg.lookahead_ms,
            lookahead_strategy=cfg.lookahead_strategy,
            lookahead_min_ms=cfg.lookahead_min_ms,
            lookahead_percentile=cfg.lookahead_percentile,
            lookahead_min_segments=cfg.lookahead_min_segments,
        )
        if home_supported:
            client.home(
                steps_per_rev=cfg.home_steps_per_rev,
                home_hz=cfg.home_hz,
                start_hz=cfg.home_start_hz,
                accel_hz_per_s=cfg.home_accel_hz_per_s,
            )
        metrics = client.metrics()
        status = client.status()

    elapsed_s = time.monotonic() - started_at
    return {
        "song": midi_path.name,
        "analysis": {
            "note_count": analysis.note_count,
            "max_polyphony": analysis.max_polyphony,
            "transpose_semitones": analysis.transpose_semitones,
        },
        "compile": {
            "event_group_count": playback_plan.event_group_count,
            "shadow_segment_count": playback_plan.shadow_segment_count,
            "duration_expected_us": expected_duration_us,
            "duration_compiled_us": compiled_duration_us,
            "duration_delta_us": compiled_duration_us - expected_duration_us,
            "adjacent_segments_merged": compiled.adjacent_segments_merged,
            "short_segments_absorbed": compiled.short_segments_absorbed,
        },
        "device": {
            "queue_capacity": int(hello.get("queue_capacity", 0)),
            "scheduler_tick_us": int(hello.get("scheduler_tick_us", 0)),
            "feature_flags": feature_flags,
            "home_supported": home_supported,
            "home_policy": {
                "steps_per_rev": cfg.home_steps_per_rev,
                "start_hz": cfg.home_start_hz,
                "target_hz": cfg.home_hz,
                "accel_hz_per_s": cfg.home_accel_hz_per_s,
            },
        },
        "playback": {
            "elapsed_s": elapsed_s,
            "metrics": asdict(metrics),
            "final_status": asdict(status),
            "event_groups_started_delta": metrics.event_groups_started - playback_plan.event_group_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repeat playback runs and capture missed-step risk metrics")
    parser.add_argument("midi_path", help="MIDI file to probe")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--runs", type=int, default=10, help="Number of playback runs")
    parser.add_argument("--sleep-s", type=float, default=0.25, help="Delay between runs")
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: .cache/missed_step_probe/<timestamp>.json)",
    )
    args = parser.parse_args()

    midi_path = Path(args.midi_path).expanduser().resolve()
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI not found: {midi_path}")
    if args.runs < 1:
        raise ValueError("--runs must be >= 1")

    cfg = load_config(args.config)
    started_at = datetime.now(timezone.utc)
    run_rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for run_idx in range(args.runs):
        run_id = run_idx + 1
        try:
            row = _run_once(cfg, midi_path)
            row["run"] = run_id
            run_rows.append(row)
        except Exception as exc:
            failures.append({"run": run_id, "error": str(exc)})
        if run_id < args.runs and args.sleep_s > 0:
            time.sleep(args.sleep_s)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at.isoformat(),
        "config_path": str(Path(args.config).expanduser().resolve()),
        "midi_path": str(midi_path),
        "runs_requested": args.runs,
        "runs_succeeded": len(run_rows),
        "runs_failed": len(failures),
        "results": run_rows,
        "failures": failures,
    }

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        out_path = Path(".cache") / "missed_step_probe" / f"{stamp}.json"
        out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
