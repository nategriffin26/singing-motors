#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from music2.cli import _supports_home
from music2.compiler import compile_segments
from music2.config import HostConfig, load_config
from music2.midi import analyze_midi
from music2.models import CompileOptions
from music2.serial_client import SerialClient


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_compile(cfg: HostConfig, midi_path: Path, *, flip_direction_on_note_change: bool):
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
            flip_direction_on_note_change=flip_direction_on_note_change,
            suppress_tight_direction_flips=cfg.suppress_tight_direction_flips,
            direction_flip_safety_margin_ms=cfg.direction_flip_safety_margin_ms,
        ),
    )
    return analysis, compiled


def _slice_event_groups(event_groups, *, excerpt_us: int | None):
    if excerpt_us is None or excerpt_us <= 0:
        return list(event_groups), None

    total_us = 0
    out = []
    for group in event_groups:
        out.append(group)
        total_us += group.delta_us
        if total_us >= excerpt_us:
            break
    return out, total_us


def _run_once(
    cfg: HostConfig,
    midi_path: Path,
    *,
    flip_direction_on_note_change: bool,
    excerpt_us: int | None,
    home_after: bool,
) -> dict[str, object]:
    analysis, compiled = _build_compile(
        cfg,
        midi_path,
        flip_direction_on_note_change=flip_direction_on_note_change,
    )
    event_groups, excerpt_total_us = _slice_event_groups(compiled.event_groups, excerpt_us=excerpt_us)
    if not event_groups:
        raise RuntimeError("event-group plan is empty")

    started = time.monotonic()
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
            event_groups,
            lookahead_ms=cfg.lookahead_ms,
            lookahead_strategy=cfg.lookahead_strategy,
            lookahead_min_ms=cfg.lookahead_min_ms,
            lookahead_percentile=cfg.lookahead_percentile,
            lookahead_min_segments=cfg.lookahead_min_segments,
        )
        playback_metrics = client.metrics()
        playback_status = client.status()
        metrics_after_home = None

        if home_after and home_supported:
            client.home(
                steps_per_rev=cfg.home_steps_per_rev,
                home_hz=cfg.home_hz,
                start_hz=cfg.home_start_hz,
                accel_hz_per_s=cfg.home_accel_hz_per_s,
            )
            metrics_after_home = client.metrics()

        final_status = client.status()

    flip_count = sum(
        1
        for group in event_groups
        for change in group.changes
        if change.flip_before_restart
    )
    return {
        "analysis": {
            "note_count": analysis.note_count,
            "max_polyphony": analysis.max_polyphony,
            "duration_s": analysis.duration_s,
            "transpose_semitones": analysis.transpose_semitones,
            "clamped_note_count": analysis.clamped_note_count,
        },
        "compile": {
            "event_group_count": len(event_groups),
            "full_event_group_count": len(compiled.event_groups),
            "segment_count": len(compiled.segments),
            "motor_change_count": compiled.motor_change_count,
            "direction_flip_requested_count": compiled.direction_flip_requested_count,
            "tight_boundary_warning_count": compiled.tight_boundary_warning_count,
            "stolen_note_count": compiled.stolen_note_count,
            "dropped_note_count": compiled.dropped_note_count,
            "excerpt_total_us": excerpt_total_us,
            "excerpt_flip_count": flip_count,
        },
        "device": {
            "queue_capacity": int(hello.get("queue_capacity", 0)),
            "scheduler_tick_us": int(hello.get("scheduler_tick_us", 0)),
            "feature_flags": feature_flags,
            "home_supported": home_supported,
            "playback_motor_count": int(hello.get("playback_motor_count", 0)),
        },
        "run": {
            "elapsed_s": round(time.monotonic() - started, 3),
            "status_soft_fail_count": client.status_soft_fail_count,
            "metrics": asdict(playback_metrics),
            "metrics_after_home": asdict(metrics_after_home) if metrics_after_home is not None else None,
            "playback_status": asdict(playback_status),
            "final_status": asdict(final_status),
            "event_groups_started_delta": playback_metrics.event_groups_started - len(event_groups),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture playback-wave-engine metrics for the active event-group song path"
    )
    parser.add_argument("--midi", required=True, help="MIDI input path")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument(
        "--excerpt-us",
        type=int,
        default=None,
        help="Optional excerpt duration in microseconds",
    )
    parser.add_argument(
        "--flip-direction-on-note-change",
        dest="flip_direction_on_note_change",
        action="store_true",
        help="Override compile to enable direction flips",
    )
    parser.add_argument(
        "--no-flip-direction-on-note-change",
        dest="flip_direction_on_note_change",
        action="store_false",
        help="Override compile to disable direction flips",
    )
    parser.add_argument(
        "--home-after",
        dest="home_after",
        action="store_true",
        default=False,
        help="Run HOME after playback and capture post-home metrics",
    )
    parser.add_argument("--out", default=None, help="Output JSON path")
    parser.set_defaults(flip_direction_on_note_change=None)
    args = parser.parse_args()

    midi_path = Path(args.midi).expanduser().resolve()
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI not found: {midi_path}")

    cfg = load_config(args.config)
    flip_direction_on_note_change = (
        cfg.flip_direction_on_note_change
        if args.flip_direction_on_note_change is None
        else bool(args.flip_direction_on_note_change)
    )
    row = _run_once(
        cfg,
        midi_path,
        flip_direction_on_note_change=flip_direction_on_note_change,
        excerpt_us=args.excerpt_us,
        home_after=bool(args.home_after),
    )
    payload = {
        "generated_at": _now_iso(),
        "config_path": str(Path(args.config).expanduser().resolve()),
        "midi_path": str(midi_path),
        "flip_direction_on_note_change": flip_direction_on_note_change,
        "excerpt_us": args.excerpt_us,
        "home_after": bool(args.home_after),
        "result": row,
    }

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = (Path(".cache") / "rca_wave" / f"{stamp}-playback-wave.json").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
