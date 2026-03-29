from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import mido

from ..artifacts import ensure_dir
from ..midi import TempoMap
from ..models import CompileReport, NoteEvent
from .cache import SongLookupCache
from .sources.base import UrlFetcher
from .types import CandidateArtifact, SourceHit

_SCORE_EXTENSIONS = {".musicxml", ".xml", ".mxl", ".mscz", ".mscx", ".abc", ".ly", ".gp3", ".gp4", ".gp5", ".gpx"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}


def acquire_source_hit(
    hit: SourceHit,
    *,
    cache: SongLookupCache,
    fetcher: UrlFetcher | None = None,
) -> CandidateArtifact:
    warnings: list[str] = []
    steps: list[str] = []
    fetch = fetcher or UrlFetcher()

    if hit.local_path:
        local_path = Path(hit.local_path).expanduser().resolve()
        return CandidateArtifact(
            source_hit=hit,
            local_path=local_path,
            artifact_kind=_artifact_kind_from_path(local_path),
            format_hint=hit.format_hint or local_path.suffix.lower().lstrip("."),
            conversion_steps=tuple(steps),
            warnings=tuple(warnings),
            acquired=local_path.exists(),
        )

    if hit.download_url:
        filename_hint = Path(urlparse(hit.download_url).path).name or f"{hit.title}.bin"
        out_path = cache.download_path(source_name=hit.source_name, url=hit.download_url, filename_hint=filename_hint)
        if not out_path.exists():
            ensure_dir(out_path.parent)
            try:
                out_path.write_bytes(fetch.fetch_bytes(hit.download_url))
                steps.append(f"download:{hit.download_url}")
            except Exception as exc:
                warnings.append(f"download failed: {exc}")
                out_path = None
        if out_path is not None:
            return CandidateArtifact(
                source_hit=hit,
                local_path=out_path,
                artifact_kind=_artifact_kind_from_path(out_path),
                format_hint=hit.format_hint or out_path.suffix.lower().lstrip("."),
                conversion_steps=tuple(steps),
                warnings=tuple(warnings),
                acquired=True,
            )

    warnings.append("source could not be acquired automatically")
    return CandidateArtifact(
        source_hit=hit,
        local_path=None,
        artifact_kind="unknown",
        format_hint=hit.format_hint,
        conversion_steps=tuple(steps),
        warnings=tuple(warnings),
        acquired=False,
    )


def ensure_midi_artifact(
    artifact: CandidateArtifact,
    *,
    cache: SongLookupCache,
) -> CandidateArtifact:
    if artifact.local_path is None:
        return artifact
    path = artifact.local_path
    if path.suffix.lower() in {".mid", ".midi"}:
        return artifact
    if path.suffix.lower() in _AUDIO_EXTENSIONS:
        return artifact
    if path.suffix.lower() not in _SCORE_EXTENSIONS:
        warnings = list(artifact.warnings)
        warnings.append(f"unsupported artifact format: {path.suffix}")
        return CandidateArtifact(
            source_hit=artifact.source_hit,
            local_path=artifact.local_path,
            artifact_kind=artifact.artifact_kind,
            format_hint=artifact.format_hint,
            conversion_steps=artifact.conversion_steps,
            warnings=tuple(warnings),
            acquired=artifact.acquired,
        )

    out_path = cache.converted_path(stem=path.stem)
    if out_path.exists():
        return CandidateArtifact(
            source_hit=artifact.source_hit,
            local_path=out_path,
            artifact_kind="midi",
            format_hint="mid",
            conversion_steps=artifact.conversion_steps + (f"cached-convert:{path.suffix.lower()}",),
            warnings=artifact.warnings,
            acquired=True,
        )

    conversion_steps = list(artifact.conversion_steps)
    warnings = list(artifact.warnings)
    converter_used = _convert_score_to_midi(path, out_path)
    if converter_used is None:
        warnings.append(f"no converter available for {path.suffix.lower()}")
        return CandidateArtifact(
            source_hit=artifact.source_hit,
            local_path=artifact.local_path,
            artifact_kind=artifact.artifact_kind,
            format_hint=artifact.format_hint,
            conversion_steps=artifact.conversion_steps,
            warnings=tuple(warnings),
            acquired=artifact.acquired,
        )
    conversion_steps.append(converter_used)
    return CandidateArtifact(
        source_hit=artifact.source_hit,
        local_path=out_path,
        artifact_kind="midi",
        format_hint="mid",
        conversion_steps=tuple(conversion_steps),
        warnings=tuple(warnings),
        acquired=True,
    )


