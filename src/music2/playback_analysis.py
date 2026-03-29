from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .arrangement_report import build_arrangement_report
from .compiler import compile_segments
from .config import HostConfig
from .instrument_profile import InstrumentProfile, load_instrument_profile
from .midi import TempoMap, analyze_midi
from .models import CompileOptions, CompileReport, MidiAnalysisReport
from .playback_modes import build_default_playback_program
from .playback_program import PlaybackProgram


@dataclass(frozen=True)
class PreparedPlaybackArtifacts:
    instrument_profile: InstrumentProfile
    analysis: MidiAnalysisReport
    compiled: CompileReport
    playback_program: PlaybackProgram
    arrangement_report: object
    avg_active: float
    tempo_map: TempoMap


def prepare_playback_artifacts(
    *,
    cfg: HostConfig,
    midi_path: str | Path,
    instrument_profile: InstrumentProfile | None = None,
) -> PreparedPlaybackArtifacts:
    midi_file = Path(midi_path).expanduser().resolve()
    profile = instrument_profile or load_instrument_profile(cfg.instrument_profile_path)
    analysis, tempo_map = analyze_midi(
        midi_path=midi_file,
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
            melody_doubling_enabled=cfg.double_melody,
            flip_direction_on_note_change=cfg.flip_direction_on_note_change,
            suppress_tight_direction_flips=cfg.suppress_tight_direction_flips,
            direction_flip_safety_margin_ms=cfg.direction_flip_safety_margin_ms,
            direction_flip_cooldown_ms=cfg.direction_flip_cooldown_ms,
            playback_run_accel_hz_per_s=cfg.playback_run_accel_hz_per_s,
            playback_launch_start_hz=cfg.playback_launch_start_hz,
            playback_launch_accel_hz_per_s=cfg.playback_launch_accel_hz_per_s,
            playback_launch_crossover_hz=cfg.playback_launch_crossover_hz,
        ),
        instrument_profile=profile,
    )
    playback_program = build_default_playback_program(analysis=analysis, compiled=compiled)
    arrangement_report = build_arrangement_report(
        analysis=analysis,
        compiled=compiled,
        instrument_profile=profile,
    )
    avg_active = (
        sum(sum(1 for freq in segment.motor_freq_hz if freq > 0.0) for segment in compiled.segments)
        / max(1, len(compiled.segments))
    )
    return PreparedPlaybackArtifacts(
        instrument_profile=profile,
        analysis=analysis,
        compiled=compiled,
        playback_program=playback_program,
        arrangement_report=arrangement_report,
        avg_active=avg_active,
        tempo_map=tempo_map,
    )
