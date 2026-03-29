from __future__ import annotations

from .types import CandidateNote


def _overlap_s(a: CandidateNote, b: CandidateNote) -> float:
    return max(0.0, min(a.end_s, b.end_s) - max(a.start_s, b.start_s))


def _can_merge(a: CandidateNote, b: CandidateNote, *, tolerance_s: float) -> bool:
    if abs(a.midi_note - b.midi_note) > 1:
        return False
    if abs(a.start_s - b.start_s) > tolerance_s:
        return False
    return _overlap_s(a, b) > 0.0 or abs(a.end_s - b.start_s) <= tolerance_s or abs(b.end_s - a.start_s) <= tolerance_s


def _merge_pair(a: CandidateNote, b: CandidateNote) -> CandidateNote:
    start_s = min(a.start_s, b.start_s)
    end_s = max(a.end_s, b.end_s)
    velocity = max(a.velocity, b.velocity)
    confidence = max(a.confidence, b.confidence)
    midi_note = int(round((a.midi_note * a.confidence + b.midi_note * b.confidence) / max(0.0001, a.confidence + b.confidence)))
    source = f"{a.source}+{b.source}" if a.source != b.source else a.source
    bends = a.bends if len(a.bends) >= len(b.bends) else b.bends
    return CandidateNote(
        start_s=start_s,
        end_s=end_s,
        midi_note=midi_note,
        velocity=velocity,
        confidence=confidence,
        source=source,
        bends=bends,
    )


def fuse_candidates(
    music_notes: list[CandidateNote],
    speech_notes: list[CandidateNote],
    *,
    tolerance_s: float = 0.03,
) -> list[CandidateNote]:
    all_notes = [note.clamped() for note in [*music_notes, *speech_notes] if note.end_s > note.start_s]
    all_notes.sort(key=lambda n: (n.start_s, n.end_s, n.midi_note, -n.confidence, n.source))
    if not all_notes:
        return []

    fused: list[CandidateNote] = []
    for note in all_notes:
        merged = False
        for idx in range(max(0, len(fused) - 16), len(fused)):
            candidate = fused[idx]
            if _can_merge(candidate, note, tolerance_s=tolerance_s):
                fused[idx] = _merge_pair(candidate, note)
                merged = True
                break
        if not merged:
            fused.append(note)

    fused.sort(key=lambda n: (n.start_s, n.end_s, n.midi_note, -n.confidence, n.source))
    return fused