def _artifact_kind_from_path(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".mid", ".midi"}:
        return "midi"
    if suffix in _SCORE_EXTENSIONS:
        return "score"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    return "unknown"


def _convert_score_to_midi(source_path: Path, out_path: Path) -> str | None:
    ensure_dir(out_path.parent)
    suffix = source_path.suffix.lower()
    muse_commands = ("musescore", "mscore", "MuseScore4", "MuseScore3")

    if suffix in {".musicxml", ".xml", ".mxl", ".mscz", ".mscx", ".gp3", ".gp4", ".gp5", ".gpx"}:
        for command in muse_commands:
            if shutil.which(command):
                subprocess.run([command, str(source_path), "-o", str(out_path)], check=True, capture_output=True, text=True)
                if out_path.exists():
                    return f"convert:{command}"
        return None

    if suffix == ".abc" and shutil.which("abc2midi"):
        subprocess.run(["abc2midi", str(source_path), "-o", str(out_path)], check=True, capture_output=True, text=True)
        if out_path.exists():
            return "convert:abc2midi"
        return None

    if suffix == ".ly" and shutil.which("lilypond"):
        out_dir = ensure_dir(out_path.parent / f"{out_path.stem}-lilypond")
        subprocess.run(
            ["lilypond", "--output", str(out_dir / out_path.stem), str(source_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        generated = out_dir / f"{out_path.stem}.midi"
        if generated.exists():
            shutil.copy2(generated, out_path)
            return "convert:lilypond"
        return None

    return None


def write_motor_safe_midi(
    *,
    analysis_notes: list[NoteEvent],
    compiled: CompileReport,
    tempo_map: TempoMap,
    output_path: Path,
) -> Path:
    events: list[tuple[int, int, mido.Message]] = []
    for point in tempo_map.points:
        events.append((point.tick, 0, mido.MetaMessage("set_tempo", tempo=point.tempo, time=0)))

    for idx, note in enumerate(analysis_notes):
        if idx >= len(compiled.assignments):
            continue
        if compiled.assignments[idx] < 0:
            continue
        effective_end = compiled.effective_end_s[idx] if idx < len(compiled.effective_end_s) else note.end_s
        if effective_end <= note.start_s:
            continue
        start_tick = _seconds_to_tick(note.start_s, tempo_map)
        end_tick = _seconds_to_tick(effective_end, tempo_map)
        end_tick = max(end_tick, start_tick + 1)
        velocity = max(1, min(127, note.velocity))
        pitch = max(0, min(127, note.transposed_note))
        events.append((start_tick, 2, mido.Message("note_on", note=pitch, velocity=velocity, time=0, channel=0)))
        events.append((end_tick, 1, mido.Message("note_off", note=pitch, velocity=0, time=0, channel=0)))

    events.sort(key=lambda item: (item[0], item[1]))
    track = mido.MidiTrack()
    last_tick = 0
    for abs_tick, _, message in events:
        delta = max(0, abs_tick - last_tick)
        last_tick = abs_tick
        track.append(message.copy(time=delta))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid = mido.MidiFile(ticks_per_beat=tempo_map.ticks_per_beat)
    mid.tracks.append(track)
    ensure_dir(output_path.parent)
    mid.save(output_path)
    return output_path


def _seconds_to_tick(seconds: float, tempo_map: TempoMap) -> int:
    points = tempo_map.points
    point = points[0]
    for candidate in points:
        if candidate.seconds <= seconds:
            point = candidate
        else:
            break
    delta_seconds = max(0.0, seconds - point.seconds)
    return point.tick + round(mido.second2tick(delta_seconds, tempo_map.ticks_per_beat, point.tempo))
