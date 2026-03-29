#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

from music2.cli import _supports_home
from music2.compiler import compile_segments
from music2.config import HostConfig, load_config
from music2.midi import analyze_midi
from music2.models import CompileOptions
from music2.serial_client import SerialClient

_PROFILE_OVERRIDES: dict[str, dict[str, object]] = {
    "clean": {
        "idle_mode": "idle",
        "lookahead_strategy": "p95",
        "lookahead_ms": 1200,
        "lookahead_min_ms": 400,
        "lookahead_min_segments": 24,
        "segment_floor_us": 25,
        "segment_floor_pulse_budget": 0.25,
    },
    "safe": {
        "idle_mode": "idle",
        "lookahead_strategy": "p95",
        "lookahead_ms": 1500,
        "lookahead_min_ms": 600,
        "lookahead_min_segments": 32,
        "segment_floor_us": 25,
        "segment_floor_pulse_budget": 0.25,
    },
    "expressive": {
        "idle_mode": "idle",
        "lookahead_strategy": "p90",
        "lookahead_ms": 1000,
        "lookahead_min_ms": 250,
        "lookahead_min_segments": 20,
        "segment_floor_us": 10,
        "segment_floor_pulse_budget": 0.15,
    },
    "quiet-hw": {
        "idle_mode": "idle",
        "lookahead_strategy": "p95",
        "lookahead_ms": 1400,
        "lookahead_min_ms": 500,
        "lookahead_min_segments": 28,
        "max_freq_hz": 650.0,
        "segment_floor_us": 40,
        "segment_floor_pulse_budget": 0.40,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configure(base_cfg: HostConfig, *, profile: str | None, transpose: int | None) -> HostConfig:
    cfg = base_cfg
    if profile is not None:
        overrides = _PROFILE_OVERRIDES[profile]
        cfg = replace(cfg, **overrides)
    if transpose is not None:
        cfg = replace(cfg, transpose_override=transpose, auto_transpose=False)
    return cfg


def _compile(cfg: HostConfig, midi_path: Path):
    analysis = analyze_midi(
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
            segment_floor_us=cfg.segment_floor_us,
            segment_floor_pulse_budget=cfg.segment_floor_pulse_budget,
        ),
    )
    return analysis, compiled


def _run_once(cfg: HostConfig, midi_path: Path) -> dict[str, object]:
    analysis, compiled = _compile(cfg, midi_path)
    expected_duration_us = int(round(analysis.duration_s * 1_000_000.0))
    compiled_duration_us = sum(seg.duration_us for seg in compiled.segments)

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
        )
        client.stream_song_and_play(
            compiled.segments,
            lookahead_ms=cfg.lookahead_ms,
            lookahead_strategy=cfg.lookahead_strategy,
            lookahead_min_ms=cfg.lookahead_min_ms,
            lookahead_percentile=cfg.lookahead_percentile,
            lookahead_min_segments=cfg.lookahead_min_segments,
        )
        playback_metrics = client.metrics()
        playback_status = client.status()
        post_home_metrics = None
        if home_supported:
            client.home(
                steps_per_rev=cfg.home_steps_per_rev,
                home_hz=cfg.home_hz,
                start_hz=cfg.home_start_hz,
                accel_hz_per_s=cfg.home_accel_hz_per_s,
            )
            post_home_metrics = client.metrics()

        final_status = client.status()

    return {
        "analysis": {
            "note_count": analysis.note_count,
            "max_polyphony": analysis.max_polyphony,
            "duration_s": analysis.duration_s,
            "transpose_semitones": analysis.transpose_semitones,
        },
        "compile": {
            "segment_count": len(compiled.segments),
            "duration_expected_us": expected_duration_us,
            "duration_compiled_us": compiled_duration_us,
            "duration_delta_us": compiled_duration_us - expected_duration_us,
            "adjacent_segments_merged": compiled.adjacent_segments_merged,
            "short_segments_absorbed": compiled.short_segments_absorbed,
            "stolen_note_count": compiled.stolen_note_count,
            "dropped_note_count": compiled.dropped_note_count,
        },
        "device": {
            "queue_capacity": int(hello.get("queue_capacity", 0)),
            "scheduler_tick_us": int(hello.get("scheduler_tick_us", 0)),
            "feature_flags": feature_flags,
            "home_supported": home_supported,
        },
        "run": {
            "elapsed_s": round(time.monotonic() - started, 3),
            "status_soft_fail_count": client.status_soft_fail_count,
            "metrics": asdict(playback_metrics),
            "metrics_after_home": asdict(post_home_metrics) if post_home_metrics is not None else None,
            "playback_status": asdict(playback_status),
            "final_status": asdict(final_status),
            "segments_started_delta": playback_metrics.segments_started - len(compiled.segments),
        },
    }


def _summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    if not rows:
        return {"runs": 0}

    keys = [
        "underrun_count",
        "pulse_edge_drop_count",
        "pulse_late_max_us",
        "pulse_timebase_rebase_count",
        "pulse_timebase_rebase_lost_us",
        "pulse_target_update_count",
        "pulse_ramp_change_count",
        "pulse_stop_after_ramp_count",
        "scheduling_late_max_us",
        "timer_empty_events",
        "timer_restart_count",
        "queue_high_water",
        "playback_slew_clamp_count",
    ]

    summary: dict[str, object] = {"runs": len(rows), "metrics": {}}
    for key in keys:
        values = [int(row["run"]["metrics"][key]) for row in rows]  # type: ignore[index]
        summary["metrics"][key] = {
            "min": min(values),
            "max": max(values),
            "mean": round(statistics.fmean(values), 3),
            "median": int(round(statistics.median(values))),
            "values": values,
        }

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture repeatable hardware playback metrics snapshots")
    parser.add_argument("--midi", required=True, help="MIDI input path")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--transpose", type=int, default=None, help="Force transpose override")
    parser.add_argument("--profile", choices=sorted(_PROFILE_OVERRIDES.keys()), default=None)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--sleep-s", type=float, default=0.25)
    parser.add_argument("--out", default=None, help="Output JSON path")
    args = parser.parse_args()

    midi_path = Path(args.midi).expanduser().resolve()
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI not found: {midi_path}")
    if args.runs < 1:
        raise ValueError("--runs must be >= 1")

    base_cfg = load_config(args.config)
    cfg = _configure(base_cfg, profile=args.profile, transpose=args.transpose)

    started_at = _now_iso()
    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for run_idx in range(args.runs):
        run_id = run_idx + 1
        try:
            row = _run_once(cfg, midi_path)
            row["run_id"] = run_id
            rows.append(row)
        except Exception as exc:
            failures.append({"run_id": run_id, "error": str(exc)})
        if run_id < args.runs and args.sleep_s > 0:
            time.sleep(args.sleep_s)

    payload = {
        "generated_at": _now_iso(),
        "started_at": started_at,
        "config_path": str(Path(args.config).expanduser().resolve()),
        "midi_path": str(midi_path),
        "profile": args.profile,
        "transpose": args.transpose,
        "runs_requested": args.runs,
        "runs_succeeded": len(rows),
        "runs_failed": len(failures),
        "results": rows,
        "failures": failures,
        "summary": _summarize(rows),
    }

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = (Path(".cache") / "rca_host" / f"{stamp}-hw_snapshot.json").resolve()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
