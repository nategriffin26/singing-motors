from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from ..artifacts import (
    collect_provenance,
    ensure_dir,
    make_bundle_id,
    write_json,
    write_jsonl,
)
from ..config import HostConfig, load_config
from ..playback_analysis import PreparedPlaybackArtifacts, prepare_playback_artifacts
from .corpus import BenchmarkCase, BenchmarkCorpus, load_benchmark_corpus
from .schema import bench_bundle_dir, write_bench_manifest

BenchmarkMode = Literal["analyze-only", "hardware-run", "synthetic-run"]


@dataclass(frozen=True)
class BenchmarkRunBundle:
    bundle_id: str
    bundle_dir: Path
    case: BenchmarkCase
    mode: BenchmarkMode
    analyze_path: Path
    manifest_path: Path
    run_metrics_path: Path | None = None


def _arrangement_report_dict(report: Any) -> dict[str, int | float]:
    return {
        "considered_note_count": report.considered_note_count,
        "preserved_note_count": report.preserved_note_count,
        "dropped_note_count": report.dropped_note_count,
        "truncated_note_count": report.truncated_note_count,
        "melody_note_count": report.melody_note_count,
        "preserved_melody_note_count": report.preserved_melody_note_count,
        "dropped_melody_note_count": report.dropped_melody_note_count,
        "bass_note_count": report.bass_note_count,
        "preserved_bass_note_count": report.preserved_bass_note_count,
        "dropped_bass_note_count": report.dropped_bass_note_count,
        "inner_note_count": report.inner_note_count,
        "dropped_inner_note_count": report.dropped_inner_note_count,
        "octave_retargeted_note_count": report.octave_retargeted_note_count,
        "coalesced_transition_count": report.coalesced_transition_count,
        "requested_reversal_count": report.requested_reversal_count,
        "applied_reversal_count": report.applied_reversal_count,
        "avoided_reversal_count": report.avoided_reversal_count,
        "tight_reversal_window_count": report.tight_reversal_window_count,
        "motor_preferred_band_violation_count": report.motor_preferred_band_violation_count,
        "motor_resonance_band_hit_count": report.motor_resonance_band_hit_count,
        "motor_avoid_band_hit_count": report.motor_avoid_band_hit_count,
        "motor_comfort_violation_count": report.motor_comfort_violation_count,
        "weighted_musical_loss": report.weighted_musical_loss,
    }


