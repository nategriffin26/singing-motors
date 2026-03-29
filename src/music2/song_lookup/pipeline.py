from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

from ..arrangement_report import build_arrangement_report
from ..artifacts import ensure_dir, safe_slug, write_json
from ..compiler import compile_segments
from ..config import HostConfig
from ..instrument_profile import InstrumentProfile, load_instrument_profile
from ..midi import analyze_midi
from ..models import CompileOptions
from ..transcribe.pipeline import convert_mp3_to_dual_midi
from ..transcribe.types import ConversionConfig
from .cache import SongLookupCache
from .convert import acquire_source_hit, ensure_midi_artifact, write_motor_safe_midi
from .query import build_default_adapters
from .rank import order_ranked_candidates, score_candidate
from .sources.base import UrlFetcher
from .types import CandidateAnalysis, LookupResult, RankedCandidate, SongQuery


def find_song(
    query: SongQuery,
    *,
    cfg: HostConfig,
    cache_root: str | Path = ".cache/song_lookup",
    out_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
    fetcher: UrlFetcher | None = None,
    download_best: bool = False,
    download_top: int = 0,
) -> LookupResult:
    repo_path = Path(repo_root or Path(__file__).resolve().parents[3]).resolve()
    cache = SongLookupCache(cache_root)
    results_dir = ensure_dir(out_dir or cache.query_output_dir(query.title, query.artist))
    instrument_profile = load_instrument_profile(cfg.instrument_profile_path)

    adapters = build_default_adapters(repo_root=repo_path, query=query, fetcher=fetcher)
    source_hits = []
    warnings: list[str] = []
    for adapter in adapters:
        try:
            source_hits.extend(adapter.search(query, max_results=query.max_candidates))
        except Exception as exc:
            warnings.append(f"{getattr(adapter, 'source_name', adapter.__class__.__name__)} search failed: {exc}")

    deduped_hits = []
    seen = set()
    for hit in source_hits:
        dedupe_key = (
            str(Path(hit.local_path).expanduser().resolve()) if hit.local_path else None,
            hit.download_url or hit.url,
            hit.title.strip().lower(),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped_hits.append(hit)

    ranked: list[RankedCandidate] = []
    analysis_limit = max(query.max_candidates * 4, query.max_candidates)
    for hit in deduped_hits[:analysis_limit]:
        artifact = acquire_source_hit(hit, cache=cache, fetcher=fetcher)
        artifact = ensure_midi_artifact(artifact, cache=cache)
        analysis = None
        candidate_warnings = list(artifact.warnings)
        if artifact.local_path is not None and artifact.local_path.suffix.lower() in {".mid", ".midi"}:
            try:
                analysis = _analyze_candidate(
                    artifact.local_path,
                    cfg=cfg,
                    instrument_profile=instrument_profile,
                    output_dir=results_dir,
                )
            except Exception as exc:
                candidate_warnings.append(f"analysis failed: {exc}")
        elif artifact.local_path is not None and artifact.local_path.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
            if query.allow_audio_fallback:
                try:
                    analysis = _transcribe_audio_candidate(
                        artifact.local_path,
                        cfg=cfg,
                        output_dir=results_dir,
                        instrument_profile=instrument_profile,
                    )
                except Exception as exc:
                    candidate_warnings.append(f"audio fallback failed: {exc}")
            else:
                candidate_warnings.append("audio candidate available but audio fallback disabled")
        score, breakdown, reason = score_candidate(query=query, hit=hit, artifact=artifact, analysis=analysis)
        status = "ready" if analysis is not None else ("warning" if candidate_warnings else "error")
        ranked.append(
            RankedCandidate(
                source_hit=hit,
                artifact=artifact,
                analysis=analysis,
                score=score,
                score_breakdown=breakdown,
                recommendation_reason=reason,
                status=status,
                warnings=tuple(candidate_warnings),
            )
        )

    ordered = tuple(order_ranked_candidates(ranked)[: query.max_candidates])
    recommended_index = 0 if ordered else None
    result = LookupResult(
        query=query,
        candidates=ordered,
        recommended_index=recommended_index,
        output_dir=results_dir,
        warnings=tuple(warnings),
    )
    _export_ranked_candidates(result, output_dir=results_dir, download_best=download_best, download_top=download_top)
    write_json(cache.report_path(stem=cache.query_slug(query.title, query.artist)), result.to_json_dict())
    return result


def _analyze_candidate(
    midi_path: Path,
    *,
    cfg: HostConfig,
    instrument_profile: InstrumentProfile,
    output_dir: Path,
) -> CandidateAnalysis:
    analysis, tempo_map = analyze_midi(
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
        instrument_profile=instrument_profile,
    )
    arrangement = build_arrangement_report(
        analysis=analysis,
        compiled=compiled,
        instrument_profile=instrument_profile,
    )
    motor_safe_out = output_dir / f"{midi_path.stem}.motor-safe.mid"
    write_motor_safe_midi(
        analysis_notes=analysis.notes,
        compiled=compiled,
        tempo_map=tempo_map,
        output_path=motor_safe_out,
    )
    avg_active = 0.0
    if compiled.segments:
        avg_active = sum(1 for segment in compiled.segments for freq in segment.motor_freq_hz if freq > 0.0) / len(compiled.segments)
    return CandidateAnalysis(
        artifact_path=midi_path,
        note_count=analysis.note_count,
        max_polyphony=analysis.max_polyphony,
        transpose_semitones=analysis.transpose_semitones,
        clamped_note_count=analysis.clamped_note_count,
        allocation_dropped_note_count=compiled.dropped_note_count,
        allocation_stolen_note_count=compiled.stolen_note_count,
        allocation_truncated_note_count=compiled.truncated_note_count,
        arrangement_dropped_note_count=arrangement.dropped_note_count,
        dropped_melody_note_count=arrangement.dropped_melody_note_count,
        dropped_bass_note_count=arrangement.dropped_bass_note_count,
        motor_comfort_violation_count=arrangement.motor_comfort_violation_count,
        weighted_musical_loss=arrangement.weighted_musical_loss,
        event_group_count=len(compiled.event_groups),
        avg_active_motors=avg_active,
        duration_s=analysis.duration_s,
        exported_motor_safe_midi=motor_safe_out,
        metadata={
            "arrangement": {
                "preserved_note_count": arrangement.preserved_note_count,
                "preserved_melody_note_count": arrangement.preserved_melody_note_count,
                "preserved_bass_note_count": arrangement.preserved_bass_note_count,
            },
            "compile": {
                "tight_boundary_warning_count": compiled.tight_boundary_warning_count,
                "direction_flip_requested_count": compiled.direction_flip_requested_count,
                "direction_flip_applied_count": compiled.direction_flip_applied_count,
                "direction_flip_suppressed_count": compiled.direction_flip_suppressed_count,
                "direction_flip_cooldown_suppressed_count": compiled.direction_flip_cooldown_suppressed_count,
            },
        },
    )


def _transcribe_audio_candidate(
    audio_path: Path,
    *,
    cfg: HostConfig,
    output_dir: Path,
    instrument_profile: InstrumentProfile,
) -> CandidateAnalysis:
    transcribe_output_dir = ensure_dir(output_dir / "transcribed")
    result = convert_mp3_to_dual_midi(
        audio_path,
        output_dir=transcribe_output_dir,
        cache_dir=Path(output_dir) / "transcribe-cache",
        config=ConversionConfig(
            mode="music",
            max_polyphony=min(6, cfg.connected_motors),
        ),
    )
    return _analyze_candidate(
        result.motor_midi_path,
        cfg=replace(cfg, transpose_override=0, auto_transpose=False),
        instrument_profile=instrument_profile,
        output_dir=output_dir,
    )


def _export_ranked_candidates(
    result: LookupResult,
    *,
    output_dir: Path,
    download_best: bool,
    download_top: int,
) -> None:
    targets: list[tuple[int, RankedCandidate]] = []
    if download_best and result.recommended_index is not None and result.recommended_index < len(result.candidates):
        targets.append((result.recommended_index, result.candidates[result.recommended_index]))
    if download_top > 0:
        for idx, candidate in enumerate(result.candidates[:download_top]):
            targets.append((idx, candidate))

    seen: set[tuple[int, str | None]] = set()
    for idx, candidate in targets:
        artifact_path = str(candidate.artifact.local_path) if candidate.artifact and candidate.artifact.local_path else None
        dedupe_key = (idx, artifact_path)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        prefix = f"{idx + 1:02d}"
        slug = safe_slug(candidate.source_hit.title)
        if candidate.artifact and candidate.artifact.local_path and candidate.artifact.local_path.exists():
            export_name = f"{prefix}_{slug}{candidate.artifact.local_path.suffix.lower()}"
            shutil.copy2(candidate.artifact.local_path, output_dir / export_name)
        write_json(output_dir / f"{prefix}_{slug}.candidate.json", candidate.to_json_dict())
