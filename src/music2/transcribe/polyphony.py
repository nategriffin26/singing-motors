from __future__ import annotations

from dataclasses import dataclass

from .types import CandidateNote, PitchBendPoint


@dataclass(frozen=True)
class PolyphonyStats:
    input_note_count: int
    output_note_count: int
    dropped_note_count: int
    max_polyphony_before: int
    max_polyphony_after: int


def compute_max_polyphony(notes: list[CandidateNote]) -> int:
    edges: list[tuple[float, int]] = []
    for note in notes:
        if note.end_s <= note.start_s:
            continue
        edges.append((note.start_s, 1))
        edges.append((note.end_s, -1))
    edges.sort(key=lambda item: (item[0], item[1]))
    active = 0
    peak = 0
    for _, delta in edges:
        active += delta
        if active > peak:
            peak = active
    return peak


def _base_score(note: CandidateNote) -> float:
    duration_score = min(note.duration_s, 5.0) / 5.0
    velocity_score = note.velocity / 127.0
    return 0.6 * note.confidence + 0.2 * duration_score + 0.2 * velocity_score


def _trim_bends(bends: tuple[PitchBendPoint, ...], start_s: float, end_s: float) -> tuple[PitchBendPoint, ...]:
    trimmed = [bend for bend in bends if start_s <= bend.time_s <= end_s]
    if not trimmed:
        return ()
    if trimmed[0].time_s > start_s:
        trimmed.insert(0, PitchBendPoint(time_s=start_s, semitones=trimmed[0].semitones, confidence=trimmed[0].confidence))
    if trimmed[-1].time_s < end_s:
        trimmed.append(PitchBendPoint(time_s=end_s, semitones=trimmed[-1].semitones, confidence=trimmed[-1].confidence))
    return tuple(trimmed)


def enforce_polyphony_cap(
    notes: list[CandidateNote],
    *,
    cap: int = 6,
    continuity_bonus: float = 0.12,
) -> tuple[list[CandidateNote], PolyphonyStats]:
    if cap < 1 or cap > 6:
        raise ValueError("cap must be in range [1, 6]")
    if not notes:
        stats = PolyphonyStats(
            input_note_count=0,
            output_note_count=0,
            dropped_note_count=0,
            max_polyphony_before=0,
            max_polyphony_after=0,
        )
        return [], stats

    normalized = [note.clamped() for note in notes if note.end_s > note.start_s]
    normalized.sort(key=lambda n: (n.start_s, n.end_s, n.midi_note, -n.confidence, n.source))
    max_before = compute_max_polyphony(normalized)
    if max_before <= cap:
        stats = PolyphonyStats(
            input_note_count=len(normalized),
            output_note_count=len(normalized),
            dropped_note_count=0,
            max_polyphony_before=max_before,
            max_polyphony_after=max_before,
        )
        return normalized, stats

    boundaries = sorted({note.start_s for note in normalized} | {note.end_s for note in normalized})
    if len(boundaries) < 2:
        stats = PolyphonyStats(
            input_note_count=len(normalized),
            output_note_count=len(normalized),
            dropped_note_count=0,
            max_polyphony_before=max_before,
            max_polyphony_after=max_before,
        )
        return normalized, stats

    active_spans: dict[int, list[tuple[float, float]]] = {idx: [] for idx in range(len(normalized))}
    previous_selected: set[int] = set()
    current_open: dict[int, float] = {}

    for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
        if right <= left:
            continue
        active = [
            idx
            for idx, note in enumerate(normalized)
            if note.start_s < right and note.end_s > left
        ]
        if not active:
            for idx, start_s in list(current_open.items()):
                if left > start_s:
                    active_spans[idx].append((start_s, left))
            previous_selected = set()
            current_open.clear()
            continue

        ranked = sorted(
            active,
            key=lambda idx: (
                -(_base_score(normalized[idx]) + (continuity_bonus if idx in previous_selected else 0.0)),
                normalized[idx].start_s,
                normalized[idx].midi_note,
                idx,
            ),
        )
        selected = set(ranked[:cap])

        for idx in list(current_open):
            if idx not in selected:
                start_s = current_open.pop(idx)
                if left > start_s:
                    active_spans[idx].append((start_s, left))

        for idx in selected:
            if idx not in current_open:
                current_open[idx] = left

        previous_selected = selected

    end_boundary = boundaries[-1]
    for idx, start_s in current_open.items():
        if end_boundary > start_s:
            active_spans[idx].append((start_s, end_boundary))

    rebuilt: list[CandidateNote] = []
    for idx, spans in active_spans.items():
        source = normalized[idx]
        for span_start, span_end in spans:
            clipped_start = max(source.start_s, span_start)
            clipped_end = min(source.end_s, span_end)
            if clipped_end <= clipped_start:
                continue
            rebuilt.append(
                CandidateNote(
                    start_s=clipped_start,
                    end_s=clipped_end,
                    midi_note=source.midi_note,
                    velocity=source.velocity,
                    confidence=source.confidence,
                    source=source.source,
                    bends=_trim_bends(source.bends, clipped_start, clipped_end),
                )
            )

    rebuilt.sort(key=lambda n: (n.start_s, n.end_s, n.midi_note, -n.confidence, n.source))
    max_after = compute_max_polyphony(rebuilt)
    dropped = max(0, len(normalized) - len(rebuilt))
    stats = PolyphonyStats(
        input_note_count=len(normalized),
        output_note_count=len(rebuilt),
        dropped_note_count=dropped,
        max_polyphony_before=max_before,
        max_polyphony_after=max_after,
    )
    return rebuilt, stats