def _analyze_payload(case: BenchmarkCase, prepared: PreparedPlaybackArtifacts) -> dict[str, Any]:
    playback_plan = prepared.playback_program.playback_plan
    durations_us = [max(1, group.delta_us) for group in playback_plan.event_groups]
    sorted_durations = sorted(durations_us)
    total_us = sum(durations_us)
    return {
        "case_id": case.case_id,
        "case_category": case.category,
        "case_suite": case.suite,
        "midi_path": str(case.midi_path),
        "golden_window_s": case.golden_window_s,
        "notes": {
            "count": prepared.analysis.note_count,
            "max_polyphony": prepared.analysis.max_polyphony,
            "transpose_semitones": prepared.analysis.transpose_semitones,
            "clamped_note_count": prepared.analysis.clamped_note_count,
            "duration_s": prepared.analysis.duration_s,
        },
        "instrument": {
            "profile_name": prepared.instrument_profile.name,
            "profile_version": prepared.instrument_profile.profile_version,
            "profile_path": str(prepared.instrument_profile.source_path or ""),
            "motor_count": prepared.instrument_profile.motor_count,
            "active_motor_count": prepared.compiled.connected_motors,
        },
        "arrangement": _arrangement_report_dict(prepared.arrangement_report),
        "allocation": {
            "policy": prepared.compiled.overflow_mode,
            "connected_motors": prepared.compiled.connected_motors,
            "stolen_note_count": prepared.compiled.stolen_note_count,
            "dropped_note_count": prepared.compiled.dropped_note_count,
            "truncated_note_count": prepared.compiled.truncated_note_count,
            "zero_length_note_count": prepared.compiled.zero_length_note_count,
            "adjacent_segments_merged": prepared.compiled.adjacent_segments_merged,
            "short_segments_absorbed": prepared.compiled.short_segments_absorbed,
            "direction_flip_requested_count": prepared.compiled.direction_flip_requested_count,
            "direction_flip_applied_count": prepared.compiled.direction_flip_applied_count,
            "direction_flip_suppressed_count": prepared.compiled.direction_flip_suppressed_count,
            "tight_boundary_warning_count": prepared.compiled.tight_boundary_warning_count,
        },
        "playback_plan": {
            "event_group_count": playback_plan.event_group_count,
            "motor_change_count": playback_plan.motor_change_count,
            "duration_total_us": total_us,
            "min_delta_us": min(sorted_durations) if sorted_durations else 0,
            "median_delta_us": sorted_durations[len(sorted_durations) // 2] if sorted_durations else 0,
            "max_delta_us": max(sorted_durations) if sorted_durations else 0,
            "avg_active_motors": prepared.avg_active,
        },
    }


def _synthetic_run_payload(prepared: PreparedPlaybackArtifacts) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    playback_plan = prepared.playback_program.playback_plan
    status_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    plan_time_us = 0
    total = max(1, playback_plan.event_group_count)
    for idx, group in enumerate(playback_plan.event_groups, start=1):
        plan_time_us += max(0, group.delta_us)
        queue_depth = max(0, total - idx)
        active_motors = sum(1 for freq in (playback_plan.shadow_segments[min(idx - 1, len(playback_plan.shadow_segments) - 1)].motor_freq_hz if playback_plan.shadow_segments else ()) if freq > 0.0)
        status_rows.append(
            {
                "captured_monotonic_s": round(plan_time_us / 1_000_000.0, 6),
                "sent_segments": idx,
                "total_segments": total,
                "queue_depth": queue_depth,
                "queue_capacity": total,
                "credits": queue_depth,
                "active_motors": active_motors,
                "playhead_us": plan_time_us,
                "playing": idx < total,
                "stream_open": True,
                "stream_end_received": idx >= total,
            }
        )
        metrics_rows.append(
            {
                "captured_monotonic_s": round(plan_time_us / 1_000_000.0, 6),
                "sent_segments": idx,
                "total_segments": total,
                "queue_depth": queue_depth,
                "credits": queue_depth,
                "underrun_count": 0,
                "queue_high_water": total,
                "scheduling_late_max_us": 0,
                "crc_parse_errors": 0,
                "rx_parse_errors": 0,
                "timer_empty_events": 0,
                "timer_restart_count": 0,
                "event_groups_started": idx,
                "scheduler_guard_hits": 0,
                "control_late_max_us": 0,
                "control_overrun_count": 0,
                "wave_period_update_count": idx,
                "motor_start_count": idx,
                "motor_stop_count": max(0, idx - 1),
                "flip_restart_count": 0,
                "launch_guard_count": 0,
                "engine_fault_count": 0,
                "engine_fault_mask": 0,
            }
        )
    run_metrics = {
        "queue_capacity": total,
        "capabilities": {
            "protocol_version": 2,
            "feature_flags": 0x73,
            "queue_capacity": total,
            "scheduler_tick_us": 25,
            "device_motor_count": prepared.compiled.connected_motors,
            "playback_motor_count": prepared.compiled.connected_motors,
        },
        "last_progress": status_rows[-1] if status_rows else None,
        "metrics": metrics_rows[-1] if metrics_rows else {},
    }
    return status_rows, metrics_rows, run_metrics


class BenchmarkRunner:
    def __init__(
        self,
        *,
        cfg: HostConfig,
        corpus: BenchmarkCorpus | None = None,
        cache_root: str | Path = ".cache/bench",
        project_root: str | Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.corpus = corpus or load_benchmark_corpus()
        self.cache_root = ensure_dir(cache_root)
        self.project_root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()

    @classmethod
    def from_config(
        cls,
        *,
        config_path: str | Path = "config.toml",
        corpus_path: str | Path | None = None,
        cache_root: str | Path = ".cache/bench",
    ) -> "BenchmarkRunner":
        cfg = load_config(config_path)
        corpus = load_benchmark_corpus(corpus_path) if corpus_path is not None else load_benchmark_corpus()
        return cls(cfg=cfg, corpus=corpus, cache_root=cache_root)

    def select_cases(
        self,
        *,
        case_id: str | None = None,
        category: str | None = None,
        quick: bool = False,
        full: bool = False,
    ) -> tuple[BenchmarkCase, ...]:
        if case_id is not None:
            return (self.corpus.get_case(case_id),)
        if category is not None:
            return self.corpus.filter(category=category)
        if full:
            return self.corpus.cases
        if quick or not full:
            return self.corpus.filter(suite="default")
        return self.corpus.cases

    def run_case(
        self,
        case: BenchmarkCase,
        *,
        mode: BenchmarkMode = "analyze-only",
        repeat_index: int = 1,
    ) -> BenchmarkRunBundle:
        prepared = prepare_playback_artifacts(cfg=self.cfg, midi_path=case.midi_path)
        bundle_id = make_bundle_id("bench", f"{case.case_id}-r{repeat_index}")
        bundle_dir = bench_bundle_dir(self.cache_root, bundle_id)
        analyze_payload = _analyze_payload(case, prepared)
        analyze_path = write_json(bundle_dir / "analyze.json", analyze_payload)

        manifest: dict[str, Any] = {
            "bundle_id": bundle_id,
            "bundle_type": "benchmark",
            "mode": mode,
            "repeat_index": repeat_index,
            "case": {
                "case_id": case.case_id,
                "display_name": case.display_name,
                "category": case.category,
                "suite": case.suite,
                "midi_path": str(case.midi_path),
                "expected_runtime_s": case.expected_runtime_s,
                "hardware_setup": case.hardware_setup,
                "unattended_safe": case.unattended_safe,
                "metrics_of_interest": list(case.metrics_of_interest),
                "golden_window_s": case.golden_window_s,
            },
            "provenance": collect_provenance(
                cwd=self.project_root,
                instrument_profile_path=self.cfg.instrument_profile_path,
                extra_files={"midi": case.midi_path},
            ),
        }

        run_metrics_path: Path | None = None
        if mode == "hardware-run":
            from ..hardware_capture import execute_playback_plan_capture

            capture = execute_playback_plan_capture(
                cfg=self.cfg,
                playback_plan=prepared.playback_program.playback_plan,
                min_note=max(0, int(prepared.analysis.min_source_note or 0)),
                max_note=max(0, int(prepared.analysis.max_source_note or 127)),
                transpose=prepared.analysis.transpose_semitones,
            )
            write_jsonl(bundle_dir / "status_trace.jsonl", list(capture.status_trace))
            write_jsonl(bundle_dir / "metrics_trace.jsonl", list(capture.metrics_trace))
            run_metrics_path = write_json(
                bundle_dir / "run_metrics.json",
                {
                    "queue_capacity": capture.execution.queue_capacity,
                    "capabilities": asdict(capture.execution.capabilities),
                    "last_progress": asdict(capture.execution.last_progress) if capture.execution.last_progress is not None else None,
                    "metrics": asdict(capture.execution.metrics),
                    "started_at_monotonic": capture.started_at_monotonic,
                    "completed_at_monotonic": capture.completed_at_monotonic,
                },
            )
            (bundle_dir / "stdout.txt").write_text("", encoding="utf-8")
        elif mode == "synthetic-run":
            status_rows, metrics_rows, run_metrics = _synthetic_run_payload(prepared)
            write_jsonl(bundle_dir / "status_trace.jsonl", status_rows)
            write_jsonl(bundle_dir / "metrics_trace.jsonl", metrics_rows)
            run_metrics_path = write_json(bundle_dir / "run_metrics.json", run_metrics)
            (bundle_dir / "stdout.txt").write_text("synthetic benchmark run\n", encoding="utf-8")
        else:
            write_json(bundle_dir / "run_metrics.json", {})
            write_jsonl(bundle_dir / "status_trace.jsonl", [])
            write_jsonl(bundle_dir / "metrics_trace.jsonl", [])
            (bundle_dir / "stdout.txt").write_text("", encoding="utf-8")

        manifest_path = write_bench_manifest(bundle_dir, manifest)
        return BenchmarkRunBundle(
            bundle_id=bundle_id,
            bundle_dir=bundle_dir,
            case=case,
            mode=mode,
            analyze_path=analyze_path,
            manifest_path=manifest_path,
            run_metrics_path=run_metrics_path,
        )

    def run_suite(
        self,
        *,
        case_id: str | None = None,
        category: str | None = None,
        mode: BenchmarkMode = "analyze-only",
        repeat: int = 1,
        quick: bool = False,
        full: bool = False,
    ) -> tuple[BenchmarkRunBundle, ...]:
        bundles: list[BenchmarkRunBundle] = []
        for case in self.select_cases(case_id=case_id, category=category, quick=quick, full=full):
            for repeat_index in range(1, max(1, repeat) + 1):
                bundles.append(self.run_case(case, mode=mode, repeat_index=repeat_index))
        return tuple(bundles)


def override_config(
    cfg: HostConfig,
    *,
    port: str | None = None,
    instrument_profile_path: str | None = None,
) -> HostConfig:
    updated = cfg
    if port is not None:
        updated = replace(updated, port=port)
    if instrument_profile_path is not None:
        updated = replace(updated, instrument_profile_path=instrument_profile_path)
    return updated
